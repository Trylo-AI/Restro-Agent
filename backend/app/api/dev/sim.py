"""WhatsApp simulator API (docs/ordering-slice.md#simulator). Local only, synthetic data only.

Customer messages enter through the real webhook (signed, over HTTP), so every production check runs.
The few writes here (reset, close-window) are dev tooling against the synthetic café.
"""

import hmac
import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Any, Literal

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.db.repositories import cafes, conversations
from app.db.repositories.conversations import DEFAULT_STATE
from app.db.tx import conn, tx
from app.integrations.whatsapp import simulated
from app.integrations.whatsapp.messages import limit_violations

SIM_PHONE_NUMBER_ID = "100000000000001"
SIM_WA_PREFIX = "9199990000"


async def require_sim_token(x_sim_token: str | None = Header(default=None)) -> None:
    token = get_settings().simulator_token
    expected = token.get_secret_value() if token else ""
    if not (expected and x_sim_token and hmac.compare_digest(x_sim_token, expected)):
        raise HTTPException(status_code=401)


router = APIRouter(prefix="/dev/sim", dependencies=[Depends(require_sim_token)], include_in_schema=False)

# In-memory, per api process; fine for one local developer.
_faults: dict[str, deque[str]] = defaultdict(deque)
_typing_until: dict[str, float] = {}
_events: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=200))
_allocated: set[str] = set()

FAULTS = {"send_timeout": "send", "send_429": "send", "llm_fail": "llm", "llm_invalid": "llm", "turn_crash": "turn"}


def event(wa_id: str, kind: str, **data: Any) -> None:
    _events[wa_id].append({"at": datetime.now(UTC).isoformat(), "kind": kind, **data})


async def _sim_cafe(c: asyncpg.Connection, pnid: str) -> cafes.Cafe:
    cafe = await cafes.by_phone_number_id(c, pnid)
    if cafe is None:
        raise HTTPException(status_code=404, detail="simulator café not seeded (run supabase/seed/generate.py)")
    return cafe


class SendBody(BaseModel):
    wa_id: str = Field(pattern=r"^\d{10,15}$")
    kind: Literal["text", "button", "list", "image", "audio", "sticker"]
    text: str | None = Field(default=None, max_length=4096)
    reply_id: str | None = Field(default=None, max_length=256)
    reply_title: str | None = Field(default=None, max_length=72)
    name: str | None = Field(default=None, max_length=60)
    phone_number_id: str = SIM_PHONE_NUMBER_ID


@router.post("/send")
async def send(body: SendBody) -> dict[str, Any]:
    kind = "interactive" if body.kind in ("button", "list") else body.kind
    payload, wamid = simulated.message_payload(
        body.phone_number_id, body.wa_id, body.name, kind, text=body.text,
        reply_id=body.reply_id, reply_title=body.reply_title, list_reply=body.kind == "list",
    )
    started = time.perf_counter()
    status = await simulated.post_signed(payload)
    event(body.wa_id, "webhook", wamid=wamid, type=body.kind, status=status,
          ms=round((time.perf_counter() - started) * 1000))
    return {"wamid": wamid, "webhook_status": status}


@router.get("/thread")
async def thread(wa_id: str, after: datetime | None = None, phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, Any]:
    async with conn() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        customer = await c.fetchrow("select id::text, name, last_inbound_at from customers where cafe_id = $1 and wa_id = $2",
                                    cafe.id, wa_id)
        if customer is None:
            return {"cafe": cafe.name, "items": [], "typing": False, "customer": None}
        since = after or datetime(1970, 1, 1, tzinfo=UTC)
        ins = await c.fetch(
            """select i.id::text, i.wamid, i.type, i.payload, i.received_at as at, i.processed_at
               from inbound_messages i join conversations cv on cv.id = i.conversation_id
               where i.cafe_id = $1 and cv.customer_id = $2 and i.received_at > $3 order by i.received_at""",
            cafe.id, customer["id"], since)
        outs = await c.fetch(
            """select id::text, wamid, type, template_name, payload, status, error_code, created_at as at, updated_at
               from wa_messages where cafe_id = $1 and customer_id = $2 and (created_at > $3 or updated_at > $3)
               order by created_at""",
            cafe.id, customer["id"], since)
    items = [{"dir": "in", "id": r["id"], "wamid": r["wamid"], "type": r["type"], "payload": r["payload"],
              "at": r["at"].isoformat(), "processed": r["processed_at"] is not None} for r in ins]
    items += [{"dir": "out", "id": r["id"], "wamid": r["wamid"], "type": r["type"], "template_name": r["template_name"],
               "payload": {k: v for k, v in r["payload"].items() if not k.startswith("_")}, "status": r["status"],
               "error_code": r["error_code"], "at": r["at"].isoformat(), "updated_at": r["updated_at"].isoformat(),
               "violations": limit_violations(r["payload"])} for r in outs]
    items.sort(key=lambda x: x["at"])
    last_inbound = customer["last_inbound_at"]
    window_open = bool(last_inbound and (datetime.now(UTC) - last_inbound).total_seconds() < 86400)
    return {"cafe": cafe.name, "items": items, "typing": _typing_until.get(wa_id, 0) > time.time(),
            "customer": {"id": customer["id"], "name": customer["name"], "window_open": window_open},
            "server_time": datetime.now(UTC).isoformat()}


