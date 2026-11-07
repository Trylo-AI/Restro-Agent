"""Seed hook for the audit log viewer (docs/retention-audit-recommend.md, Track C).

Called by generate.py inside its transaction, after demo_retention. Synthetic data only (seed names, no numbers or
message text). About 40 staff actions written through `app.domain.audit.write`, so every row is classified and carries
its subjects exactly as the running backend would write it: accepts, a staff cancel after the kitchen accepted (with a
staff note on the order), an undo, a long delay, a chat taken from a teammate, an allergy removed by hand, a price
change, an allergen marked absent, the seeded redemptions, a campaign, team and settings changes.

Only when the café has no audit rows yet. `audit.write` stamps every row with the transaction time, so the rows are
then spread over the order and action times below (seed only: the append-only trigger is off for that one UPDATE of
`at`; no other column is touched).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import asyncpg

from app.domain import audit

SHIFT, MANAGER, OWNER = "shift@restro.test", "manager@restro.test", "owner@restro.test"
ROLES = {SHIFT: "shift_manager", MANAGER: "manager", OWNER: "owner"}  # each seed user's membership role
CANCEL_KEY = "seed-audit:cancelled"


class Writer:
    """Writes one row at a time and remembers when it should have happened."""

    def __init__(self, conn: asyncpg.Connection, cafe_id: str, users: dict[str, str]) -> None:
        self.conn, self.cafe_id, self.users = conn, cafe_id, users
        self.at: dict[str, datetime] = {}

    async def __call__(self, who: str, role: str, action: str, change: dict[str, object], at: datetime) -> None:
        actor = audit.Actor(self.users.get(who), role)
        await audit.write(self.conn, self.cafe_id, actor, action, change)
        row_id = await self.conn.fetchval(
            "select id::text from audit_log where cafe_id = $1 and at = now() and id::text <> all($2::text[]) limit 1",
            self.cafe_id, list(self.at))
        self.at[row_id] = at

    async def redate(self) -> None:
        await self.conn.execute("alter table audit_log disable trigger audit_log_append_only")
        await self.conn.executemany("update audit_log set at = $2 where id = $1::uuid",
                                    [(row_id, at) for row_id, at in self.at.items()])
        await self.conn.execute("alter table audit_log enable trigger audit_log_append_only")


async def _cancelled_order(conn: asyncpg.Connection, cafe_id: str, users: dict[str, str]) -> asyncpg.Record | None:
    """Dev Kapoor's Butter Chicken Bowl, accepted by Ravi, then cancelled by Kiran (out of stock)."""
    found = await conn.fetchrow("select id::text, number, customer_id::text, created_at from orders where idempotency_key = $1",
                                CANCEL_KEY)
    if found:
        return found
    dev = await conn.fetchval("select id from customers where cafe_id = $1 and wa_id = '919999000004'", cafe_id)
    item = await conn.fetchrow("select id, name, price_paise from menu_items where cafe_id = $1 and name = 'Butter Chicken Bowl'",
                               cafe_id)
    table = await conn.fetchval("select id from tables where cafe_id = $1 and label = 'T6'", cafe_id)
    if dev is None or item is None or table is None:
        return None
    created = (await conn.fetchval("select now()")) - timedelta(days=1, hours=3)
    session = await conn.fetchval(
        "insert into table_sessions (cafe_id, table_id, opened_at, closed_at) values ($1, $2, $3, $4) returning id",
        cafe_id, table, created, created + timedelta(minutes=30))
    number = await conn.fetchval("select next_order_number($1)", cafe_id)
    order = await conn.fetchrow(
        """insert into orders (cafe_id, customer_id, table_session_id, number, status, expected_minutes, total_paise, source,
                               idempotency_key, version, cancel_reason_code, cancel_reason_text, cancelled_by, created_at,
                               accepted_at, cancelled_at)
           values ($1, $2, $3, $4, 'CANCELLED', 18, $5, 'qr', $6, 3, 'out_of_stock', 'Ran out of chicken', $7, $8::timestamptz,
                   $8::timestamptz + interval '2 minutes', $8::timestamptz + interval '9 minutes')
           returning id::text, number, customer_id::text, created_at""",
        cafe_id, dev, session, number, item["price_paise"], CANCEL_KEY, users.get(MANAGER), created)
    await conn.execute(
        """insert into order_items (cafe_id, order_id, item_id, name_snapshot, price_snapshot_paise, qty)
           values ($1, $2, $3, $4, $5, 1)""", cafe_id, order["id"], item["id"], item["name"], item["price_paise"])
    for frm, to, actor_type, actor, minutes in ((None, "NEW", "customer", None, 0),
                                                ("NEW", "ACCEPTED", "staff", users.get(SHIFT), 2),
                                                ("ACCEPTED", "CANCELLED", "staff", users.get(MANAGER), 9)):
        await conn.execute(
            """insert into order_events (cafe_id, order_id, from_status, to_status, actor_type, actor_id, created_at)
               values ($1, $2, $3, $4, $5, $6, $7)""",
            cafe_id, order["id"], frm, to, actor_type, actor, created + timedelta(minutes=minutes))
    return order


