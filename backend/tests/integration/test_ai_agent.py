"""AI turns through the real pipeline with a scripted LLM (docs/rules.md: LLM output row)."""

import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

import asyncpg
import pytest

from app.core.config import get_settings
from app.domain.ai_agent import calls, prompt
from app.domain.orders import service
from app.integrations import llm
from app.integrations.llm import StubLlm
from tests.integration.conftest import CafeFixture, drop_test_cafe
from tests.integration.conversation_kit import (
    Kitchen,
    RecordingTransport,
    TaskRecorder,
    build_kitchen,
    create_staff,
    drop_staff,
    install,
    uninstall,
)
from tests.integration.test_conversations import principal

pytestmark = pytest.mark.integration
WA = "919999720001"


def ref(system: str, name: str) -> str:
    """The short id the prompt gave this dish (m1, m2, …)."""
    m = re.search(rf"^(m\d+)\|{re.escape(name)}\|", system, re.M)
    assert m, f"{name} not in the prompt menu"
    return m.group(1)


def out(intent: str = "add_items", ops: list[dict[str, Any]] | None = None, reply: str = "", clarify: str | None = None,
        allergen: dict[str, Any] | None = None, human: str | None = None) -> dict[str, Any]:
    return {"intent": intent, "cart_ops": ops or [], "allergen_question": allergen,
            "needs_human": {"flag": human is not None, "reason": human}, "clarify": clarify, "reply": reply}


@pytest.fixture
async def k(db: asyncpg.Pool, cafe: CafeFixture) -> AsyncIterator[Kitchen]:
    async with db.acquire() as c:
        items, tables = await build_kitchen(c, cafe.cafe_id)
        staff = await create_staff(c, cafe.cafe_id)
        pnid = await c.fetchval("select phone_number_id from cafes where id = $1", cafe.cafe_id)
    transport, rec = RecordingTransport(), TaskRecorder()
    install(transport, rec)
    prompt._cache.clear()
    try:
        yield Kitchen(db, cafe.cafe_id, pnid, items, tables, transport, rec, staff)
    finally:
        uninstall()
        calls.set_auto_kill(None)
        async with db.acquire() as c:
            await drop_test_cafe(c, cafe.cafe_id)
            await drop_staff(c, staff)


def script(fn: Callable[[str, str], Any]) -> StubLlm:
    stub = StubLlm(fn)
    llm.set_client(stub)
    return stub


async def seated(k: Kitchen, name: str = "Aman") -> None:
    await k.say(WA, "Hi, I'm at Table 5")
    await k.say(WA, name)


async def test_goal_phrase_builds_the_cart_and_meters_the_call(k: Kitchen) -> None:
    def respond(system: str, user: str) -> dict[str, Any]:
        assert "2 cold coffee aur ek avocado toast" in user
        return out(ops=[{"op": "add", "item_ref": ref(system, "Cold Coffee"), "qty": 2, "note": None},
                        {"op": "add", "item_ref": ref(system, "Avocado Toast"), "qty": 1, "note": "no chilli"}],
                   reply="Done! Kuch aur?")
    stub = script(respond)
    await seated(k)
    sent = await k.say(WA, "2 cold coffee aur ek avocado toast, toast mein chilli nahi")
    texts = [s.text for s in sent]
    assert texts[0] == "Done! Kuch aur?"
    assert "2 × Cold Coffee ₹380" in texts[1] and "↳ no chilli" in texts[1] and "Total (incl. GST) ₹700" in texts[1]
    calls_rows = await k.db.fetch("select purpose, provider, outcome, cost, message_id from llm_calls where cafe_id = $1", k.cafe_id)
    assert len(calls_rows) == 1 and calls_rows[0]["outcome"] == "ok" and calls_rows[0]["message_id"] is not None
    # Nothing sensitive in the prompt.
    p = stub.calls[0]["system"] + stub.calls[0]["prompt"]
    assert WA not in p and "+91" not in p


