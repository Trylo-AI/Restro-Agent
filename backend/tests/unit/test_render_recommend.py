"""The picks message and the "For you" row (docs/backend.md#recommendations)."""

from app.domain import recommend
from app.domain.conversation import render
from app.integrations.whatsapp import messages as m

PICKS = [("id-1", "Cold Coffee", 19000, True, "Your usual, and it's warm out"),
         ("id-2", "Masala Chai", 9000, True, "A gentle pick-me-up"),
         ("id-3", "Chicken Club Sandwich With Extra Long Name", 32000, False, "Goes well with your cart")]


def rows(p: dict) -> list[dict]:  # type: ignore[type-arg]
    return [r for s in p["interactive"]["action"]["sections"] for r in s["rows"]]


def test_picks_list_at_most_ten_rows() -> None:
    p = m.payload(render.recommendations(PICKS + [("id-4", "Extra", 100, True, "x")], "Here are a few picks for you:"))
    assert len(rows(p)) == 10 and m.limit_violations(p) == []
    assert [s["title"] for s in p["interactive"]["action"]["sections"]] == ["Picks for you", "Pick by mood", "More"]
    assert p["interactive"]["action"]["button"] == "See picks"


def test_pick_titles_clipped_prices_from_db() -> None:
    msg = render.recommendations(PICKS, "Here are a few picks for you, Riya:")
    body = msg.body
    assert body.splitlines()[0] == "Here are a few picks for you, Riya:"
    assert "1. Cold Coffee · ₹190\n   Your usual, and it's warm out" in body and "2. Masala Chai · ₹90" in body
    assert body.endswith("Tap a dish to add it, or pick a mood.")
    picks = msg.sections[0].rows
    assert all(len(r.title) <= m.ROW_TITLE_MAX for r in picks) and picks[2].title.endswith("…")
    assert picks[0].description == "₹190 · veg · Tap to add" and picks[2].description == "₹320 · non-veg · Tap to add"


def test_pick_row_ids_are_item_rec() -> None:
    msg = render.recommendations(PICKS, "Hi")
    assert [r.id for r in msg.sections[0].rows] == ["item:id-1:rec", "item:id-2:rec", "item:id-3:rec"]
    assert [r.id for r in msg.sections[2].rows] == ["rec_more", "menu"]
    assert [r.id for r in render.recommendations(PICKS, "Hi", more=False).sections[2].rows] == ["menu"]


def test_active_mood_row_omitted() -> None:
    msg = render.recommendations(PICKS, "For something light, try:", mood="light")
    assert [r.id for r in msg.sections[1].rows] == ["rec_mood:energy", "rec_mood:cool", "rec_mood:comfort", "rec_mood:sweet"]
    assert [(c, t) for c, t in render.MOOD_ROWS] == [(c, v[0]) for c, v in recommend.MOODS.items()]


def test_edit_mode_body_names_order() -> None:
    msg = render.recommendations(PICKS[:1], "Hi", editing=42, allergy=True)
    assert msg.body.endswith("Tap a dish to add it to order #42.")
    assert render.ALLERGY_CAVEAT in msg.body


def test_categories_adds_suggest_row_when_nine_or_fewer() -> None:
    nine = [(f"c{n}", f"Cat {n}", 2) for n in range(9)]
    p = m.payload(render.categories(nine))
    assert rows(p)[0]["id"] == "recommend" and rows(p)[0]["title"] == "✨ Suggest for me" and len(rows(p)) == 10
    assert m.limit_violations(p) == []
    ten = [(f"c{n}", f"Cat {n}", 2) for n in range(10)]
    assert "recommend" not in [r["id"] for r in rows(m.payload(render.categories(ten)))]
    assert "recommend" not in [r.id for s in render.categories(nine, with_recommend=False).sections for r in s.rows]


def test_no_safe_picks_and_no_more() -> None:
    plain = render.no_safe_picks(False)
    assert plain.body.startswith("I couldn't find a dish on our menu that fits your preferences.")
    assert [b.id for b in plain.buttons] == ["menu", "help"]
    assert "preferences and allergies" in render.no_safe_picks(True).body
    assert render.no_more_picks().body == "That's everything I'd suggest right now."
