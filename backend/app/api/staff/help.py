"""Requests, chats, bills, notifications and console telemetry (contracts/openapi.yaml)."""

from dataclasses import asdict
from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.staff import router
from app.core.auth import Principal, manager, shift_manager
from app.core.logging import get_logger
from app.db.repositories.conversations import Conversation
from app.db.repositories.requests import Request
from app.domain.handoff import service

log = get_logger(__name__)


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplyBody(Strict):
    text: str = Field(min_length=1, max_length=1000)


class OpenBody(Strict):
    table_session_id: str


class TelemetryEvent(Strict):
    kind: Literal["realtime_disconnect", "realtime_reconnect", "offline_banner"]
    at: datetime


class TelemetryBody(Strict):
    events: list[TelemetryEvent] = Field(max_length=50)


def _request(r: Request) -> dict[str, Any]:
    d = asdict(r)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


def _conversation(c: Conversation) -> dict[str, Any]:
    return {"id": c.id, "customer_id": c.customer_id, "mode": c.mode, "assigned_to": c.assigned_to,
            "table_session_id": c.table_session_id}


@router.post("/requests/{request_id}/ack")
async def ack(request_id: str, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _request(await service.ack(p, request_id))


@router.post("/requests/{request_id}/resolve")
async def resolve(request_id: str, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _request(await service.resolve(p, request_id))


@router.post("/conversations/{conversation_id}/takeover")
async def takeover(conversation_id: str, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _conversation(await service.takeover(p, conversation_id))


@router.post("/conversations/{conversation_id}/reply")
async def reply(conversation_id: str, body: ReplyBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _conversation(await service.reply(p, conversation_id, body.text.strip()))


@router.post("/conversations/{conversation_id}/handback")
async def handback(conversation_id: str, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _conversation(await service.handback(p, conversation_id))


@router.post("/conversations/open")
async def open_table_chat(body: OpenBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _conversation(await service.open_for_table(p, body.table_session_id))


@router.post("/bills/{table_session_id}/deliver")
async def deliver(table_session_id: str, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return await service.deliver_bill(p, table_session_id)


@router.post("/notifications/{notification_id}/ack")
async def ack_notification(notification_id: str, p: Principal = Depends(manager)) -> dict[str, int]:
    return {"read": await service.ack_notification(p, notification_id)}


@router.post("/notifications/ack-all")
async def ack_all(p: Principal = Depends(manager)) -> dict[str, int]:
    return {"read": await service.ack_notification(p, None)}


@router.post("/telemetry/console", status_code=204)
async def telemetry(body: TelemetryBody, p: Principal = Depends(shift_manager)) -> Response:
    """Metrics only: kinds and counts, no content."""
    counts: dict[str, int] = {}
    for e in body.events:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    log.info("console_telemetry", cafe_id=p.cafe_id, **counts)
    return Response(status_code=204)
