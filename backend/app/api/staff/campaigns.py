from datetime import datetime
from typing import Any, Literal

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.staff import router
from app.core.auth import Principal, owner
from app.domain import campaigns

Segment = Literal["all_opted_in", "lapsed", "regulars"]


class CampaignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    segment: Segment
    template_name: str = Field(min_length=1, max_length=60)
    offer_text: str | None = Field(default=None, max_length=120)
    discount_percent: int | None = Field(default=None, ge=1, le=90)
    code_prefix: str | None = Field(default=None, pattern=r"^[A-Z0-9]{2,10}$")
    valid_until: datetime | None = None


class PreviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    segment: Segment
    template_name: str = Field(min_length=1, max_length=60)


class ScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scheduled_at: datetime


@router.post("/campaigns/audience-preview")
async def audience_preview(body: PreviewBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await campaigns.preview(p, body.segment, body.template_name)


@router.post("/campaigns")
async def create(body: CampaignBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await campaigns.save_draft(p, None, body.model_dump())


@router.put("/campaigns/{campaign_id}")
async def edit(campaign_id: str, body: CampaignBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await campaigns.save_draft(p, campaign_id, body.model_dump())


@router.post("/campaigns/{campaign_id}/schedule")
async def schedule(campaign_id: str, body: ScheduleBody, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await campaigns.schedule(p, campaign_id, body.scheduled_at)


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel(campaign_id: str, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await campaigns.cancel(p, campaign_id)
