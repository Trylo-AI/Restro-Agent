"""Load-test harness (loadtest/run.py) decisions and the stub_menu responder it relies on."""

import importlib.util
import sys
from pathlib import Path

import pytest

from app.integrations.llm.stub import menu_words

LOADTEST = Path(__file__).resolve().parents[3] / "loadtest"


@pytest.fixture(scope="module")
def run():
    sys.path.insert(0, str(LOADTEST))
    spec = importlib.util.spec_from_file_location("loadtest_run", LOADTEST / "run.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["loadtest_run"] = module  # dataclasses resolve annotations through sys.modules
    spec.loader.exec_module(module)
    return module


def _text(body):
    return {"payload": {"type": "text", "text": {"body": body}}, "status": "sent"}


def _buttons(body, *ids):
    buttons = [{"type": "reply", "reply": {"id": i, "title": i.split(":")[0].title()}} for i in ids]
    return {"payload": {"type": "interactive", "interactive": {"type": "button", "body": {"text": body},
                                                                "action": {"buttons": buttons}}}, "status": "sent"}


def test_customer_script(run):
    flow = run.Flow()
    assert run.next_action([_text("Welcome! What's your name?")], flow, "Asha", "1 Cappuccino") == ("text", {"text": "Asha"})
    assert run.next_action([_text("Nice to meet you")], flow, "Asha", "1 Cappuccino") == ("text", {"text": "1 Cappuccino"})
    assert run.next_action([_text("Done! Anything else?")], flow, "Asha", "1 Cappuccino") == ("text", {"text": "that's all"})
    tap = run.next_action([_buttons("1 × Cappuccino", "confirm:3", "edit:3")], flow, "Asha", "1 Cappuccino")
    assert tap == ("button", {"reply_id": "confirm:3", "reply_title": "Confirm"})
    assert run.next_action([_text("Order #12 placed")], flow, "Asha", "1 Cappuccino") is None


def test_name_asked_only_once_and_order_first_when_not_asked(run):
    flow = run.Flow()
    assert run.next_action([_text("Hi! You're at L3.")], flow, "Dev", "2 Flat White") == ("text", {"text": "2 Flat White"})
    assert run.next_action([_text("Done! By the way, your name?")], flow, "Dev", "2 Flat White") == ("text", {"text": "Dev"})
    assert run.next_action([_text("your name?")], flow, "Dev", "2 Flat White") == ("text", {"text": "that's all"})
    assert run.next_action([_text("Anything else?")], flow, "Dev", "2 Flat White") is None  # no Confirm: stop


def test_last_confirm_button_wins(run):
    outs = [_buttons("old", "confirm:1"), _text("x"), _buttons("new", "confirm:2", "edit:2")]
    assert run.button(outs, "confirm:") == ("confirm:2", "Confirm")
    assert run.button([_text("x")], "confirm:") is None


def test_percentiles(run):
    assert run.pct([], 0.95) is None
    assert run.pct([100.0], 0.95) == 100.0
    assert run.pct([float(v) for v in range(1, 101)], 0.5) == 50.5
    assert run.pct([float(v) for v in range(1, 101)], 1.0) == 100.0


def test_verdict(run):
    clean = {"lost_inbound": 0, "unprocessed": 0, "duplicate_outbound": 0, "duplicate_orders": 0, "dead_lettered": 0,
             "outbound_not_sent": 0, "webhook_errors": 0, "stuck_conversations": 0,
             "reply_ms": {"p50": 1200.0, "p95": 3100.0, "max": 4000.0, "n": 50}}
    assert run.verdict(clean) == []
    bad = {**clean, "lost_inbound": 1, "duplicate_outbound": 2, "reply_ms": {**clean["reply_ms"], "p95": 5200.0}}
    fails = run.verdict(bad)
    assert len(fails) == 3
    assert any("never stored" in f for f in fails) and any("duplicate replies" in f for f in fails)
    assert run.verdict({**clean, "reply_ms": {"p50": None, "p95": None, "max": None, "n": 0}})  # nothing measured
    # From a laptop far from the DB the latency is reported, not gated; correctness checks still apply.
    far = {**clean, "reply_ms": {**clean["reply_ms"], "p95": 41000.0}, "latency_gate": False, "db_rtt_ms": 180.0}
    assert run.verdict(far) == []
    assert "180.0 ms" in run.latency_note(far)
    assert run.latency_note(clean) is None
    assert run.verdict({**far, "lost_inbound": 1}) == ["1 acknowledged messages never stored"]


def test_stub_menu_reads_dishes_and_confirms():
    prompt = "STEP: browsing\nCUSTOMER NOW SAYS:\n2 Cold Coffee and 1 Chocolate Brownie"
    out = menu_words("", prompt)
    assert out["intent"] == "add_items"
    assert [(o["item_ref"], o["qty"]) for o in out["cart_ops"]] == [("Cold Coffee", 2), ("Chocolate Brownie", 1)]
    assert menu_words("", "CUSTOMER NOW SAYS:\nthat's all")["intent"] == "confirm"
    assert menu_words("", "CUSTOMER NOW SAYS:\nAsha") is None  # falls back to the stub's unclear reply
    # Only the customer's own line counts, never the menu or the chat above it.
    assert menu_words("", "RECENT CHAT:\ncustomer: 2 Mocha\nCUSTOMER NOW SAYS:\nhello") is None