async def seed(conn: asyncpg.Connection, cafe_id: str, users: dict[str, str]) -> None:
    if await conn.fetchval("select 1 from audit_log where cafe_id = $1 limit 1", cafe_id):
        return
    w = Writer(conn, cafe_id, users)
    now = await conn.fetchval("select now()")

    # Orders: the five latest served ones, accepted / ready / served by staff at the stamps generate.py uses.
    served = await conn.fetch(
        """select id::text, number, customer_id::text, table_session_id::text, total_paise, created_at from orders
           where cafe_id = $1 and status = 'SERVED' order by created_at desc limit 5""", cafe_id)
    for i, o in enumerate(served):
        base = {"order_id": o["id"], "number": o["number"], "customer_id": o["customer_id"]}
        who, role = (SHIFT, "shift_manager") if i % 2 == 0 else (MANAGER, "manager")
        await w(who, role, "order.transition", {**base, "from": "NEW", "to": "ACCEPTED"}, o["created_at"] + timedelta(minutes=2))
        if i == 1:  # marked ready too early, then undone
            await w(SHIFT, "shift_manager", "order.transition", {**base, "from": "ACCEPTED", "to": "READY"},
                    o["created_at"] + timedelta(minutes=5))
            await w(SHIFT, "shift_manager", "order.undo", {**base, "from": "READY", "to": "ACCEPTED"},
                    o["created_at"] + timedelta(minutes=5, seconds=20))
        if i == 2:
            await w(SHIFT, "shift_manager", "order.delay", {**base, "minutes": 20, "has_note": True},
                    o["created_at"] + timedelta(minutes=6))
        await w(who, role, "order.transition", {**base, "from": "ACCEPTED", "to": "READY"}, o["created_at"] + timedelta(minutes=12))
        await w(SHIFT, "shift_manager", "order.transition", {**base, "from": "READY", "to": "SERVED"},
                o["created_at"] + timedelta(minutes=18))
    if served:
        o = served[0]
        await w(SHIFT, "shift_manager", "bill.deliver",
                {"table_session_id": o["table_session_id"], "total_paise": o["total_paise"], "orders": [o["number"]],
                 "subtotal_paise": o["total_paise"], "discount_paise": 0, "code": None}, o["created_at"] + timedelta(minutes=45))

    # A staff cancel after the kitchen accepted it (critical); the staff note stays on the order, never in `change`.
    cancelled = await _cancelled_order(conn, cafe_id, users)
    if cancelled:
        base = {"order_id": cancelled["id"], "number": cancelled["number"], "customer_id": cancelled["customer_id"]}
        await w(SHIFT, "shift_manager", "order.transition", {**base, "from": "NEW", "to": "ACCEPTED"},
                cancelled["created_at"] + timedelta(minutes=2))
        await w(MANAGER, "manager", "order.cancel", {**base, "from": "ACCEPTED", "to": "CANCELLED", "reason_code": "out_of_stock"},
                cancelled["created_at"] + timedelta(minutes=9))

    # Chats: Ravi takes Riya's chat, Kiran takes it from him (warning), replies and hands it back.
    riya = await conn.fetchrow(
        """select cu.id::text as customer_id, cv.id::text as conversation_id from customers cu
           join conversations cv on cv.customer_id = cu.id where cu.cafe_id = $1 and cu.wa_id = '919999000001'""", cafe_id)
    if riya:
        chat = {"conversation_id": riya["conversation_id"], "customer_id": riya["customer_id"]}
        at = now - timedelta(days=2, hours=4)
        await w(SHIFT, "shift_manager", "chat.takeover",
                {**chat, "via": "takeover", "from_mode": "bot", "from_assigned_to": None}, at)
        await w(MANAGER, "manager", "chat.takeover",
                {**chat, "via": "takeover", "from_mode": "human", "from_assigned_to": users.get(SHIFT)}, at + timedelta(minutes=3))
        await w(MANAGER, "manager", "chat.reply", {**chat, "chars": 64}, at + timedelta(minutes=4))
        await w(MANAGER, "manager", "chat.handback", chat, at + timedelta(minutes=12))
        # The seeded comment on Riya's profile, applied by the AI on Ravi's behalf.
        await w(SHIFT, "staff_via_ai", "customer.profile_update",
                {"customer_id": riya["customer_id"], "ops": [{"op": "add", "kind": "allergy", "value": "peanuts"},
                                                             {"op": "add", "kind": "dislike", "value": "onion"}]},
                now - timedelta(days=11, hours=23))

    # An allergy removed by hand (critical).
    meera = await conn.fetchval("select id::text from customers where cafe_id = $1 and wa_id = '919999000003'", cafe_id)
    if meera:
        await w(MANAGER, "manager", "customer.profile_update",
                {"customer_id": meera, "ops": [{"op": "remove", "kind": "allergy", "value": "sesame"}], "by_hand": True},
                now - timedelta(days=1, hours=6))

    # Calls: Dev's complaint, acknowledged and resolved.
    req = await conn.fetchrow(
        """select id::text, kind, order_id::text, conversation_id::text, created_at from requests
           where cafe_id = $1 and reason_tag = 'complaint' order by created_at limit 1""", cafe_id)
    if req:
        links = {k: req[k] for k in ("order_id", "conversation_id") if req[k]}
        await w(SHIFT, "shift_manager", "request.ack", {"request_id": req["id"], "kind": req["kind"], **links},
                req["created_at"] + timedelta(minutes=1))
        await w(MANAGER, "manager", "request.resolve", {"request_id": req["id"], "kind": req["kind"], **links},
                req["created_at"] + timedelta(minutes=6))

    # Menu: a price change (warning), an allergen marked absent (critical), two sold-out toggles.
    items = {r["name"]: r for r in await conn.fetch(
        "select id::text, name, price_paise, available from menu_items where cafe_id = $1", cafe_id)}
    if "Masala Chai" in items:
        chai = items["Masala Chai"]
        await w(MANAGER, "manager", "menu.update",
                {"item_id": chai["id"], "name": chai["name"], "new": False, "fields": ["price_paise"], "allergens": 1,
                 "orders_flagged": 0, "price_paise_from": chai["price_paise"] - 1000, "price_paise_to": chai["price_paise"],
                 "available_from": True, "available_to": True, "allergens_changed": []},
                now - timedelta(days=3, hours=2))
    if "Cold Brew" in items:
        brew = items["Cold Brew"]
        await w(MANAGER, "manager", "menu.update",
                {"item_id": brew["id"], "name": brew["name"], "new": False, "fields": [], "allergens": 1, "orders_flagged": 0,
                 "price_paise_from": brew["price_paise"], "price_paise_to": brew["price_paise"], "available_from": True,
                 "available_to": True, "allergens_changed": [{"allergen": "milk", "from": "contains", "to": "free_from"}]},
                now - timedelta(days=3, hours=1))
    for name, flagged, hours in (("Vietnamese Iced Coffee", 0, 30), ("Pesto Pasta", 1, 5)):
        if name in items:
            await w(SHIFT, "shift_manager", "menu.availability",
                    {"item_id": items[name]["id"], "name": name, "available": False, "orders_flagged": flagged},
                    now - timedelta(hours=hours))

    # Offers: the redemptions demo_retention seeded, with the D28 payload.
    for r in await conn.fetch(
            """select d.code, d.channel, d.table_session_id::text, d.order_id::text, o.number as order_number,
                      d.campaign_recipient_id::text as recipient_id, cr.campaign_id::text, cr.customer_id::text,
                      ca.discount_percent, d.bill_paise, d.discount_paise, d.redeemed_at, d.redeemed_by::text
               from redemptions d join campaign_recipients cr on cr.id = d.campaign_recipient_id
               join campaigns ca on ca.id = cr.campaign_id left join orders o on o.id = d.order_id
               where d.cafe_id = $1 order by d.redeemed_at""", cafe_id):
        who = next((email for email, uid in users.items() if uid == r["redeemed_by"] and email in ROLES), SHIFT)
        await w(who, ROLES[who], "offer.redeem",
                {"table_session_id": r["table_session_id"], "order_id": r["order_id"], "order_number": r["order_number"],
                 "campaign_id": r["campaign_id"], "recipient_id": r["recipient_id"], "customer_id": r["customer_id"],
                 "code": r["code"], "channel": r["channel"], "discount_percent": r["discount_percent"],
                 "bill_paise": r["bill_paise"], "discount_paise": r["discount_paise"]}, r["redeemed_at"])

    # Campaigns: the seeded campaign's draft and schedule.
    camp = await conn.fetchrow(
        """select id::text, discount_percent, scheduled_at, created_at,
                  (select count(*) from campaign_recipients r where r.campaign_id = c.id)::int as audience
           from campaigns c where cafe_id = $1 order by created_at limit 1""", cafe_id)
    if camp:
        await w(OWNER, "owner", "campaign.draft",
                {"campaign_id": camp["id"], "new": True, "fields": ["name", "segment", "template_name", "discount_percent"],
                 "discount_percent": camp["discount_percent"]}, camp["created_at"])
        await w(OWNER, "owner", "campaign.schedule",
                {"campaign_id": camp["id"], "audience": camp["audience"],
                 "scheduled_at": (camp["scheduled_at"] or camp["created_at"]).isoformat()},
                camp["created_at"] + timedelta(minutes=10))

    # Team and settings (owner).
    for email, role, days in ((MANAGER, "manager", 6), (SHIFT, "manager", 6)):
        mid = await conn.fetchval("select id::text from memberships where cafe_id = $1 and user_id = $2", cafe_id,
                                  users.get(email))
        if mid:
            await w(OWNER, "owner", "team.invite", {"membership_id": mid, "user_id": users[email], "role": role},
                    now - timedelta(days=days))
    ravi = await conn.fetchval("select id::text from memberships where cafe_id = $1 and user_id = $2", cafe_id, users.get(SHIFT))
    if ravi:  # Ravi was invited as a manager, then moved to shift manager (critical)
        await w(OWNER, "owner", "team.update",
                {"membership_id": ravi, "user_id": users[SHIFT], "from_role": "manager", "role": "shift_manager",
                 "from_active": True, "active": True}, now - timedelta(days=5, hours=20))
    await w(OWNER, "owner", "settings.update", {"fields": ["alert_phone", "cutoff_minutes"]}, now - timedelta(days=5, hours=18))
    await w(MANAGER, "manager", "ordering.pause", {"paused": True}, now - timedelta(days=4, hours=3))
    await w(MANAGER, "manager", "ordering.resume", {"paused": False}, now - timedelta(days=4, hours=2, minutes=40))
    await w(OWNER, "owner", "consent.export", {"rows": 3}, now - timedelta(days=1, hours=1))
    await w(MANAGER, "manager", "notification.ack", {"count": 2}, now - timedelta(hours=7))
    await w.redate()
