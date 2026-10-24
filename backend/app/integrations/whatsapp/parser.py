"""Meta webhook payload → typed events. Every entry and every change is handled (one POST can carry several).

Field names follow the Cloud API webhook format **[verify]**. Business rules don't live here.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class InboundEvent:
    phone_number_id: str
    wa_id: str
    wamid: str
    timestamp: datetime
    type: str                 # text · interactive · image · audio · sticker · video · document · location · ...
    text: str | None = None
    reply_id: str | None = None
    reply_title: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_tap(self) -> bool:
        return self.reply_id is not None


@dataclass(frozen=True)
class StatusEvent:
    phone_number_id: str
    wamid: str
    status: str               # sent · delivered · read · failed
    timestamp: datetime
    recipient_id: str | None = None
    error_code: str | None = None
    pricing_category: str | None = None
    billable: bool | None = None
    callback_data: str | None = None


@dataclass(frozen=True)
class AccountEvent:
    field: str
    value: dict[str, Any]


@dataclass
class Batch:
    messages: list[InboundEvent] = field(default_factory=list)
    statuses: list[StatusEvent] = field(default_factory=list)
    other: list[AccountEvent] = field(default_factory=list)


def _ts(value: Any) -> datetime:
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return datetime.now(UTC)


def _message(phone_number_id: str, m: dict[str, Any]) -> InboundEvent | None:
    wa_id, wamid, mtype = m.get("from"), m.get("id"), m.get("type", "unknown")
    if not wa_id or not wamid:
        return None
    text = reply_id = reply_title = None
    if mtype == "text":
        text = (m.get("text") or {}).get("body")
    elif mtype == "interactive":
        inter = m.get("interactive") or {}
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        reply_id, reply_title = reply.get("id"), reply.get("title")
    elif mtype == "button":  # quick-reply button on a template
        btn = m.get("button") or {}
        reply_id, reply_title = btn.get("payload"), btn.get("text")
    return InboundEvent(phone_number_id, str(wa_id), str(wamid), _ts(m.get("timestamp")), mtype,
                        text=text, reply_id=reply_id, reply_title=reply_title, raw=m)


def _status(phone_number_id: str, s: dict[str, Any]) -> StatusEvent | None:
    if not s.get("id") or not s.get("status"):
        return None
    errors = s.get("errors") or []
    pricing = s.get("pricing") or {}
    return StatusEvent(
        phone_number_id=phone_number_id, wamid=str(s["id"]), status=str(s["status"]), timestamp=_ts(s.get("timestamp")),
        recipient_id=s.get("recipient_id"),
        error_code=str(errors[0].get("code")) if errors else None,
        pricing_category=pricing.get("category"), billable=pricing.get("billable"),
        callback_data=s.get("biz_opaque_callback_data"),
    )


def parse(body: dict[str, Any]) -> Batch:
    batch = Batch()
    for entry in body.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            if change.get("field") != "messages":
                batch.other.append(AccountEvent(str(change.get("field")), value))
                continue
            pnid = str((value.get("metadata") or {}).get("phone_number_id", ""))
            for m in value.get("messages") or []:
                if (ev := _message(pnid, m)) is not None:
                    batch.messages.append(ev)
            for s in value.get("statuses") or []:
                if (st := _status(pnid, s)) is not None:
                    batch.statuses.append(st)
    return batch
