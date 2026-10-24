from datetime import timedelta
from typing import Any, Literal

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from app.api.staff import router
from app.core.auth import Principal, shift_manager
from app.core.errors import BadRequest
from app.db.repositories.orders import Order
from app.domain.orders import UNDO_SECONDS, service


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TransitionBody(Strict):
    to: Literal["ACCEPTED", "READY", "SERVED"]
    expected_version: int = Field(ge=1)


class CancelBody(Strict):
    reason_code: Literal["out_of_stock", "kitchen_issue", "customer_asked", "other"]
    reason_text: str | None = Field(default=None, max_length=120)
    expected_version: int = Field(ge=1)


class DelayBody(Strict):
    minutes: int = Field(ge=1, le=99)
    note: str | None = Field(default=None, max_length=120)


class ManualItem(Strict):
    item_id: str
    qty: int = Field(ge=1, le=20)
    note: str | None = Field(default=None, max_length=120)


class ManualBody(Strict):
    table_id: str
    items: list[ManualItem] = Field(min_length=1, max_length=30)
    allergy_note: str | None = Field(default=None, max_length=200)


def out(order: Order, *, undo: bool = False) -> dict[str, Any]:
    body = order.as_dict()
    body.pop("idempotency_key", None)
    body["undo_until"] = (order.updated_at + timedelta(seconds=UNDO_SECONDS)).isoformat() if undo else None
    return body


@router.post("/orders/{order_id}/transition")
async def transition(order_id: str, body: TransitionBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return out(await service.staff_transition(p, order_id, body.to, body.expected_version), undo=True)


@router.post("/orders/{order_id}/cancel")
async def cancel(order_id: str, body: CancelBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    # Role by status (shift_manager only while NEW) is checked in the domain, against the stored status.
    return out(await service.staff_cancel(p, order_id, body.reason_code, body.reason_text, body.expected_version))


@router.post("/orders/{order_id}/undo")
async def undo(order_id: str, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return out(await service.staff_undo(p, order_id))


@router.post("/orders/{order_id}/delay")
async def delay(order_id: str, body: DelayBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return out(await service.staff_delay(p, order_id, body.minutes, body.note))


@router.post("/orders/manual")
async def manual(body: ManualBody, p: Principal = Depends(shift_manager),
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 80:
        raise BadRequest("Idempotency-Key header is required.")
    items = [i.model_dump() for i in body.items]
    return out(await service.manual_order(p, body.table_id, items, body.allergy_note, idempotency_key))
