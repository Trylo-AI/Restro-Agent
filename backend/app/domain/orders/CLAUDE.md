# domain/orders — Module Context (L2) · MONEY + STATE PATH

> Parent: [domain/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [order-lifecycle](../../../../docs/backend/order-lifecycle.md) · [cart-and-confirmation](../../../../docs/backend/cart-and-confirmation.md) · [staff-panel](../../../../docs/frontend/staff-panel.md) · [api/staff](../../../../docs/api/staff.md)

## Responsibility

Order creation, status transitions, ETA and the overdue flag.

## Create (one transaction)

Insert `orders` (`UNIQUE (cart_id, cart_version)`, per-café `order_number`) plus `order_items` with `name_snapshot` and `unit_price_snapshot`, plus `order_status_events (→ PLACED)`. Set `expected_ready_at = now + max(prep_minutes)`, then commit. Realtime then notifies the staff panel. Reply "Order #N received, about M minutes". On the first order with no consent record, ask for consent.

## Allowed transitions

| From → To | Who | Customer message |
|---|---|---|
| → PLACED | customer | "Order #123 received, about 20 min" |
| PLACED → PREPARING | staff | "The kitchen has started on your order" (edits/cancel now blocked) |
| PREPARING → READY | staff | "Your order is ready and on its way" |
| READY → SERVED | staff | — (schedule `feedback_prompt` at +30 min) |
| PLACED → CANCELLED | customer / staff ⚠️ *open: §6.7 says staff, §4/§8.2 say `manager`+* | "Your order has been cancelled" + reason |
| PREPARING → CANCELLED | **manager**+ | same |

Anything else is rejected.

## Must

- Write `order_status_events (from, to, actor, at)` **and** `audit_log` on every transition.
- Send the customer message through `outbound`. Inside the 24h window it's free text. Outside it, the `order_update` utility template.
- Treat **overdue as a flag, not a state**: `now > expected_ready_at` and not READY. The `overdue_sweep` sets it and sends one delay message.
- Have the sweep re-alert an order left unacknowledged in PLACED for more than 60 s, via the `new_order_alert` template to the staff number.
- Exclude cancelled orders from revenue analytics.

## Must never

- Create an order outside the confirm path, e.g. from the AI or from an admin shortcut.
- Allow a backwards transition, or customer edits after PREPARING.
