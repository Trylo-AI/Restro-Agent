"""App evaluation scoring (evals/app_run.py) and the DB snapshot diff (loadtest/db_stats.py): pure parts."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    for p in ("supabase/seed", "loadtest", "evals"):
        sys.path.insert(0, str(ROOT / p))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def app_run():
    return _load("app_run_under_test", ROOT / "evals" / "app_run.py")


@pytest.fixture(scope="module")
def db_stats():
    return _load("db_stats_under_test", ROOT / "loadtest" / "db_stats.py")


def seen(**kw):
    base = {"stored": True, "cart": {}, "mode": "bot", "replies": [{"text": "ok", "buttons": []}], "requests": [],
            "parsed": None, "llm": [], "dead_lettered": False, "reply_ms": 1000, "runs": []}
    return {**base, **kw}


def test_cart_is_scored_from_the_app_state(app_run):
    case = {"text": "teen cappuccino, ek mein sugar nahi", "expect": {"cart": [{"dish": "Cappuccino", "qty": 3, "note": "sugar"}]}}
    good = seen(cart={"Cappuccino": {"qty": 3, "notes": ["no sugar"]}}, parsed={"intent": "add_items"})
    assert app_run.score(case, good) == []
    wrong = seen(cart={"Cappuccino": {"qty": 4, "notes": ["no sugar"]}})
    assert app_run.score(case, wrong) == ["Cappuccino qty 4 ≠ 3"]


def test_staff_request_decides_handoff(app_run):
    case = {"text": "my order is cold", "expect": {"human": True}}
    assert app_run.score(case, seen(requests=[{"kind": "help", "reason_tag": "complaint"}])) == []
    assert app_run.score(case, seen()) == ["staff request False ≠ True"]
    assert app_run.score({"text": "tell me a joke", "expect": {"human": False}}, seen(mode="human"))


def test_router_keyword_counts_as_the_intent(app_run):
    # "where is my order" never reaches the AI: the router's status keyword answers it.
    case = {"text": "where is my order?", "expect": {"intent": ["order_status"]}}
    assert app_run.score(case, seen()) == []


def test_no_reply_and_dead_letters_fail(app_run):
    case = {"text": "1 cappuccino", "expect": {}}
    assert "no reply to the customer" in app_run.score(case, seen(replies=[]))
    assert "dead-lettered" in app_run.score(case, seen(dead_lettered=True))
    assert app_run.score(case, {"stored": False}) == ["message not stored"]


def test_sold_out_never_in_the_cart(app_run):
    case = {"text": "pesto pasta please", "expect": {"never": ["Pesto Pasta"]}}
    told = [{"text": "Pesto Pasta is sold out right now. How about Mushroom Risotto?", "buttons": []}]
    assert app_run.score(case, seen(replies=told)) == []
    assert app_run.score(case, seen(replies=told, cart={"Pesto Pasta": {"qty": 1, "notes": []}})) == [
        "Pesto Pasta in the cart"]
    # The AI saying it isn't on the menu is wrong for the customer: it is, it's sold out.
    wrong = [{"text": "We don't have pesto pasta on our menu.", "buttons": []}]
    assert app_run.score(case, seen(replies=wrong)) == ["not told it's sold out (told it isn't on the menu)"]


def test_db_diff_separates_reads_writes_and_postgrest(db_stats):
    before = {"statements": [
        {"queryid": "1", "calls": 10, "total": 5.0, "rows": 10, "query": "select * from orders where cafe_id = $1"},
        {"queryid": "2", "calls": 1, "total": 2.0, "rows": 1, "query": "update conversations set state = $1 where id = $2"},
    ], "tables": [{"relname": "orders", "seq_scan": 1, "seq_tup_read": 10, "idx_scan": 5, "n_tup_ins": 0, "n_tup_upd": 0,
                   "n_tup_del": 0, "n_live_tup": 100}]}
    after = {"statements": [
        {"queryid": "1", "calls": 30, "total": 15.0, "rows": 30, "query": "select * from orders where cafe_id = $1"},
        {"queryid": "2", "calls": 5, "total": 42.0, "rows": 5, "query": "update conversations set state = $1 where id = $2"},
        {"queryid": "3", "calls": 4, "total": 8.0, "rows": 4,
         "query": "with s as (update wa_messages set attempted_at = now() where id = $1 returning *) select * from s"},
        {"queryid": "4", "calls": 100, "total": 13.0, "rows": 100,
         "query": "select set_config('search_path', $1, true), set_config($2, $3, true)"},
        {"queryid": "5", "calls": 9, "total": 1.0, "rows": 9, "query": "select * from pg_stat_statements"},
    ], "tables": [{"relname": "orders", "seq_scan": 3, "seq_tup_read": 210, "idx_scan": 25, "n_tup_ins": 0, "n_tup_upd": 0,
                   "n_tup_del": 0, "n_live_tup": 100}]}
    d = db_stats.diff(before, after)
    assert d["reads"] == {"statements": 1, "calls": 20, "total_ms": 10.0, "mean_ms": 0.5}
    assert d["writes"]["calls"] == 8 and d["writes"]["total_ms"] == 48.0  # the CTE update counts as a write
    assert d["postgrest_request_setup"] == {"requests": 100, "total_ms": 13.0}
    assert d["top_by_total"][0]["mean_ms"] == 10.0  # the conversations update: 40 ms over 4 calls
    assert d["seq_scans"][0]["table"] == "orders" and d["seq_scans"][0]["seq_tup_read"] == 200


def test_db_diff_sums_the_rows_each_role_has_for_one_statement(db_stats):
    q = "select set_config('search_path', $1, true), set_config($2, $3, true)"
    before = {"statements": [{"queryid": "9", "calls": 18316, "total": 2400.0, "rows": 0, "query": q},
                             {"queryid": "9", "calls": 21, "total": 1.0, "rows": 0, "query": q}], "tables": []}
    same = db_stats.diff(before, before)
    assert same["postgrest_request_setup"] == {"requests": 0, "total_ms": 0.0}  # not 18,295
    after = {"statements": [{**before["statements"][0], "calls": 18422, "total": 2410.0}, before["statements"][1]],
             "tables": []}
    assert db_stats.diff(before, after)["postgrest_request_setup"] == {"requests": 106, "total_ms": 10.0}
