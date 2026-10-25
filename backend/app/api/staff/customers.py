from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.staff import router
from app.core.auth import Principal, owner, shift_manager
from app.db.repositories.preferences import Note
from app.domain import customers

Kind = Literal["diet", "milk", "sugar", "spice", "seating", "allergy", "dislike", "like", "other"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteBody(Strict):
    text: str = Field(min_length=1, max_length=500)


class RemoveBody(Strict):
    kind: Kind
    value: str = Field(min_length=1, max_length=40)


def _note(n: Note) -> dict[str, Any]:
    d = dict(n.__dict__)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


@router.post("/customers/{customer_id}/notes")
async def add_note(customer_id: str, body: NoteBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _note(await customers.add_note(p, customer_id, body.text.strip()))


@router.post("/customer-notes/{note_id}/undo")
async def undo_note(note_id: str, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _note(await customers.undo_note(p, note_id))


@router.post("/customers/{customer_id}/preferences/remove")
async def remove_preference(customer_id: str, body: RemoveBody, p: Principal = Depends(shift_manager)) -> list[dict[str, str]]:
    remaining = await customers.remove_preference(p, customer_id, body.kind, body.value)
    return [{"kind": k, "value": v} for k, v in remaining]


@router.post("/customers/{customer_id}/delete")
async def delete_customer(customer_id: str, p: Principal = Depends(owner)) -> dict[str, str]:
    from app.domain.customers import privacy

    return await privacy.delete_customer_data(p, customer_id)


@router.get("/consents/export")
async def consent_export(p: Principal = Depends(owner)) -> Response:
    from app.domain.customers import privacy

    return Response(await privacy.consent_csv(p), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="consent-register.csv"'})