class WaBody(BaseModel):
    wa_id: str = Field(pattern=r"^\d{10,15}$")
    phone_number_id: str = SIM_PHONE_NUMBER_ID


@router.post("/read")
async def read(body: WaBody) -> dict[str, int]:
    async with conn() as c:
        cafe = await _sim_cafe(c, body.phone_number_id)
        rows = await c.fetch(
            """select m.id::text, m.wamid from wa_messages m join customers cu on cu.id = m.customer_id
               where m.cafe_id = $1 and cu.wa_id = $2 and m.status in ('sent', 'delivered') and m.wamid is not null""",
            cafe.id, body.wa_id)
    for r in rows:
        await simulated.post_signed(simulated.status_payload(body.phone_number_id, r["wamid"], "read", body.wa_id, r["id"]))
    return {"read": len(rows)}


@router.get("/debug")
async def debug(wa_id: str, phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, Any]:
    async with conn() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        convo = await c.fetchrow(
            """select cv.id::text, cv.mode, cv.assigned_to::text, cv.state, cv.lease_owner, cv.lease_expires_at,
                      cv.table_session_id::text, t.label as table_label
               from conversations cv join customers cu on cu.id = cv.customer_id
               left join table_sessions ts on ts.id = cv.table_session_id left join tables t on t.id = ts.table_id
               where cv.cafe_id = $1 and cu.wa_id = $2""", cafe.id, wa_id)
        if convo is None:
            return {"conversation": None, "events": list(_events[wa_id])}
        work = await c.fetch(
            """select status, attempts, next_attempt_at, last_error, dead_lettered_at, updated_at from pending_work
               where conversation_id = $1 order by created_at desc limit 3""", convo["id"])
        llm = await c.fetch(
            """select id::text, purpose, provider, model, outcome, latency_ms, input_tokens, cached_tokens, output_tokens,
                      cost, currency, created_at from llm_calls where conversation_id = $1 order by created_at desc limit 5""",
            convo["id"])
        try:
            traces = await c.fetch(
                """select llm_call_id::text, purpose, prompt_chars, suffix, raw_output, parsed, guardrails, created_at
                   from dev_turn_traces where conversation_id = $1 order by created_at desc limit 5""", convo["id"])
        except asyncpg.UndefinedTableError:
            traces = []
        order_events = await c.fetch(
            """select o.number, e.from_status, e.to_status, e.actor_type, e.reason, e.created_at
               from order_events e join orders o on o.id = e.order_id
               join customers cu on cu.id = o.customer_id
               where o.cafe_id = $1 and cu.wa_id = $2 order by e.created_at desc limit 10""", cafe.id, wa_id)
    lease = {"owner": bool(convo["lease_owner"]), "expires_at": convo["lease_expires_at"]}
    return {
        "conversation": {"id": convo["id"], "mode": convo["mode"], "assigned_to": convo["assigned_to"],
                         "state": convo["state"], "table": convo["table_label"], "lease": lease},
        "work": [dict(r) for r in work],
        "llm_calls": [dict(r) for r in llm],
        "traces": [dict(r) for r in traces],
        "order_events": [dict(r) for r in order_events],
        "events": list(_events[wa_id]),
    }


@router.get("/customers")
async def list_customers(phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, Any]:
    async with conn() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        rows = await c.fetch("select wa_id, name from customers where cafe_id = $1 and wa_id like $2 order by wa_id",
                             cafe.id, SIM_WA_PREFIX + "%")
        tables = await c.fetch("select label, qr_text from tables where cafe_id = $1 order by length(label), label", cafe.id)
    return {"cafe": cafe.name, "customers": [dict(r) for r in rows], "tables": [dict(r) for r in tables]}


