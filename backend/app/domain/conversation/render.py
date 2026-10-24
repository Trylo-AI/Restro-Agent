"""Every customer-facing message the bot sends, rendered by code (CLAUDE.md invariants 1 and 2).

Names and prices come from the DB; the LLM never writes an amount. Reply ids are `action[:arg[:arg]]`.
Keep wording here so it can be reviewed in one place.
"""

from collections.abc import Sequence

from app.domain.cart import Priced
from app.integrations.whatsapp.messages import Button, Buttons, ListMessage, Row, Section, Template, Text

MENU = Button("menu", "Menu")
RECOMMEND = Button("recommend", "Recommend")
CALL_WAITER = Button("help", "Call waiter")


def inr(paise: int) -> str:
    """₹ with Indian digit grouping, e.g. 124000 paise → ₹1,240; 12345 → ₹123.45."""
    rupees, rem = divmod(int(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    return f"₹{s}" + (f".{rem:02d}" if rem else "")


def table_name(label: str) -> str:
    """'T5' → 'Table 5'; 'Patio 2' → 'Table Patio 2'. What customers see; staff see the label as is."""
    return f"Table {label[1:]}" if len(label) > 1 and label[0] in "Tt" and label[1:].isdigit() else f"Table {label}"


def veg_mark(veg: bool) -> str:
    return "veg" if veg else "non-veg"


# ── Greeting and name ─────────────────────────────────────────────────────

def greeting_new(cafe: str, tone: str = "warm") -> Buttons:
    hello = {"crisp": f"Hi, this is {cafe}.", "playful": f"Hey there! Welcome to {cafe} ☕"}.get(tone, f"Hi! Welcome to {cafe}.")
    return Buttons(f"{hello} What should we call you?", [MENU, RECOMMEND, CALL_WAITER])


def greeting_returning(name: str, last_items: Sequence[str], last_order_id: str | None, tone: str = "warm") -> Buttons:
    hello = {"crisp": f"Hi {name}.", "playful": f"Back for more, {name}? Excellent taste."}.get(tone, f"Hi {name}! Welcome back.")
    if last_items and last_order_id:
        body = f"{hello} Last time: {', '.join(last_items)}. Same again?"
        return Buttons(body, [Button(f"reorder:{last_order_id}", "Same again"), MENU, CALL_WAITER])
    return Buttons(f"{hello} What would you like today?", [MENU, RECOMMEND, CALL_WAITER])


def name_saved(name: str) -> Buttons:
    return Buttons(f"Nice to meet you, {name}! What would you like? Type your order or open the menu.",
                   [MENU, RECOMMEND, CALL_WAITER])


def ask_name_after_order() -> Text:
    return Text("By the way, what should we call you?")


# ── Menu ──────────────────────────────────────────────────────────────────

def categories(cats: Sequence[tuple[str, str, int]]) -> ListMessage:
    rows = [Row(f"cat:{slug}", label, f"{n} item{'s' if n != 1 else ''}") for slug, label, n in cats]
    return ListMessage("What would you like? Pick a category.", "See menu", [Section("Menu", rows)])


def category_items(label: str, slug: str, items: Sequence[tuple[str, str, int, bool, int]], page: int,
                   has_more: bool) -> ListMessage:
    """items: (item_id, name, price_paise, veg, spice). Prices go in the row description."""
    rows = [Row(f"item:{iid}", name, f"{inr(paise)} · {veg_mark(veg)}" + (" · " + "🌶" * spice if spice else ""))
            for iid, name, paise, veg, spice in items]
    if has_more:
        rows.append(Row(f"cat:{slug}:{page + 1}", "More…", f"More in {label}"))
    rows.append(Row("menu", "← All categories"))
    return ListMessage(f"{label} — tap a dish to add it.", "Choose", [Section(label, rows)])


# ── Cart and confirmation ─────────────────────────────────────────────────

def summary(priced: Priced, table_label: str | None, allergy_note: str | None, version: int) -> Buttons:
    head = f"Your order · {table_name(table_label)}" if table_label else "Your order"
    lines = [head]
    for line in priced.lines:
        lines.append(f"{line.qty} × {line.item.name} {inr(line.line_paise)}")
        if line.note:
            lines.append(f"   ↳ {line.note}")
    if allergy_note:
        lines.append(f"Allergy noted: {allergy_note} — the team will check")
    lines.append(f"Total (incl. GST) {inr(priced.total_paise)}")
    lines.append(f"Ready in about {priced.expected_minutes} min")
    return Buttons("\n".join(lines), [Button(f"confirm:{version}", "Confirm"), Button(f"edit:{version}", "Edit"),
                                      Button(f"cancel:{version}", "Cancel")])


def dropped(names: Sequence[str]) -> Text:
    return Text(f"Sorry, {', '.join(names)} just sold out, so I removed {'it' if len(names) == 1 else 'them'}.")


def sold_out(name: str) -> Buttons:
    return Buttons(f"{name} is sold out today.", [MENU, CALL_WAITER])


def edit_prompt() -> Buttons:
    return Buttons("What would you like to change? Type it — like “remove the toast” or “make it 2 cold coffee” — "
                   "or add more from the menu.", [MENU])


def cart_cleared() -> Buttons:
    return Buttons("Done, your cart is empty.", [MENU, CALL_WAITER])


def empty_cart() -> Buttons:
    return Buttons("Your cart is empty. What would you like?", [MENU, CALL_WAITER])


def ask_table(tables: Sequence[tuple[str, str]]) -> ListMessage:
    rows = [Row(f"table:{tid}", table_name(label)) for tid, label in tables[:10]]
    return ListMessage("Which table are you at?", "Choose table", [Section("Tables", rows)])


def order_received(number: int, minutes: int, order_id: str, version: int) -> Buttons:
    return Buttons(f"Order #{number} received — about {minutes} min. We'll message you when it's being made.",
                   [Button(f"edit_order:{order_id}", "Edit order"), Button(f"cancel_order:{order_id}:{version}", "Cancel order"),
                    CALL_WAITER])


def order_updated(number: int, minutes: int, order_id: str, version: int) -> Buttons:
    return Buttons(f"Updated order #{number} — about {minutes} min.",
                   [Button(f"edit_order:{order_id}", "Edit order"), Button(f"cancel_order:{order_id}:{version}", "Cancel order"),
                    CALL_WAITER])


def already_placed(number: int) -> Text:
    return Text(f"Already placed — order #{number}.")


def outdated_summary() -> Text:
    return Text("That summary is out of date — here's your latest one.")


# ── Status and staff-driven messages ──────────────────────────────────────

STATUS_LINE = {
    "NEW": "received — waiting for the kitchen",
    "ACCEPTED": "being made",
    "READY": "ready and on its way",
}


def status(lines: Sequence[tuple[int, str, int | None]]) -> Text:
    """lines: (number, status, minutes_left)."""
    parts = []
    for number, st, left in lines:
        text = f"Order #{number} — {STATUS_LINE.get(st, st.lower())}"
        if st == "ACCEPTED" and left is not None:
            text += f", about {max(left, 1)} min left" if left > 0 else ", any minute now"
        parts.append(text + ".")
    return Text("\n".join(parts))


def no_open_orders() -> Buttons:
    return Buttons("You have no open orders.", [MENU, CALL_WAITER])


def order_status_template(number: int, status_text: str) -> Template:
    return Template("order_update", [str(number), status_text])


ACCEPTED_TEXT = "Your order is being made."
READY_TEXT = "Your order is ready and on its way."


# ── Help, bill, consent ───────────────────────────────────────────────────

def help_opened() -> Text:
    return Text("Someone from our team will be with you shortly.")


def bill_opened() -> Text:
    return Text("Your bill is on its way.")


def already_on_way() -> Text:
    return Text("Someone is already on the way.")


def allergy_check() -> Text:
    return Text("Let me check with the team.")


def opted_out(cafe: str) -> Text:
    return Text(f"You won't get offers from {cafe}. Ordering still works.")


# ── Can't do it now ───────────────────────────────────────────────────────

def non_text() -> Buttons:
    return Buttons("Sorry, I can only read text. Please type your order.", [MENU, CALL_WAITER])


def paused() -> Buttons:
    return Buttons("We're not taking WhatsApp orders right now — please ask a waiter.", [CALL_WAITER])


def closed(opens: str | None) -> Text:
    return Text(f"We're closed now. We open at {opens}." if opens else "We're closed now.")


def fallback() -> Buttons:
    return Buttons("Sorry, I didn't catch that. Pick from the menu or call a waiter.", [MENU, CALL_WAITER])


def dead_letter() -> Buttons:
    return Buttons("Sorry, something went wrong on our side. Tap below to talk to a person.", [CALL_WAITER])
