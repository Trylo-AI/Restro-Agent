"""Counter offers: lookup and redeem (contracts/openapi.yaml `/offers/*`, docs/backend.md#redemption).

Lookup is a POST so names, digits and codes never land in URLs or request logs. Shift manager and up.
"""

from typing import Any

from fastapi import Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from app.api.staff import router
from app.core.auth import Principal, shift_manager
from app.core.errors import BadRequest
from app.domain.campaigns import redemption


class LookupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str = Field(min_length=2, max_length=40)


class RedeemBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=3, max_length=20)
    bill_paise: int  # 100 to 10 000 000, checked by the domain so the 400 says "Check the amount"


@router.post("/offers/lookup")
async def lookup(body: LookupBody, p: Principal = Depends(shift_manager)) -> dict[str, Any]:
    return await redemption.lookup(p, body.q)


@router.post("/offers/redeem")
async def redeem(body: RedeemBody, p: Principal = Depends(shift_manager),
                 idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 80:
        raise BadRequest("Idempotency-Key header is required.")
    return await redemption.redeem_at_counter(p, body.code, body.bill_paise, idempotency_key)
