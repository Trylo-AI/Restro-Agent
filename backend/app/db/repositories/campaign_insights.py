import asyncpg


async def recipient_rows(conn: asyncpg.Connection, cafe_id: str, campaign_id: str) -> list[asyncpg.Record]:
    """One campaign's recipients and what happened after the send (`v_campaign_recipients`; the server role reads it
    past RLS, so `cafe_id` is always bound here)."""
    return list(await conn.fetch(
        """select recipient_id::text, customer_id::text, name, masked_phone, deleted, code, status, sent_at,
                  delivered_at, read_at, replied_at, orders, spent_paise, redeemed_at, redemption_channel, bill_paise,
                  discount_paise, redeemed_by_name, opted_out_at, opted_out_source
           from v_campaign_recipients where cafe_id = $1 and campaign_id = $2
           order by queued_at, recipient_id""", cafe_id, campaign_id))