async def test_sold_out_and_unknown_dishes_are_explained_by_code(k: Kitchen) -> None:
    await k.db.execute("update menu_items set available = false where id = $1", k.items["Chocolate Brownie"])
    prompt._cache.clear()

    def respond(system: str, user: str) -> dict[str, Any]:
        assert "Chocolate Brownie" not in system  # sold-out dishes never reach the prompt
        return out(ops=[{"op": "add", "item_ref": "chocolate brownie", "qty": 1, "note": None},
                        {"op": "add", "item_ref": "pizza", "qty": 1, "note": None},
                        {"op": "add", "item_ref": ref(system, "Masala Chai"), "qty": 1, "note": None}],
                   reply="Added everything! That's ₹270 total.")
    script(respond)
    await seated(k)
    sent = await k.say(WA, "one brownie, a pizza and a chai")
    joined = "\n".join(s.text for s in sent)
    assert "Chocolate Brownie is sold out right now" in joined
    assert "couldn't find “pizza” on our menu" in joined
    assert "Added everything" not in joined and "₹270" not in joined  # model reply discarded (and it had an amount)
    assert "1 × Masala Chai ₹90" in joined


async def test_two_unclear_turns_hand_off(k: Kitchen) -> None:
    script(lambda s, u: out(intent="unclear", clarify="Sorry, which drink did you mean?"))
    await seated(k)
    first = await k.say(WA, "the green one")
    assert "which drink" in first[0].text
    second = await k.say(WA, "you know, that one")
    assert "with you shortly" in second[0].text
    conv = await k.conversation_id(WA)
    assert await k.db.fetchval("select mode from conversations where id = $1", conv) == "human"
    assert await k.db.fetchval("select reason_tag from requests where conversation_id = $1", conv) == "repeated_unclear"


async def test_amounts_in_model_text_never_reach_the_customer(k: Kitchen) -> None:
    script(lambda s, u: out(intent="ask_question", reply="Our cold coffee is just Rs 190, best in town!"))
    await seated(k)
    sent = await k.say(WA, "how much is cold coffee")
    assert all("190" not in s.text for s in sent)
    assert sent[0].text == "What would you like to order?"


@pytest.mark.parametrize("dish,state,expect", [
    ("Cold Coffee", "contains", "Cold Coffee contains milk."),
    ("Avocado Toast", "free_from", "Avocado Toast is marked free from milk"),
])
async def test_allergen_answers_come_from_menu_data(k: Kitchen, dish: str, state: str, expect: str) -> None:
    if state == "free_from":
        await k.db.execute("insert into menu_item_allergens (cafe_id, item_id, allergen, state) values ($1, $2, 'milk', 'free_from')",
                           k.cafe_id, k.items[dish])
    script(lambda s, u: out(intent="ask_question", allergen={"allergen": "dairy", "item_ref": ref(s, dish)},
                            reply="Yes it's totally safe!"))
    await seated(k)
    sent = await k.say(WA, f"does the {dish} have dairy")
    assert expect in sent[0].text and "totally safe" not in sent[0].text


async def test_unknown_allergen_always_reaches_a_person(k: Kitchen) -> None:
    script(lambda s, u: out(intent="ask_question", allergen={"allergen": "nuts", "item_ref": ref(s, "Chocolate Brownie")}))
    await seated(k)
    sent = await k.say(WA, "any nuts in the brownie?")
    assert sent[0].text == "Let me check with the team."
    conv = await k.conversation_id(WA)
    row = await k.db.fetchrow("select kind, reason_tag from requests where conversation_id = $1", conv)
    assert tuple(row) == ("allergy", "allergen_unknown")
    assert await k.db.fetchval("select mode from conversations where id = $1", conv) == "human"


async def test_human_intent_hands_off(k: Kitchen) -> None:
    script(lambda s, u: out(intent="human", human="frustration", reply="ok"))
    await seated(k)
    sent = await k.say(WA, "this is taking forever, very bad service")
    assert "with you shortly" in sent[0].text
    conv = await k.conversation_id(WA)
    assert await k.db.fetchval("select reason_tag from requests where conversation_id = $1", conv) == "frustration"


async def test_llm_failure_retries_on_the_other_model_then_hands_off(k: Kitchen) -> None:
    script(lambda s, u: "error")
    await seated(k)
    sent = await k.say(WA, "2 chai")
    assert sent[0].text == "Sorry, I'm having trouble — a team member will help you."
    rows = await k.db.fetch("select model, outcome from llm_calls where cafe_id = $1 order by created_at", k.cafe_id)
    s = get_settings()
    assert [(r["model"], r["outcome"]) for r in rows] == [(s.llm_model_primary, "error"), (s.llm_model_fallback, "error")]
    conv = await k.conversation_id(WA)
    assert await k.db.fetchval("select reason_tag from requests where conversation_id = $1", conv) == "system_error"