@router.post("/customers")
async def new_customer(phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, str]:
    async with conn() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        used = {r["wa_id"] for r in await c.fetch("select wa_id from customers where cafe_id = $1", cafe.id)}
    for n in range(10, 100):
        wa_id = f"{SIM_WA_PREFIX}{n:02d}"
        if wa_id not in used and wa_id not in _allocated:
            _allocated.add(wa_id)
            return {"wa_id": wa_id}
    raise HTTPException(status_code=409, detail="no free synthetic numbers")


@router.post("/customers/{wa_id}/reset")
async def reset_customer(wa_id: str, phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, bool]:
    async with tx() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        convo_id = await c.fetchval(
            """select cv.id::text from conversations cv join customers cu on cu.id = cv.customer_id
               where cv.cafe_id = $1 and cu.wa_id = $2""", cafe.id, wa_id)
        if convo_id:
            await c.execute(
                """update conversations set state = $3, mode = 'bot', assigned_to = null, table_session_id = null,
                          lease_owner = null, lease_expires_at = null where cafe_id = $1 and id = $2""",
                cafe.id, convo_id, DEFAULT_STATE)
            await c.execute("update pending_work set status = 'done' where conversation_id = $1 and status in ('pending', 'running')",
                            convo_id)
            await c.execute("update inbound_messages set processed_at = now() where conversation_id = $1 and processed_at is null",
                            convo_id)
            await c.execute("update requests set status = 'resolved', resolved_at = now() where conversation_id = $1 and status <> 'resolved'",
                            convo_id)
    _events.pop(wa_id, None)
    return {"reset": bool(convo_id)}


@router.post("/customers/{wa_id}/close-window")
async def close_window(wa_id: str, phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, bool]:
    async with conn() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        done = await c.fetchval(
            "update customers set last_inbound_at = now() - interval '25 hours' where cafe_id = $1 and wa_id = $2 returning id",
            cafe.id, wa_id)
    event(wa_id, "window_closed")
    return {"closed": done is not None}


class FaultBody(BaseModel):
    fault: Literal["send_timeout", "send_429", "llm_fail", "llm_invalid", "turn_crash"]


@router.post("/faults")
async def arm_fault(body: FaultBody) -> dict[str, list[str]]:
    _faults[FAULTS[body.fault]].append(body.fault)
    return {k: list(v) for k, v in _faults.items()}


@router.get("/faults")
async def armed_faults() -> dict[str, list[str]]:
    return {k: list(v) for k, v in _faults.items()}


class ConsumeBody(BaseModel):
    kind: Literal["send", "llm", "turn"]


@router.post("/_faults/consume")
async def consume_fault(body: ConsumeBody) -> dict[str, str | None]:
    """Called by the worker's simulator hooks. Each armed fault fires once."""
    queue = _faults[body.kind]
    return {"fault": queue.popleft() if queue else None}


class EventBody(BaseModel):
    wa_id: str
    kind: str = Field(max_length=40)
    data: dict[str, Any] = Field(default_factory=dict)


@router.post("/_events")
async def push_event(body: EventBody) -> dict[str, bool]:
    if body.kind == "typing":
        _typing_until[body.wa_id] = time.time() + 6
    event(body.wa_id, body.kind, **body.data)
    return {"ok": True}


@router.get("/alerts")
async def alerts(phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, Any]:
    async with conn() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        rows = await c.fetch(
            """select id::text, template_name, payload, status, created_at from wa_messages
               where cafe_id = $1 and customer_id is null order by created_at desc limit 50""", cafe.id)
    return {"alerts": [{**dict(r), "created_at": r["created_at"].isoformat()} for r in rows]}


@router.get("/conversation-id")
async def conversation_id(wa_id: str, phone_number_id: str = SIM_PHONE_NUMBER_ID) -> dict[str, str | None]:
    async with conn() as c:
        cafe = await _sim_cafe(c, phone_number_id)
        cust = await c.fetchval("select id::text from customers where cafe_id = $1 and wa_id = $2", cafe.id, wa_id)
        convo = await conversations.by_customer(c, cafe.id, cust) if cust else None
    return {"conversation_id": convo.id if convo else None}
