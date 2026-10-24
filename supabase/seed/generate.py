"""Seed generator: synthetic data only (docs/ordering-slice.md#seed-data). Safe to run again.

Run from the repo root with the backend venv:
    backend\\.venv\\Scripts\\python.exe supabase\\seed\\generate.py            # cloud dev project from backend/.env
    backend\\.venv\\Scripts\\python.exe supabase\\seed\\generate.py --live     # + open orders in every status
    backend\\.venv\\Scripts\\python.exe supabase\\seed\\generate.py --dev-sql  # + apply supabase/dev/*.sql first

Staff logins are created through the Supabase Auth Admin API with confirmed emails and SEED_STAFF_PASSWORD.
Never put real names, numbers or menus here.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402

NOW = datetime.now(UTC)
SIM_PHONE_NUMBER_ID = "100000000000001"
CAFE_B_PHONE_NUMBER_ID = "100000000000002"


@dataclass
class Item:
    name: str
    category: str
    rupees: int
    prep: int
    veg: bool
    spice: int = 0
    description: str = ""
    allergens: dict[str, str] = field(default_factory=dict)
    available: bool = True


C, F, U = "contains", "free_from", "unknown"
MENU: list[Item] = [
    Item("Espresso", "Hot coffee", 120, 3, True, description="Double shot, house blend"),
    Item("Cappuccino", "Hot coffee", 180, 4, True, allergens={"milk": C}),
    Item("Café Latte", "Hot coffee", 190, 4, True, allergens={"milk": C}),
    Item("Flat White", "Hot coffee", 200, 4, True, allergens={"milk": C}),
    Item("Filter Coffee", "Hot coffee", 110, 4, True, description="South Indian, chicory blend", allergens={"milk": C}),
    Item("Mocha", "Hot coffee", 230, 5, True, allergens={"milk": C, "soy": U}),
    Item("Cold Coffee", "Cold coffee", 190, 4, True, description="Blended with ice cream", allergens={"milk": C, "eggs": F}),
    Item("Cold Brew", "Cold coffee", 210, 2, True, description="Steeped 18 hours", allergens={"milk": F}),
    Item("Cold Brew Tonic", "Cold coffee", 240, 3, True, description="Cold brew over tonic and orange", allergens={"milk": F, "sulphites": U}),
    Item("Iced Hazelnut Latte", "Cold coffee", 250, 4, True, allergens={"milk": C, "tree_nuts": C}),
    Item("Vietnamese Iced Coffee", "Cold coffee", 230, 4, True, allergens={"milk": C}, available=False),
    Item("Masala Chai", "Tea and more", 90, 5, True, spice=1, allergens={"milk": C}),
    Item("Lemon Iced Tea", "Tea and more", 140, 3, True, allergens={"milk": F, "gluten": F}),
    Item("Hot Chocolate", "Tea and more", 220, 5, True, allergens={"milk": C, "soy": U}),
    Item("Mango Smoothie", "Tea and more", 210, 4, True, allergens={"milk": C}),
    Item("Masala Omelette", "Breakfast", 180, 8, False, spice=1, allergens={"eggs": C, "milk": F}),
    Item("Poha", "Breakfast", 140, 8, True, spice=1, description="With peanuts and curry leaves", allergens={"peanuts": C, "gluten": F}),
    Item("Upma", "Breakfast", 140, 8, True, spice=1, allergens={"gluten": C, "mustard": U}),
    Item("Granola Bowl", "Breakfast", 260, 4, True, description="Yoghurt, honey, seasonal fruit", allergens={"milk": C, "tree_nuts": C, "gluten": C}),
    Item("Avocado Toast", "Toasts and sandwiches", 320, 8, True, spice=1, description="Sourdough, chilli flakes, lime", allergens={"gluten": C, "sesame": U, "milk": F}),
    Item("Chilli Cheese Toast", "Toasts and sandwiches", 220, 7, True, spice=2, allergens={"gluten": C, "milk": C}),
    Item("Bombay Sandwich", "Toasts and sandwiches", 180, 7, True, spice=1, allergens={"gluten": C, "milk": C}),
    Item("Paneer Tikka Sandwich", "Toasts and sandwiches", 260, 10, True, spice=2, allergens={"gluten": C, "milk": C}),
    Item("Chicken Club Sandwich", "Toasts and sandwiches", 320, 10, False, allergens={"gluten": C, "eggs": C, "mustard": C}),
    Item("Mushroom Risotto", "Mains", 380, 18, True, allergens={"milk": C, "gluten": F}),
    Item("Pesto Pasta", "Mains", 360, 15, True, allergens={"gluten": C, "milk": C, "tree_nuts": C}, available=False),
    Item("Butter Chicken Bowl", "Mains", 420, 18, False, spice=2, allergens={"milk": C, "tree_nuts": U}),
    # Popular dish with no allergen data at all: every allergen question about it reaches a person.
    Item("Thai Green Curry Bowl", "Mains", 380, 18, True, spice=3, description="Coconut curry, jasmine rice"),
    Item("Chocolate Brownie", "Desserts", 180, 3, True, allergens={"gluten": C, "milk": C, "eggs": C, "tree_nuts": U}),
    Item("Tiramisu", "Desserts", 280, 3, True, allergens={"gluten": C, "milk": C, "eggs": C}),
    Item("Banoffee Jar", "Desserts", 240, 3, True, allergens={"gluten": C, "milk": C}),
]

STAFF = [
    ("shift@restro.test", "Ravi", "shift_manager", "919999000801"),
    ("manager@restro.test", "Kiran", "manager", "919999000802"),
    ("owner@restro.test", "Neha", "owner", "919999000803"),
]


@dataclass
class Customer:
    wa_id: str
    name: str
    orders: list[tuple[int, list[tuple[str, int, str | None]]]]  # (days ago, [(item, qty, note)])
    prefs: list[tuple[str, str]] = field(default_factory=list)
    note: tuple[str, str, list[dict[str, str | None]]] | None = None  # (text, summary, ops)
    ratings: dict[int, tuple[int, str | None]] = field(default_factory=dict)  # order index → (rating, text)
    complaint: bool = False


CUSTOMERS = [
    Customer(
        "919999000001", "Riya Sharma",
        [(52, [("Cold Brew Tonic", 1, None), ("Avocado Toast", 1, None)]),
         (40, [("Cold Brew Tonic", 1, None)]),
         (31, [("Cold Brew Tonic", 1, None), ("Banoffee Jar", 1, None)]),
         (24, [("Avocado Toast", 1, "no chilli"), ("Cold Brew", 1, None)]),
         (19, [("Cold Brew Tonic", 2, None)]),
         (12, [("Cold Brew Tonic", 1, None), ("Avocado Toast", 1, None)])],
        prefs=[("allergy", "peanuts"), ("dislike", "onion")],
        note=("Riya is allergic to peanuts and doesn't like onion in anything.", "Allergy + peanuts · Dislikes + onion",
              [{"op": "add", "kind": "allergy", "value": "peanuts", "previous": None},
               {"op": "add", "kind": "dislike", "value": "onion", "previous": None}]),
        ratings={4: (5, "Lovely as always")},
    ),
    Customer(
        "919999000002", "Arjun Mehta",
        [(45, [("Cold Coffee", 2, None), ("Poha", 1, None)]),
         (30, [("Lemon Iced Tea", 1, None), ("Thai Green Curry Bowl", 1, None)]),
         (14, [("Cold Brew", 1, None)]),
         (5, [("Thai Green Curry Bowl", 1, "extra rice")])],
        prefs=[("diet", "vegan"), ("dislike", "mushrooms")],
        note=("He's vegan now and hates mushrooms.", "Diet → vegan · Dislikes + mushrooms",
              [{"op": "set", "kind": "diet", "value": "vegan", "previous": None},
               {"op": "add", "kind": "dislike", "value": "mushrooms", "previous": None}]),
        ratings={3: (4, None)},
    ),
    Customer(
        "919999000003", "Meera Iyer",
        [(58, [("Café Latte", 1, "oat milk")]), (50, [("Café Latte", 1, "oat milk")]),
         (41, [("Flat White", 1, "oat milk"), ("Granola Bowl", 1, None)]), (33, [("Café Latte", 1, "oat milk")]),
         (26, [("Cold Brew Tonic", 1, None)]), (17, [("Café Latte", 1, "oat milk"), ("Tiramisu", 1, None)]),
         (9, [("Flat White", 1, "oat milk")]), (2, [("Café Latte", 2, "oat milk")])],
        prefs=[("milk", "oat"), ("sugar", "less"), ("like", "Cold Brew Tonic")],
        ratings={6: (5, None), 7: (4, "Could be a bit hotter")},
    ),
    Customer(
        "919999000004", "Dev Kapoor",
        [(70, [("Butter Chicken Bowl", 1, None)]), (55, [("Chicken Club Sandwich", 1, None), ("Cold Coffee", 1, None)]),
         (40, [("Butter Chicken Bowl", 1, "less spicy"), ("Masala Chai", 1, None)])],
        ratings={2: (2, "Food took 25 minutes and came cold.")},
        complaint=True,
    ),
    Customer(
        "919999000005", "Sana Khan",
        [(20, [("Masala Chai", 2, None), ("Bombay Sandwich", 1, None)]), (6, [("Hot Chocolate", 1, None)])],
    ),
]


async def auth_user(client: httpx.AsyncClient, base: str, key: str, email: str, password: str) -> str:
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    r = await client.post(f"{base}/auth/v1/admin/users", headers=headers,
                          json={"email": email, "password": password, "email_confirm": True})
    if r.status_code in (200, 201):
        return str(r.json()["id"])
    page = 1
    while True:
        lr = await client.get(f"{base}/auth/v1/admin/users", headers=headers, params={"page": page, "per_page": 200})
        lr.raise_for_status()
        users = lr.json().get("users", [])
        for u in users:
            if u.get("email") == email:
                await client.put(f"{base}/auth/v1/admin/users/{u['id']}", headers=headers,
                                 json={"password": password, "email_confirm": True})
                return str(u["id"])
        if len(users) < 200:
            raise RuntimeError(f"could not create or find auth user {email}: {r.status_code} {r.text[:200]}")
        page += 1


async def upsert_cafe(conn: asyncpg.Connection, name: str, phone_number_id: str, display: str, alert: str) -> str:
    hours = {d: [["08:00", "23:00"]] for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}
    row = await conn.fetchrow(
        """
        insert into cafes (name, phone_number_id, display_phone_number, status, opening_hours, cutoff_minutes,
                           settings, token_ref, health, checklist)
        values ($1, $2, $3, 'live', $4::jsonb, 30,
                jsonb_build_object('paused', false, 'tone', 'warm', 'feedback_delay_minutes', 30, 'alert_phone', $5::text),
                'env:WA_TOKEN_DEMO', '{"token": "valid", "quality": "High"}'::jsonb, '{}'::jsonb)
        on conflict (phone_number_id) do update set name = excluded.name
        returning id
        """,
        name, phone_number_id, display, _json(hours), alert,
    )
    cafe_id = str(row["id"])
    await conn.execute("insert into cafe_counters (cafe_id) values ($1) on conflict do nothing", cafe_id)
    return cafe_id


def _json(v: object) -> str:
    import json
    return json.dumps(v)


async def seed_tables(conn: asyncpg.Connection, cafe_id: str, n: int) -> dict[str, str]:
    ids = {}
    for i in range(1, n + 1):
        label = f"T{i}"
        row = await conn.fetchrow(
            """insert into tables (cafe_id, label, area, seats, qr_text) values ($1, $2, $3, $4, $5)
               on conflict (cafe_id, label) do update set qr_text = excluded.qr_text returning id""",
            cafe_id, label, "Patio" if i > 8 else "Inside", 2 if i % 3 else 4, f"Hi, I'm at Table {i}",
        )
        ids[label] = str(row["id"])
    return ids


async def seed_menu(conn: asyncpg.Connection, cafe_id: str, menu: list[Item]) -> dict[str, tuple[str, int]]:
    out = {}
    for it in menu:
        existing = await conn.fetchrow("select id, price_paise from menu_items where cafe_id = $1 and lower(name) = lower($2)",
                                       cafe_id, it.name)
        if existing:
            item_id = str(existing["id"])
        else:
            version = await conn.fetchval("select next_menu_version($1)", cafe_id)
            item_id = str(await conn.fetchval(
                """insert into menu_items (cafe_id, name, description, category, price_paise, prep_minutes, veg, spice,
                                           available, version)
                   values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) returning id""",
                cafe_id, it.name, it.description or None, it.category, it.rupees * 100, it.prep, it.veg, it.spice,
                it.available, version,
            ))
        for allergen, state in it.allergens.items():
            await conn.execute(
                """insert into menu_item_allergens (cafe_id, item_id, allergen, state) values ($1, $2, $3, $4)
                   on conflict (item_id, allergen) do update set state = excluded.state""",
                cafe_id, item_id, allergen, state,
            )
        out[it.name] = (item_id, it.rupees * 100)
    return out


async def staff_membership(conn: asyncpg.Connection, user_id: str, cafe_id: str, role: str, name: str, phone: str) -> None:
    await conn.execute(
        """insert into memberships (user_id, cafe_id, role, active, display_name, alert_phone)
           values ($1, $2, $3, true, $4, $5)
           on conflict (user_id, cafe_id) do update set role = excluded.role, active = true,
             display_name = excluded.display_name, alert_phone = excluded.alert_phone""",
        user_id, cafe_id, role, name, phone,
    )


async def create_order(conn: asyncpg.Connection, cafe_id: str, customer_id: str | None, table_id: str, key: str,
                       lines: list[tuple[str, int, str | None]], menu: dict[str, tuple[str, int]], status: str,
                       created: datetime, *, allergy: str | None = None, flags: list[str] | None = None,
                       delay: int = 0, open_session: bool = False) -> str | None:
    if await conn.fetchval("select 1 from orders where idempotency_key = $1", key):
        return None
    if open_session:
        session_id = await conn.fetchval("select id from table_sessions where table_id = $1 and closed_at is null", table_id)
        if not session_id:
            session_id = await conn.fetchval(
                "insert into table_sessions (cafe_id, table_id, opened_at) values ($1, $2, $3) returning id",
                cafe_id, table_id, created)
    else:
        session_id = await conn.fetchval(
            "insert into table_sessions (cafe_id, table_id, opened_at, closed_at) values ($1, $2, $3, $4) returning id",
            cafe_id, table_id, created, created + timedelta(minutes=50))
    total = sum(menu[name][1] * qty for name, qty, _ in lines)
    prep = max(next(i.prep for i in MENU if i.name == name) for name, _, _ in lines)
    number = await conn.fetchval("select next_order_number($1)", cafe_id)
    stamps = {"ACCEPTED": 2, "READY": 12, "SERVED": 18}
    order_id = await conn.fetchval(
        """insert into orders (cafe_id, customer_id, table_session_id, number, status, flags, allergy_note,
                               expected_minutes, delay_minutes, total_paise, source, idempotency_key, version,
                               created_at, accepted_at, ready_at, served_at)
           values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, 'qr', $11, $12, $13, $14, $15, $16) returning id""",
        cafe_id, customer_id, session_id, number, status, flags or [], allergy, prep, delay, total, key,
        1 + ["NEW", "ACCEPTED", "READY", "SERVED"].index(status),
        created,
        created + timedelta(minutes=stamps["ACCEPTED"]) if status in ("ACCEPTED", "READY", "SERVED") else None,
        created + timedelta(minutes=stamps["READY"]) if status in ("READY", "SERVED") else None,
        created + timedelta(minutes=stamps["SERVED"]) if status == "SERVED" else None,
    )
    for name, qty, note in lines:
        item_id, price = menu[name]
        await conn.execute(
            """insert into order_items (cafe_id, order_id, item_id, name_snapshot, price_snapshot_paise, qty, note)
               values ($1, $2, $3, $4, $5, $6, $7)""", cafe_id, order_id, item_id, name, price, qty, note)
    prev = None
    for st in ["NEW", "ACCEPTED", "READY", "SERVED"][: ["NEW", "ACCEPTED", "READY", "SERVED"].index(status) + 1]:
        await conn.execute(
            """insert into order_events (cafe_id, order_id, from_status, to_status, actor_type, created_at)
               values ($1, $2, $3, $4, $5, $6)""",
            cafe_id, order_id, prev, st, "customer" if st == "NEW" else "staff", created)
        prev = st
    return str(order_id)


async def seed_customers(conn: asyncpg.Connection, cafe_id: str, tables: dict[str, str], menu: dict[str, tuple[str, int]],
                         author_id: str) -> None:
    for ci, c in enumerate(CUSTOMERS):
        first_order = NOW - timedelta(days=max(d for d, _ in c.orders))
        last_order = NOW - timedelta(days=min(d for d, _ in c.orders))
        customer_id = str(await conn.fetchval(
            """insert into customers (cafe_id, wa_id, name, first_seen_at, last_inbound_at, last_visit_at)
               values ($1, $2, $3, $4, $5, $5)
               on conflict (cafe_id, wa_id) do update set name = excluded.name, last_visit_at = excluded.last_visit_at
               returning id""",
            cafe_id, c.wa_id, c.name, first_order, last_order))
        await conn.execute(
            "insert into conversations (cafe_id, customer_id) values ($1, $2) on conflict (cafe_id, customer_id) do nothing",
            cafe_id, customer_id)
        order_ids: list[str | None] = []
        for oi, (days, lines) in enumerate(c.orders):
            created = NOW - timedelta(days=days, hours=ci)
            table = f"T{(oi + ci) % 12 + 1}"
            order_ids.append(await create_order(conn, cafe_id, customer_id, tables[table], f"seed:{c.wa_id}:{oi}",
                                                lines, menu, "SERVED", created))
        for idx, (rating, text) in c.ratings.items():
            oid = order_ids[idx] or await conn.fetchval("select id from orders where idempotency_key = $1",
                                                        f"seed:{c.wa_id}:{idx}")
            await conn.execute(
                "insert into feedback (cafe_id, order_id, rating, text) values ($1, $2, $3, $4) on conflict (order_id) do nothing",
                cafe_id, oid, rating, text)
        note_id = None
        if c.note:
            text, summary, ops = c.note
            note_id = await conn.fetchval("select id from customer_notes where customer_id = $1 and text = $2", customer_id, text)
            if not note_id:
                note_id = await conn.fetchval(
                    """insert into customer_notes (cafe_id, customer_id, author_id, text, ai_status, ai_ops, ai_summary, created_at)
                       values ($1, $2, $3, $4, 'applied', $5::jsonb, $6, $7) returning id""",
                    cafe_id, customer_id, author_id, text, _json(ops), summary, last_order + timedelta(minutes=30))
        for kind, value in c.prefs:
            await conn.execute(
                """insert into customer_preferences (cafe_id, customer_id, kind, value, source, note_id)
                   values ($1, $2, $3, $4, $5, $6) on conflict (customer_id, kind, value) do nothing""",
                cafe_id, customer_id, kind, value, "staff_note" if note_id else "chat", note_id)
        if c.complaint:
            conv_id = await conn.fetchval("select id from conversations where customer_id = $1", customer_id)
            if not await conn.fetchval("select 1 from requests where conversation_id = $1 and reason_tag = 'complaint'", conv_id):
                await conn.execute(
                    """insert into requests (cafe_id, conversation_id, order_id, kind, status, reason_tag, created_at, resolved_at)
                       values ($1, $2, $3, 'help', 'resolved', 'complaint', $4::timestamptz,
                               $4::timestamptz + interval '6 minutes')""",
                    cafe_id, conv_id, order_ids[-1] or await conn.fetchval(
                        "select id from orders where idempotency_key = $1", f"seed:{c.wa_id}:{len(c.orders) - 1}"),
                    NOW - timedelta(days=40))


async def seed_live(conn: asyncpg.Connection, cafe_id: str, tables: dict[str, str], menu: dict[str, tuple[str, int]]) -> None:
    sana = await conn.fetchval("select id from customers where cafe_id = $1 and wa_id = '919999000005'", cafe_id)
    riya = await conn.fetchval("select id from customers where cafe_id = $1 and wa_id = '919999000001'", cafe_id)
    meera = await conn.fetchval("select id from customers where cafe_id = $1 and wa_id = '919999000003'", cafe_id)
    await create_order(conn, cafe_id, riya, tables["T7"], "seed-live:1", [("Avocado Toast", 1, None), ("Cold Brew Tonic", 1, None)],
                       menu, "NEW", NOW - timedelta(seconds=40), allergy="peanuts", open_session=True)
    await create_order(conn, cafe_id, sana, tables["T2"], "seed-live:2", [("Masala Chai", 2, None)],
                       menu, "NEW", NOW - timedelta(seconds=90), open_session=True)
    await create_order(conn, cafe_id, meera, tables["T3"], "seed-live:3", [("Café Latte", 1, "oat milk"), ("Tiramisu", 1, None)],
                       menu, "ACCEPTED", NOW - timedelta(minutes=7), flags=["delayed"], delay=5, open_session=True)
    await create_order(conn, cafe_id, None, tables["T9"], "seed-live:4", [("Mushroom Risotto", 1, None)],
                       menu, "READY", NOW - timedelta(minutes=20), open_session=True)


async def apply_dev_sql(conn: asyncpg.Connection) -> None:
    for path in sorted((ROOT / "supabase" / "dev").glob("*.sql")):
        await conn.execute(path.read_text(encoding="utf-8"))
        print(f"applied {path.name}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="add open orders in every status")
    parser.add_argument("--dev-sql", action="store_true", help="apply supabase/dev/*.sql first (local dev only)")
    args = parser.parse_args()

    s = get_settings()
    password = os.environ.get("SEED_STAFF_PASSWORD") or _env_value("SEED_STAFF_PASSWORD")
    if not password:
        raise SystemExit("SEED_STAFF_PASSWORD is missing in backend/.env")
    dsn = (s.database_url_session or s.database_url).get_secret_value()
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        if args.dev_sql:
            await apply_dev_sql(conn)
        async with httpx.AsyncClient(timeout=30) as client:
            key = s.supabase_secret_key.get_secret_value()
            users = {email: await auth_user(client, s.supabase_url.rstrip("/"), key, email, password) for email, *_ in STAFF}
            b_user = await auth_user(client, s.supabase_url.rstrip("/"), key, "b-shift@restro.test", password)

        async with conn.transaction():
            cafe_id = await upsert_cafe(conn, "Kaapi House", SIM_PHONE_NUMBER_ID, "+91 90000 00001", "919999000900")
            for email, name, role, phone in STAFF:
                await staff_membership(conn, users[email], cafe_id, role, name, phone)
            tables = await seed_tables(conn, cafe_id, 12)
            menu = await seed_menu(conn, cafe_id, MENU)
            for tname in ("order_update", "new_order_alert"):
                await conn.execute(
                    """insert into templates (cafe_id, name, category, language, status, quality)
                       values ($1, $2, 'utility', 'en', 'approved', 'High')
                       on conflict (cafe_id, name, language) do update set status = 'approved'""", cafe_id, tname)
            await seed_customers(conn, cafe_id, tables, menu, users["shift@restro.test"])
            if args.live:
                await seed_live(conn, cafe_id, tables, menu)

            cafe_b = await upsert_cafe(conn, "Test Café B", CAFE_B_PHONE_NUMBER_ID, "+91 90000 00002", "919999000901")
            await staff_membership(conn, b_user, cafe_b, "shift_manager", "Bea", "919999000811")
            b_tables = await seed_tables(conn, cafe_b, 1)
            b_menu = await seed_menu(conn, cafe_b, [MENU[0]])
            await conn.execute(
                """insert into customers (cafe_id, wa_id, name) values ($1, '919999000101', 'Bo')
                   on conflict (cafe_id, wa_id) do nothing""", cafe_b)
            _ = (b_tables, b_menu)

        counts = await conn.fetchrow(
            """select (select count(*) from menu_items where cafe_id = $1) items,
                      (select count(*) from customers where cafe_id = $1) customers,
                      (select count(*) from orders where cafe_id = $1) orders,
                      (select count(*) from customer_preferences where cafe_id = $1) prefs""", cafe_id)
        print(f"Kaapi House seeded: {dict(counts)}")
        print("Logins: shift@restro.test, manager@restro.test, owner@restro.test (password = SEED_STAFF_PASSWORD)")
    finally:
        await conn.close()


def _env_value(name: str) -> str | None:
    env = ROOT / "backend" / ".env"
    if not env.exists():
        return None
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip() or None
    return None


if __name__ == "__main__":
    asyncio.run(main())
