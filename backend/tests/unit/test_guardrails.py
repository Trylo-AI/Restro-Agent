import pytest

from app.db.repositories.menu import MenuItem
from app.domain.ai_agent import guardrails as g
from app.integrations.llm import pricing


@pytest.mark.parametrize("text", ["That's ₹190", "Rs 190 only", "rs.190", "190 rs", "it is 190 rupees", "INR 200",
                                  "sirf 200/-", "do sau rupaye", "the price is 190"])
def test_amounts_are_caught(text: str) -> None:
    assert g.has_amount(text)
    assert g.clean_reply(text) is None


@pytest.mark.parametrize("text", ["2 cold coffee coming up", "Table 5", "Order #1048", "about 10 min", "first visit"])
def test_ordinary_numbers_pass(text: str) -> None:
    assert not g.has_amount(text)
    assert g.clean_reply(text) == text


def test_reply_is_trimmed_and_capped() -> None:
    assert g.clean_reply("  hello   there ") == "hello there"
    assert len(g.clean_reply("x" * 900) or "") == 600


@pytest.mark.parametrize("text", ["We'll refund you", "here's a voucher", "you get a free drink", "paise wapas milenge",
                                  "we'll compensate you", "10% discount next time"])
def test_refund_promises_are_caught(text: str) -> None:
    assert g.has_refund_promise(text)


@pytest.mark.parametrize("words,expected", [
    ("nuts", "tree_nuts"), ("Tree nuts", "tree_nuts"), ("dairy", "milk"), ("peanut", "peanuts"), ("gluten", "gluten"),
    ("anda", "eggs"), ("badam", "tree_nuts"), ("kiwi", None), ("", None),
])
def test_allergen_names_normalise(words: str, expected: str | None) -> None:
    got = g.normalise_allergen(words)
    assert (got[0] if got else None) == expected


def test_allergy_mentions_need_an_allergy_word() -> None:
    assert g.allergens_in("I'm allergic to peanuts and dairy") == ["milk", "peanuts"]
    assert g.allergens_in("mujhe moongphali se allergy hai") == ["peanuts"]
    assert g.allergens_in("peanut butter toast please") == []


def item(name: str, available: bool = True) -> MenuItem:
    return MenuItem(id=name, name=name, description=None, category="c", price_paise=100, prep_minutes=1, veg=True,
                    spice=0, available=available, version=1)


def test_match_by_name_needs_a_clear_winner() -> None:
    items = [item("Cold Coffee"), item("Cold Brew Tonic"), item("Avocado Toast")]
    assert g.match_by_name("avocado", items).name == "Avocado Toast"  # type: ignore[union-attr]
    assert g.match_by_name("cold coffee", items).name == "Cold Coffee"  # type: ignore[union-attr]
    assert g.match_by_name("cold", items) is None  # ambiguous
    assert g.match_by_name("pizza", items) is None


def test_llm_cost_is_never_under_reported_for_unknown_models() -> None:
    assert pricing.cost("gemini-2.5-flash-lite", 1_000_000, 0, 0) == 0.10
    assert pricing.cost("some-new-model", 1_000_000, 0, 1_000_000) == pricing.DEFAULT[0] + pricing.DEFAULT[2]
    assert pricing.cost("gemini-2.5-flash", 1000, 1000, 0) == round(1000 * 0.075 / 1_000_000, 6)
