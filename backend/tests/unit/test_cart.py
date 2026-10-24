"""Cart: every mix of qty and notes; total = sum of lines; integer paise only (docs/rules.md Cart row)."""

import pytest

from app.db.repositories.menu import MenuItem
from app.domain import cart as c


def item(i: str, rupees: int, prep: int = 5, available: bool = True) -> MenuItem:
    return MenuItem(id=i, name=f"Dish {i}", description=None, category="Cat", price_paise=rupees * 100,
                    prep_minutes=prep, veg=True, spice=0, available=available, version=1)


COFFEE, TOAST, SOLD = item("cof", 190, 4), item("toa", 320, 8), item("old", 100, available=False)
MENU = {i.id: i for i in (COFFEE, TOAST, SOLD)}


def test_add_merges_same_item_and_note_and_bumps_version() -> None:
    cart = c.add(c.empty(), COFFEE, 2)
    cart = c.add(cart, COFFEE, 1)
    cart = c.add(cart, TOAST, 1, "no chilli")
    cart = c.add(cart, TOAST, 1, "  no   chilli ")
    cart = c.add(cart, TOAST, 1)  # different note → separate line
    assert cart["version"] == 5
    assert cart["items"] == [
        {"item_id": "cof", "qty": 3, "note": None},
        {"item_id": "toa", "qty": 2, "note": "no chilli"},
        {"item_id": "toa", "qty": 1, "note": None},
    ]


@pytest.mark.parametrize("bad", [0, 21, -1])
def test_qty_range(bad: int) -> None:
    with pytest.raises(c.CartError) as e:
        c.add(c.empty(), COFFEE, bad)
    assert e.value.reason == "qty_range"


def test_qty_cap_applies_to_the_merged_line() -> None:
    cart = c.add(c.empty(), COFFEE, 15)
    with pytest.raises(c.CartError):
        c.add(cart, COFFEE, 6)


def test_unknown_and_sold_out_are_rejected() -> None:
    with pytest.raises(c.CartError) as e1:
        c.add(c.empty(), None)
    with pytest.raises(c.CartError) as e2:
        c.add(c.empty(), SOLD)
    assert (e1.value.reason, e2.value.reason) == ("unknown", "sold_out")


def test_set_qty_zero_removes_and_missing_lines_are_rejected() -> None:
    cart = c.add(c.add(c.empty(), COFFEE, 2), TOAST)
    cart = c.set_qty(cart, "cof", 0)
    assert c.item_ids(cart) == ["toa"]
    with pytest.raises(c.CartError):
        c.set_qty(cart, "cof", 1)
    with pytest.raises(c.CartError):
        c.remove(cart, "cof")
    with pytest.raises(c.CartError):
        c.set_note(cart, "cof", "x")


def test_set_note_merges_lines_of_the_same_dish() -> None:
    cart = c.add(c.add(c.empty(), TOAST, 1, "no chilli"), TOAST, 2)
    cart = c.set_note(cart, "toa", "extra butter")
    assert cart["items"] == [{"item_id": "toa", "qty": 3, "note": "extra butter"}]
    with pytest.raises(c.CartError):
        c.set_note(cart, "toa", "x" * 121)


def test_price_is_integer_paise_sum_of_lines_and_max_prep() -> None:
    cart = c.add(c.add(c.empty(), COFFEE, 2), TOAST, 1, "no chilli")
    p = c.price(cart, MENU)
    assert p.total_paise == 2 * 19000 + 32000 == 70000
    assert isinstance(p.total_paise, int) and all(isinstance(line.line_paise, int) for line in p.lines)
    assert p.expected_minutes == 8


def test_price_reads_live_prices_and_drops_items_sold_out_since() -> None:
    cart = c.add(c.add(c.empty(), COFFEE, 2), TOAST)
    now = {**MENU, "toa": item("toa", 320, 8, available=False), "cof": item("cof", 200, 4)}
    p = c.price(cart, now)
    assert [line.item.id for line in p.lines] == ["cof"] and [d.id for d in p.dropped] == ["toa"]
    assert p.total_paise == 40000  # the price changed: the live price wins
    assert p.cart["version"] == cart["version"] + 1 and c.item_ids(p.cart) == ["cof"]


def test_clear() -> None:
    cart = c.clear(c.add(c.empty(), COFFEE))
    assert cart == {"items": [], "version": 2}
