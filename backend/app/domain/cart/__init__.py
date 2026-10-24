"""Cart (docs/backend.md#cart). Critical core: money and availability.

`state.cart = {items: [{item_id, qty, note}], version}`. No prices are stored: every render reads live prices
and availability. Integer paise only; total = sum of lines (prices include GST, decision G2).
Lines are keyed by (item_id, note). `version` + 1 on every change.
"""

from dataclasses import dataclass, field
from typing import Any

from app.db.repositories.menu import MenuItem

QTY_MAX = 20
NOTE_MAX = 120


class CartError(Exception):
    """An op that is rejected (the reason code goes back to the customer as code-rendered text)."""

    def __init__(self, reason: str, item_id: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason  # unknown · sold_out · qty_range · not_in_cart · note_too_long
        self.item_id = item_id


def empty() -> dict[str, Any]:
    return {"items": [], "version": 0}


def _norm_note(note: str | None) -> str | None:
    note = " ".join((note or "").split())
    if len(note) > NOTE_MAX:
        raise CartError("note_too_long")
    return note or None


def _find(cart: dict[str, Any], item_id: str, note: str | None) -> dict[str, Any] | None:
    return next((line for line in cart["items"] if line["item_id"] == item_id and line.get("note") == note), None)


def _bump(cart: dict[str, Any]) -> dict[str, Any]:
    return {"items": cart["items"], "version": int(cart.get("version", 0)) + 1}


def add(cart: dict[str, Any], item: MenuItem | None, qty: int = 1, note: str | None = None) -> dict[str, Any]:
    if item is None:
        raise CartError("unknown")
    if not item.available:
        raise CartError("sold_out", item.id)
    if not 1 <= qty <= QTY_MAX:
        raise CartError("qty_range", item.id)
    note = _norm_note(note)
    items = [dict(line) for line in cart.get("items", [])]
    new = {"items": items, "version": cart.get("version", 0)}
    line = _find(new, item.id, note)
    if line:
        if line["qty"] + qty > QTY_MAX:
            raise CartError("qty_range", item.id)
        line["qty"] += qty
    else:
        items.append({"item_id": item.id, "qty": qty, "note": note})
    return _bump(new)


def remove(cart: dict[str, Any], item_id: str, note: str | None = None, *, any_note: bool = True) -> dict[str, Any]:
    items = [dict(line) for line in cart.get("items", [])]
    keep = [line for line in items if not (line["item_id"] == item_id and (any_note or line.get("note") == note))]
    if len(keep) == len(items):
        raise CartError("not_in_cart", item_id)
    return _bump({"items": keep, "version": cart.get("version", 0)})


def set_qty(cart: dict[str, Any], item_id: str, qty: int) -> dict[str, Any]:
    if not 0 <= qty <= QTY_MAX:
        raise CartError("qty_range", item_id)
    items = [dict(line) for line in cart.get("items", [])]
    lines = [line for line in items if line["item_id"] == item_id]
    if not lines:
        raise CartError("not_in_cart", item_id)
    if qty == 0:
        items = [line for line in items if line["item_id"] != item_id]
    else:
        lines[0]["qty"] = qty
        items = [line for line in items if line["item_id"] != item_id or line is lines[0]]
    return _bump({"items": items, "version": cart.get("version", 0)})


def set_note(cart: dict[str, Any], item_id: str, note: str | None) -> dict[str, Any]:
    note = _norm_note(note)
    items = [dict(line) for line in cart.get("items", [])]
    lines = [line for line in items if line["item_id"] == item_id]
    if not lines:
        raise CartError("not_in_cart", item_id)
    if len(lines) == 1:
        lines[0]["note"] = note
    else:  # several lines of the same dish: the note goes on the first, others merge into it
        lines[0]["note"] = note
        lines[0]["qty"] = min(QTY_MAX, sum(line["qty"] for line in lines))
        items = [line for line in items if line["item_id"] != item_id or line is lines[0]]
    return _bump({"items": items, "version": cart.get("version", 0)})


def clear(cart: dict[str, Any]) -> dict[str, Any]:
    return _bump({"items": [], "version": cart.get("version", 0)})


@dataclass
class Line:
    item: MenuItem
    qty: int
    note: str | None

    @property
    def line_paise(self) -> int:
        return self.item.price_paise * self.qty


@dataclass
class Priced:
    lines: list[Line] = field(default_factory=list)
    dropped: list[MenuItem] = field(default_factory=list)  # in the cart but sold out (or gone) now
    total_paise: int = 0
    expected_minutes: int = 0
    cart: dict[str, Any] = field(default_factory=empty)    # the cart after dropping unavailable lines

    @property
    def is_empty(self) -> bool:
        return not self.lines


def price(cart: dict[str, Any], items: dict[str, MenuItem]) -> Priced:
    """Live prices and availability. A line whose item is unavailable is dropped (and the version bumped)."""
    out = Priced()
    kept = []
    for raw in cart.get("items", []):
        item = items.get(raw["item_id"])
        if item is None or not item.available:
            if item is not None:
                out.dropped.append(item)
            continue
        out.lines.append(Line(item, int(raw["qty"]), raw.get("note")))
        kept.append(raw)
    out.total_paise = sum(line.line_paise for line in out.lines)
    out.expected_minutes = max((line.item.prep_minutes for line in out.lines), default=0)
    version = int(cart.get("version", 0)) + (1 if len(kept) != len(cart.get("items", [])) else 0)
    out.cart = {"items": kept, "version": version}
    return out


def item_ids(cart: dict[str, Any]) -> list[str]:
    return [line["item_id"] for line in cart.get("items", [])]
