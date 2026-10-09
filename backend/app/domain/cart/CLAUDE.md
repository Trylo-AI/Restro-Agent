# domain/cart — Module Context (L2) · MONEY PATH

> Parent: [domain/CLAUDE.md](../CLAUDE.md) · Root: [/CLAUDE.md](../../../../CLAUDE.md)
> Docs: [cart-and-confirmation](../../../../docs/backend/cart-and-confirmation.md) · [menu-management](../../../../docs/backend/menu-management.md) · [constraints-and-indexes](../../../../docs/database/constraints-and-indexes.md)

## Responsibility

Cart lines, totals, tax and **versioning**. This is the only place prices are computed. Confirmation must never be wrong.

## Confirmation contract

1. `request_confirmation(cart)`: reload the lines with **current** prices and availability from the DB.
2. If an item became unavailable: remove the line and tell the customer "Sorry, X just sold out", with the updated cart.
3. Otherwise: `cart.version += 1`, store `priced_snapshot`, return the summary (items, qty, notes, subtotal, tax, total) with buttons whose ID is `confirm:{cart_id}:{version}`, Edit and Cancel.
4. On a Confirm tap: valid only if `version == current` **and** the cart isn't already ordered. Otherwise reply "This cart changed, here's the latest" or "Already placed".
5. When valid, hand off to `orders.create_order`.

## Must

- Compute subtotal, tax and total in code, from DB prices. Tax rules are a café setting (GST inclusive/exclusive is an **open decision**).
- Bump the version on **every** priced summary, so old Confirm buttons go stale.
- Enforce qty 1–20 and that the cart belongs to this conversation.

## Must never

- Accept a price from the AI, the client or a button payload.
- Render dish names or prices from anything but the DB.
- Mutate a cart that's already ordered.

## Tests that must stay green

Totals and tax · version bump makes old buttons stale · sold-out at confirm · double-tap confirm gives one order.