async def test_invalid_output_then_success_on_the_fallback(k: Kitchen) -> None:
    seen: list[int] = []

    def respond(system: str, user: str) -> Any:
        seen.append(1)
        return "not json at all" if len(seen) == 1 else out(ops=[{"op": "add", "item_ref": ref(system, "Masala Chai"), "qty": 2}])
    script(respond)
    await seated(k)
    sent = await k.say(WA, "do chai")
    assert "2 × Masala Chai ₹180" in sent[-1].text
    outcomes = [r["outcome"] for r in await k.db.fetch("select outcome from llm_calls where cafe_id = $1 order by created_at", k.cafe_id)]
    assert outcomes == ["invalid_output", "ok"]


async def test_kill_switch_handoff_skips_the_llm(k: Kitchen) -> None:
    stub = script(lambda s, u: out())
    calls.set_auto_kill("handoff")
    await seated(k)
    sent = await k.say(WA, "2 chai")
    assert "team member will help" in sent[0].text and stub.calls == []


async def test_typed_confirm_places_the_order_and_allergy_reaches_it(k: Kitchen) -> None:
    def respond(system: str, user: str) -> dict[str, Any]:
        if "confirm" in user.lower():
            return out(intent="confirm")
        return out(ops=[{"op": "add", "item_ref": ref(system, "Masala Chai"), "qty": 1, "note": None}])
    script(respond)
    await seated(k)
    summary = await k.say(WA, "one chai please, I'm allergic to peanuts")
    assert "Allergy noted: peanuts" in summary[-1].text
    placed = await k.say(WA, "confirm it")
    orders = await k.orders()
    assert len(orders) == 1 and orders[0]["allergy_note"] == "peanuts" and "received" in placed[0].text


async def test_prompt_uses_preferences_but_never_profile_allergies(k: Kitchen) -> None:
    stub = script(lambda s, u: out(intent="browse", reply="Try our chai!"))
    await seated(k)
    cust = await k.db.fetchval("select id from customers where cafe_id = $1 and wa_id = $2", k.cafe_id, WA)
    for kind, value in (("dislike", "mushrooms"), ("diet", "vegan"), ("allergy", "peanuts")):
        await k.db.execute("insert into customer_preferences (cafe_id, customer_id, kind, value, source) values ($1, $2, $3, $4, 'staff_note')",
                           k.cafe_id, cust, kind, value)
    await k.say(WA, "suggest something")
    p = stub.calls[-1]["prompt"]
    assert "mushrooms" in p and "vegan" in p and "peanuts" not in p


async def test_cancellation_message_guardrails(k: Kitchen) -> None:
    script(lambda s, u: out(ops=[{"op": "add", "item_ref": ref(s, "Masala Chai"), "qty": 1}]))
    await seated(k)
    await k.say(WA, "one chai")
    version = (await k.state(WA))["cart"]["version"]
    await k.say(WA, tap=f"confirm:{version}")
    order = (await k.orders())[0]
    seen: list[str] = []

    def drafts(system: str, user: str) -> dict[str, Any]:
        seen.append(user)
        return {"reply": "So sorry! We'll refund you and add a free dessert."}
    script(drafts)
    from app.domain import timers
    cancelled = await service.staff_cancel(principal(k), order["id"], "other", "Gas cylinder empty", order["version"])
    await timers.dispatch(k.rec.timers("status_message")[-1])
    msg = k.transport.sent[-1].text
    assert msg == f"Sorry, order #{order['number']} has been cancelled."  # refund promise → fixed fallback
    assert all("Gas cylinder" not in u for u in seen)  # reason_text never reaches the LLM

    script(lambda s, u: {"reply": "Sorry Aman, we had to cancel your order."})
    from app.domain.ai_agent import cancel_draft
    built = await cancel_draft.message(k.cafe_id, cancelled)
    assert built.body == f"Sorry Aman, we had to cancel your order.\nOrder #{order['number']}"
    assert [b.id for b in built.buttons] == ["help"]
    _ = json
