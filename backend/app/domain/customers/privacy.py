"""Customer data deletion and the consent export (docs/architecture.md#data-protection, console.md#customers).

Deletion is one transaction and can't be undone: the name is cleared, `wa_id` becomes a random token (the same
number later is a new customer with no consent), preferences are deleted, staff comments are erased, chat text
(`inbound_messages.payload`, `wa_messages.payload`, matching `webhook_events`) and conversation state are purged,
feedback text is cleared (the rating stays). Orders, items, redemptions, campaign recipients and consents stay,
anonymous. A `data_deletion_requests` row records it; audit `customer.delete`.

Deviation: comment rows are erased in place (text "(deleted)", no applied changes) rather than deleted, because
`llm_calls` (append-only) points at them and an FK "set null" would be an update it forbids.
"""

import csv
import io
import secrets

from app.core.auth import Principal
from app.core.errors import NotFound
from app.db.repositories.conversations import DEFAULT_STATE
from app.db.tx import conn, tx
from app.domain import audit


def masked(wa_id: str) -> str:
    return f"+{wa_id[:2]} {wa_id[2:4]}••• ••{wa_id[-3:]}" if len(wa_id) >= 10 else "••••"


async def delete_customer_data(p: Principal, customer_id: str) -> dict[str, str]:
    async with tx() as c:
        wa_id = await c.fetchval("select wa_id from customers where cafe_id = $1 and id = $2 for update", p.cafe_id, customer_id)
        if wa_id is None:
            raise NotFound()
        if wa_id.startswith("000"):
            done = await c.fetchrow("""select id::text, status from data_deletion_requests where cafe_id = $1 and customer_id = $2
                                       order by created_at desc limit 1""", p.cafe_id, customer_id)
            if done:
                return dict(done)  # already deleted
        request_id = await c.fetchval(
            "insert into data_deletion_requests (cafe_id, customer_id, source) values ($1, $2, 'owner') returning id::text",
            p.cafe_id, customer_id)
        token = "000" + "".join(secrets.choice("0123456789") for _ in range(12))  # never a real number (no country code 0)
        await c.execute("update customers set name = null, wa_id = $3 where cafe_id = $1 and id = $2", p.cafe_id, customer_id, token)
        await c.execute("delete from customer_preferences where cafe_id = $1 and customer_id = $2", p.cafe_id, customer_id)
        await c.execute("""update customer_notes set text = '(deleted)', ai_ops = '[]'::jsonb, ai_summary = null
                           where cafe_id = $1 and customer_id = $2""", p.cafe_id, customer_id)
        await c.execute("""update inbound_messages set payload = '{}'::jsonb
                           where cafe_id = $1 and conversation_id in (select id from conversations where customer_id = $2)""",
                        p.cafe_id, customer_id)
        await c.execute("update wa_messages set payload = '{}'::jsonb where cafe_id = $1 and customer_id = $2", p.cafe_id, customer_id)
        await c.execute("update conversations set state = $3, mode = 'bot', assigned_to = null where cafe_id = $1 and customer_id = $2",
                        p.cafe_id, customer_id, DEFAULT_STATE)
        await c.execute("delete from webhook_events where payload::text like '%' || $1 || '%'", wa_id)
        await c.execute("""update feedback set text = null
                           where cafe_id = $1 and order_id in (select id from orders where customer_id = $2)""",
                        p.cafe_id, customer_id)
        await c.execute("update data_deletion_requests set status = 'completed', completed_at = now() where id = $1", request_id)
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "customer.delete",
                          {"customer_id": customer_id, "request_id": request_id})
    return {"id": request_id, "status": "completed"}


async def consent_csv(p: Principal) -> str:
    """Full consent history with masked numbers, oldest first."""
    async with conn() as c:
        rows = await c.fetch(
            """select cu.wa_id, cu.name, s.type, s.status, s.wording_version, s.source, s.created_at
               from consents s join customers cu on cu.id = s.customer_id
               where s.cafe_id = $1 order by s.created_at""", p.cafe_id)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["customer", "first_name", "type", "status", "wording_version", "source", "at"])
    for r in rows:
        w.writerow([masked(r["wa_id"]), (r["name"] or "").split(" ")[0], r["type"], r["status"], r["wording_version"] or "",
                    r["source"], r["created_at"].isoformat()])
    async with tx() as c:
        await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role), "consent.export", {"rows": len(rows)})
    return out.getvalue()
