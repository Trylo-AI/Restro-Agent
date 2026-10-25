from typing import Any, Literal

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from app.api.staff import router
from app.core.auth import Principal, manager, owner, shift_manager
from app.core.errors import BadRequest
from app.db.repositories.menu import MenuItem
from app.domain import menu
from app.domain.menu.csv_import import ALLERGENS, MAX_BYTES

Allergen = Literal["gluten", "milk", "eggs", "peanuts", "tree_nuts", "soy", "sesame", "fish", "shellfish", "mustard",
                   "sulphites"]


class AvailabilityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    available: bool


class AllergenState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allergen: Allergen
    state: Literal["contains", "free_from", "unknown"]


class ItemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=200)
    category: str = Field(min_length=1, max_length=40)
    price_paise: int = Field(gt=0, le=10_000_000)
    prep_minutes: int = Field(ge=1, le=120)
    veg: bool
    spice: int = Field(default=0, ge=0, le=3)
    available: bool = True
    allergens: list[AllergenState] = Field(default_factory=list, max_length=len(ALLERGENS))

    def fields(self) -> dict[str, object]:
        return {"name": self.name.strip(), "description": (self.description or "").strip() or None,
                "category": self.category.strip(), "price_paise": self.price_paise, "prep_minutes": self.prep_minutes,
                "veg": self.veg, "spice": self.spice, "available": self.available}

    def states(self) -> dict[str, str]:
        return {a.allergen: a.state for a in self.allergens}


class EditBody(ItemBody):
    version: int


def _item(item: MenuItem, allergens: dict[str, str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"id": item.id, "name": item.name, "description": item.description, "category": item.category,
                           "price_paise": item.price_paise, "prep_minutes": item.prep_minutes, "veg": item.veg,
                           "spice": item.spice, "available": item.available, "version": item.version}
    if allergens is not None:
        out["allergens"] = [{"allergen": a, "state": s} for a, s in allergens.items()]
    return out


@router.post("/menu/{item_id}/availability")
async def availability(item_id: str, body: AvailabilityBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return _item(await menu.set_availability(p, item_id, body.available))


@router.post("/menu")
async def create(body: ItemBody, p: Principal = Depends(manager)) -> dict[str, Any]:
    item, allergens = await menu.save_item(p, None, body.fields(), body.states(), None)
    return _item(item, allergens)


@router.put("/menu/{item_id}")
async def edit(item_id: str, body: EditBody, p: Principal = Depends(manager)) -> dict[str, Any]:
    item, allergens = await menu.save_item(p, item_id, body.fields(), body.states(), body.version)
    return _item(item, allergens)


@router.post("/menu/import")
async def import_csv(request: Request, p: Principal = Depends(owner)) -> dict[str, int]:
    raw = await request.body()
    if len(raw) > MAX_BYTES:
        raise BadRequest("The file is larger than 1 MB.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadRequest("Save the file as CSV UTF-8 and try again.") from exc
    return await menu.import_csv(p, text)
