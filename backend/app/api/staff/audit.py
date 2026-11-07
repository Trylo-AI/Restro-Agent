"""Owner audit log reads and the order history (contracts/openapi.yaml `/audit/*`, `/orders/{id}/history`).

Owner only (D15). Reads go through the API, not Supabase: labels, sentences and severity reasons come from the code
catalogue and raw `change` isn't exposed to direct client queries (decision 19). Bad query values → 400.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, Query, Response
from pydantic import StringConstraints

from app.api.staff import router
from app.core.auth import Principal, owner
from app.domain.audit import viewer
from app.domain.audit.viewer import AuditFilters

Severity = Literal["critical", "warning", "info"]
Category = Literal["orders", "calls", "chats", "menu", "customers", "campaigns", "offers", "team", "settings", "platform",
                   "other"]
Role = Literal["shift_manager", "manager", "owner", "platform_admin", "staff_via_ai", "system"]
ActionName = Annotated[str, StringConstraints(pattern=r"^[a-z_]+\.[a-z_]+$", max_length=60)]
UUID = Annotated[str, StringConstraints(pattern=r"^[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}$")]


def filters(
    action: Annotated[list[ActionName] | None, Query(max_length=40)] = None,
    category: Annotated[list[Category] | None, Query()] = None,
    severity: Annotated[list[Severity] | None, Query()] = None,
    actor_id: Annotated[list[UUID] | None, Query(max_length=20)] = None,
    role: Annotated[list[Role] | None, Query()] = None,
    order_number: Annotated[int | None, Query(ge=1, le=999_999_999)] = None,
    order_id: UUID | None = None,
    customer_id: UUID | None = None,
    conversation_id: UUID | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
) -> AuditFilters:
    return AuditFilters(
        actions=tuple(action or ()), categories=tuple(category or ()), severities=tuple(severity or ()),
        actor_ids=tuple(a.lower() for a in actor_id or ()), roles=tuple(role or ()), order_number=order_number,
        order_id=order_id.lower() if order_id else None, customer_id=customer_id.lower() if customer_id else None,
        conversation_id=conversation_id.lower() if conversation_id else None, since=from_, until=to, q=q,
    )


@router.get("/audit/entries")
async def entries(f: AuditFilters = Depends(filters),
                  before: Annotated[str | None, Query(max_length=120)] = None,
                  limit: Annotated[int, Query(ge=1, le=200)] = 50,
                  p: Principal = Depends(owner)) -> dict[str, Any]:
    return await viewer.entries(p, f, before, limit)


@router.get("/audit/facets")
async def facets(from_: Annotated[datetime | None, Query(alias="from")] = None, to: datetime | None = None,
                 p: Principal = Depends(owner)) -> dict[str, Any]:
    return await viewer.facets(p, from_, to)


@router.get("/audit/catalog")
async def catalog(p: Principal = Depends(owner)) -> dict[str, Any]:
    return viewer.catalog_json()


@router.get("/audit/export")
async def export(f: AuditFilters = Depends(filters), p: Principal = Depends(owner)) -> Response:
    text, local_date = await viewer.export_csv(p, f)
    return Response(text, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="audit-log-{local_date}.csv"'})


@router.get("/orders/{order_id}/history")
async def order_history(order_id: UUID, p: Principal = Depends(owner)) -> dict[str, Any]:
    return await viewer.order_history(p, order_id)
