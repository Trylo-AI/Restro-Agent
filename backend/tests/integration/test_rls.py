"""RLS as the console sees it: sign in through Supabase Auth, read through PostgREST.

Runs against the stack named by RLS_SUPABASE_URL / RLS_PUBLISHABLE_KEY / RLS_PASSWORD (the local stack in CI;
the cloud dev project when run by hand). Needs the seed (supabase/seed/generate.py). Only reads and
write attempts that RLS must refuse, so it never changes data.
"""

import os
from functools import cache

import httpx
import pytest

pytestmark = pytest.mark.integration

URL = os.environ.get("RLS_SUPABASE_URL", "").rstrip("/")
KEY = os.environ.get("RLS_PUBLISHABLE_KEY", "")
PASSWORD = os.environ.get("RLS_PASSWORD", "")

if not (URL and KEY and PASSWORD):
    pytest.skip("RLS_SUPABASE_URL, RLS_PUBLISHABLE_KEY and RLS_PASSWORD not set", allow_module_level=True)

MEMBER_TABLES = [
    "tables", "menu_items", "menu_item_allergens", "customers", "customer_preferences", "customer_notes",
    "feedback", "conversations", "inbound_messages", "wa_messages", "table_sessions", "orders", "order_items",
    "escalations", "redemptions", "requests",
]
HIDDEN_FROM_SHIFT = ["consents", "templates", "campaigns", "campaign_recipients", "llm_calls", "audit_log",
                     "pending_work", "order_events", "data_deletion_requests", "cafe_counters"]


@cache
def token(email: str) -> str:
    r = httpx.post(f"{URL}/auth/v1/token", params={"grant_type": "password"},
                   headers={"apikey": KEY}, json={"email": email, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    return str(r.json()["access_token"])


def rest(method: str, path: str, jwt: str | None, **kw: object) -> httpx.Response:
    headers = {"apikey": KEY}
    if jwt:
        headers["Authorization"] = f"Bearer {jwt}"
    return httpx.request(method, f"{URL}/rest/v1/{path}", headers=headers, timeout=30, **kw)  # type: ignore[arg-type]


def cafe_ids(jwt: str) -> set[str]:
    r = rest("GET", "cafes?select=id", jwt)
    r.raise_for_status()
    return {row["id"] for row in r.json()}


def test_each_member_sees_only_their_cafe() -> None:
    a = cafe_ids(token("shift@restro.test"))
    b = cafe_ids(token("b-shift@restro.test"))
    assert len(a) == 1 and len(b) == 1 and a.isdisjoint(b)


@pytest.mark.parametrize("table", MEMBER_TABLES)
def test_member_tables_never_show_another_cafe(table: str) -> None:
    for email in ("shift@restro.test", "b-shift@restro.test"):
        jwt = token(email)
        mine = cafe_ids(jwt)
        r = rest("GET", f"{table}?select=cafe_id&limit=1000", jwt)
        assert r.status_code == 200, r.text
        assert {row["cafe_id"] for row in r.json()} <= mine


def test_shift_manager_reads_the_customer_profile() -> None:
    jwt = token("shift@restro.test")
    for path in ("customer_preferences?select=kind,value", "customer_notes?select=ai_status", "feedback?select=rating",
                 "v_customer_card?select=first_name,masked_phone,visits"):
        r = rest("GET", path, jwt)
        assert r.status_code == 200, r.text
        assert r.json(), f"{path} returned nothing for the seeded café"
    card = rest("GET", "v_customer_card?select=masked_phone", jwt).json()
    assert all("••" in row["masked_phone"] for row in card)


@pytest.mark.parametrize("table", HIDDEN_FROM_SHIFT)
def test_shift_manager_cannot_read_owner_platform_or_backend_tables(table: str) -> None:
    r = rest("GET", f"{table}?select=*&limit=5", token("shift@restro.test"))
    assert r.status_code in (200, 401, 403, 404), r.text
    if r.status_code == 200:
        assert r.json() == []


def test_low_rating_requests_hidden_below_manager() -> None:
    r = rest("GET", "requests?select=kind&kind=eq.low_rating", token("shift@restro.test"))
    assert r.status_code == 200 and r.json() == []


def test_notifications_only_own_rows_and_manager_plus() -> None:
    assert rest("GET", "notifications?select=id", token("shift@restro.test")).json() == []
    r = rest("GET", "notifications?select=user_id", token("manager@restro.test"))
    assert r.status_code == 200


def test_anon_sees_nothing() -> None:
    for table in ("cafes", "orders", "customers", "menu_items"):
        r = rest("GET", f"{table}?select=id&limit=1", None)
        assert r.status_code in (401, 403) or r.json() == []


@pytest.mark.parametrize("method,path,body", [
    ("POST", "orders", {"number": 1}),
    ("PATCH", "orders?status=eq.NEW", {"status": "ACCEPTED"}),
    ("DELETE", "menu_items?name=eq.Espresso", None),
    ("PATCH", "menu_items?name=eq.Espresso", {"available": False}),
    ("POST", "customer_notes", {"text": "x"}),
])
def test_client_writes_are_refused(method: str, path: str, body: dict[str, object] | None) -> None:
    r = rest(method, path, token("owner@restro.test"), json=body)
    assert r.status_code in (401, 403, 404, 405), f"{method} {path} -> {r.status_code} {r.text[:200]}"


def test_staff_names_visible_to_every_member_of_their_cafe_only() -> None:
    names = rest("GET", "v_staff_names?select=cafe_id,display_name,role", token("shift@restro.test")).json()
    mine = cafe_ids(token("shift@restro.test"))
    assert {"shift_manager", "manager", "owner"} <= {n["role"] for n in names}  # teammates, not just my row
    assert {n["cafe_id"] for n in names} <= mine
    anon = rest("GET", "v_staff_names?select=user_id", None)
    assert anon.status_code != 200 or anon.json() == []


@pytest.mark.parametrize(("view", "min_role"), [
    ("v_daily_revenue", "owner"), ("v_top_items", "owner"), ("v_customer_segments", "owner"),
    ("v_customer_profile", "owner"), ("v_campaign_funnel", "owner"),
    ("v_prep_times", "manager"), ("v_service_report", "manager"), ("v_feedback_summary", "manager"),
])
def test_analytics_views_follow_their_role(view: str, min_role: str) -> None:
    below = "shift@restro.test" if min_role == "manager" else "manager@restro.test"
    r = rest("GET", f"{view}?select=cafe_id&limit=5", token(below))
    assert r.status_code == 200 and r.json() == [], f"{view} visible to {below}"
    r = rest("GET", f"{view}?select=cafe_id&limit=1000", token("owner@restro.test"))
    assert r.status_code == 200, r.text
    assert {row["cafe_id"] for row in r.json()} <= cafe_ids(token("owner@restro.test"))


def test_owner_sees_masked_profiles_and_revenue() -> None:
    jwt = token("owner@restro.test")
    profiles = rest("GET", "v_customer_profile?select=masked_phone,visits,spend_paise,consent_status", jwt).json()
    assert profiles and all("••" in p["masked_phone"] for p in profiles)
    assert {p["consent_status"] for p in profiles} <= {"opted_in", "opted_out", "not_asked"}
    revenue = rest("GET", "v_daily_revenue?select=revenue_paise,orders,customers", jwt).json()
    assert revenue and all(r["revenue_paise"] > 0 and r["orders"] >= r["customers"] for r in revenue)
