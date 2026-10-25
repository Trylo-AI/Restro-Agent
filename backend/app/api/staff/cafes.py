from typing import Any, Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.staff import router
from app.core.auth import Principal, manager, owner
from app.core.errors import Forbidden, NotFound
from app.db.repositories import cafes
from app.db.tx import tx
from app.domain import audit, setup


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


Role = Literal["shift_manager", "manager", "owner"]


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    opening_hours: dict[str, list[list[str]]] | None = None
    cutoff_minutes: int | None = Field(default=None, ge=0, le=180)
    tone: Literal["warm", "crisp", "playful"] | None = None
    feedback_delay_minutes: Literal[15, 30, 60] | None = None
    alert_phone: str | None = Field(default=None, pattern=r"^\+?[0-9]{10,15}$")


@router.put("/cafes/{cafe_id}/settings")
async def save_settings(cafe_id: str, body: SettingsBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    if cafe_id != p.cafe_id:
        raise Forbidden()
    data = body.model_dump(exclude_unset=True)
    if "alert_phone" in data and data["alert_phone"]:
        data["alert_phone"] = data["alert_phone"].lstrip("+")
    return await setup.save_settings(p, data)


class TableBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=20)
    area: str | None = Field(default=None, max_length=40)
    seats: int | None = Field(default=None, ge=1, le=50)


@router.post("/tables")
async def create_table(body: TableBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await setup.save_table(p, None, body.label, body.area, body.seats)


@router.put("/tables/{table_id}")
async def edit_table(table_id: str, body: TableBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await setup.save_table(p, table_id, body.label, body.area, body.seats)


class InviteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(max_length=200, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role
    display_name: str | None = Field(default=None, max_length=40)


@router.post("/team/invite")
async def invite(body: InviteBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await setup.invite(p, str(body.email), body.role, (body.display_name or "").strip() or None)


class MemberBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Role | None = None
    active: bool | None = None


@router.patch("/team/{membership_id}")
async def update_member(membership_id: str, body: MemberBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await setup.update_member(p, membership_id, body.role, body.active)
