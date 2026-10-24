from typing import Any

from fastapi import Depends
from pydantic import BaseModel, ConfigDict

from app.api.staff import router
from app.core.auth import Principal, manager
from app.core.errors import Forbidden, NotFound
from app.db.repositories import cafes
from app.db.tx import tx
from app.domain import audit


class PauseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: bool


@router.post("/cafes/{cafe_id}/pause-ordering")
async def pause_ordering(cafe_id: str, body: PauseBody, p: Principal = Depends(manager)) -> dict[str, Any]:
    if cafe_id != p.cafe_id:
        raise Forbidden()  # the path's café must be the one X-Cafe-Id authorised
    async with tx() as c:
        changed = await cafes.set_paused(c, p.cafe_id, body.paused)
        if changed is not None:
            await audit.write(c, p.cafe_id, audit.Actor(p.user_id, p.role),
                              "ordering.pause" if body.paused else "ordering.resume", {"paused": body.paused})
        current = changed or await cafes.get(c, p.cafe_id)
    if current is None:
        raise NotFound()
    return {"id": current.id, "settings": current.settings}
