"""Seed hook for counter redemptions and campaign insights (docs/retention-audit-recommend.md, Track A).

Called by generate.py inside its transaction, after seed_retention. Synthetic data only (seed names, 9199990000xx
numbers). One completed campaign, "Diwali comeback", sent 10 days ago and valid for 5 more days, with one recipient
per status (queued, deferred, sent, delivered, read, held, failed). Riya redeemed hers on a table bill and Arjun at
the counter. Sana opted out after the send. Meera's code is still eligible: the e2e and by-hand checks redeem it.
Idempotent: nothing is written when the campaign already exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import asyncpg

CAMPAIGN = "Diwali comeback"
EXTRA = [("919999000006", "Kabir Rao"), ("919999000007", "Isha Nair")]
# (wa_id, code, recipient status, wa_messages status or None when never sent)
RECIPIENTS = [
    ("919999000001", "DIWALI-R7K", "read", "read"),          # Riya: redeemed on a table bill
    ("919999000002", "DIWALI-A4M", "delivered", "delivered"),  # Arjun: redeemed at the counter
    ("919999000003", "DIWALI-M9P", "sent", "sent"),          # Meera: eligible
    ("919999000004", "DIWALI-D2X", "failed", "failed"),      # Dev
    ("919999000005", "DIWALI-S5H", "held", "sent"),          # Sana: held, opted out after the send
    ("919999000006", "DIWALI-K3Q", "queued", None),          # Kabir
    ("919999000007", "DIWALI-I8W", "deferred", None),        # Isha
]


async def seed(conn: asyncpg.Connection, cafe_id: str, users: dict[str, str]) -> None:
    if await conn.fetchval("select 1 from campaigns where cafe_id = $1 and name = $2", cafe_id, CAMPAIGN):
        return
    now = datetime.now(UTC)
    sent = now - timedelta(days=10)
    for wa_id, name in EXTRA:
        await conn.execute(
            """insert into customers (cafe_id, wa_id, name, first_seen_at, last_inbound_at)
               values ($1, $2, $3, $4, $4) on conflict (cafe_id, wa_id) do nothing""",
            cafe_id, wa_id, name, now - timedelta(days=40))
    campaign_id = await conn.fetchval(
        """insert into campaigns (cafe_id, name, segment, template_name, offer_text, discount_percent, code_prefix,
                                  valid_until, scheduled_at, status, created_at)
           values ($1, $2, 'all_opted_in', 'weekly_offer', '20% off your next visit', 20, 'DIWALI', $3, $4,
                   'completed', $5) returning id""",
        cafe_id, CAMPAIGN, now + timedelta(days=5), sent, sent - timedelta(hours=2))
    recipients: dict[str, tuple[str, str, str]] = {}  # wa_id → (recipient_id, customer_id, code)
    for i, (wa_id, code, status, msg_status) in enumerate(RECIPIENTS):
        customer_id = await conn.fetchval("select id from customers where cafe_id = $1 and wa_id = $2", cafe_id, wa_id)
        recipient_id = await conn.fetchval(
            """insert into campaign_recipients (cafe_id, campaign_id, customer_id, code, status, created_at, updated_at)
               values ($1, $2, $3, $4, $5, $6, $6) returning id""",
            cafe_id, campaign_id, customer_id, code, status, sent)
        recipients[wa_id] = (str(recipient_id), str(customer_id), code)
        if msg_status is None:
            continue
        at = sent + timedelta(seconds=i)
        await conn.execute(
            """insert into wa_messages (cafe_id, customer_id, idempotency_key, type, template_name, payload, status,
                                        attempted_at, sent_at, delivered_at, read_at, failed_at, error_code,
                                        pricing_category, billable, cost, currency, cost_basis, created_at)
               values ($1, $2, $3, 'template', 'weekly_offer', '{}'::jsonb, $4, $5, $6, $7, $8, $9, $10,
                       'marketing', $11, $12, 'INR', 'estimated', $5)""",
            cafe_id, customer_id, f"campaign:{recipient_id}", msg_status, at,
            at if msg_status != "failed" else None,
            at + timedelta(minutes=1) if msg_status in ("delivered", "read") else None,
            at + timedelta(hours=3) if msg_status == "read" else None,
            at if msg_status == "failed" else None,
            "131026" if msg_status == "failed" else None,
            msg_status != "failed", 0.88 if msg_status != "failed" else None)

    # Riya came back and redeemed on her table bill (3 days ago, served, the code linked to the order).
    riya_recipient, riya, riya_code = recipients["919999000001"]
    at = now - timedelta(days=3)
    table_id = await conn.fetchval("select id from tables where cafe_id = $1 order by label limit 1", cafe_id)
    session_id = await conn.fetchval(
        "insert into table_sessions (cafe_id, table_id, opened_at, closed_at) values ($1, $2, $3, $4) returning id",
        cafe_id, table_id, at, at + timedelta(minutes=50))
    item_id, item_name, price = await conn.fetchrow(
        """select id, name, price_paise from menu_items where cafe_id = $1
           order by (name = 'Cold Brew Tonic') desc, name limit 1""", cafe_id)
    total = price * 2
    order_id = await conn.fetchval(
        """insert into orders (cafe_id, customer_id, table_session_id, number, status, expected_minutes, total_paise,
                               source, campaign_recipient_id, idempotency_key, version, created_at, accepted_at,
                               ready_at, served_at)
           values ($1, $2, $3, next_order_number($1), 'SERVED', 5, $4, 'campaign', $5, 'demo-retention:riya', 4, $6::timestamptz,
                   $6::timestamptz + interval '2 minutes', $6::timestamptz + interval '12 minutes',
                   $6::timestamptz + interval '18 minutes') returning id""",
        cafe_id, riya, session_id, total, riya_recipient, at)
    await conn.execute(
        """insert into order_items (cafe_id, order_id, item_id, name_snapshot, price_snapshot_paise, qty)
           values ($1, $2, $3, $4, $5, 2)""", cafe_id, order_id, item_id, item_name, price)
    await conn.execute("update customers set last_visit_at = greatest(last_visit_at, $2) where id = $1", riya, at)
    await conn.execute(
        """insert into redemptions (cafe_id, campaign_recipient_id, code, order_id, table_session_id, discount_paise,
                                    redeemed_by, redeemed_at, channel, bill_paise)
           values ($1, $2, $3, $4, $5, $6, $7, $8, 'table_bill', $9)""",
        cafe_id, riya_recipient, riya_code, order_id, session_id, total * 20 // 100, users["manager@restro.test"],
        at + timedelta(minutes=45), total)

    # Arjun walked in and paid ₹ 1,240 at the counter (2 days ago).
    arjun_recipient, _, arjun_code = recipients["919999000002"]
    await conn.execute(
        """insert into redemptions (cafe_id, campaign_recipient_id, code, discount_paise, redeemed_by, redeemed_at,
                                    channel, bill_paise, idempotency_key)
           values ($1, $2, $3, 24800, $4, $5, 'counter', 124000, 'demo-retention:arjun')""",
        cafe_id, arjun_recipient, arjun_code, users["shift@restro.test"], now - timedelta(days=2))

    # Sana tapped "Stop offers" after the send. Never before her seeded opt-in, so opted_out is her latest consent.
    _, sana, _ = recipients["919999000005"]
    await conn.execute(
        """insert into consents (cafe_id, customer_id, type, status, wording_version, source, created_at)
           values ($1, $2, 'marketing', 'opted_out', 1, 'button',
                   greatest($3, (select max(s.created_at) + interval '1 second' from consents s
                                 where s.customer_id = $2)))""", cafe_id, sana, sent + timedelta(days=1))
