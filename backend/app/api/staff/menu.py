from typing import Any

from fastapi import Depends
from pydantic import BaseModel, ConfigDict

from app.api.staff import router
from app.core.auth import Principal, shift_manager
from app.domain import menu


class AvailabilityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool


@router.post("/menu/{item_id}/availability")
async def availability(item_id: str, body: AvailabilityBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    item = await menu.set_availability(p, item_id, body.available)
    return {"id": item.id, "name": item.name, "category": item.category, "available": item.available, "version": item.version}
