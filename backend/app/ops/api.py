"""/api/ops/* (PDS §10): read-only JSON for the /platform/ops pages, a script or a fleet view. Thin: every number
comes from app/ops/queries.py or insights.py. The only write is dismissing an insight.

Access (docs/OPS_PLAN.md, Q-E): `X-Ops-Token` must equal OPS_DASHBOARD_TOKEN; with no token configured every
route answers 404, so the dashboard is off rather than open. A platform_admin login replaces this later.
"""

import hmac
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.db import pool as pool_mod
from app.ops import insights as insights_mod
from app.ops import logs as logs_mod
from app.ops import queries
from app.ops.tracing import version


async def require_ops_token(x_ops_token: str | None = Header(default=None)) -> None:
    configured = get_settings().ops_dashboard_token
    if configured is None or not configured.get_secret_value():
        raise HTTPException(status_code=404)
    if not x_ops_token or not hmac.compare_digest(x_ops_token, configured.get_secret_value()):
        raise HTTPException(status_code=401, detail="ops token required")


router = APIRouter(prefix="/api/ops", dependencies=[Depends(require_ops_token)], include_in_schema=False)

From = Query(default=None, alias="from")
To = Query(default=None, alias="to")


def _w(frm: str | None, to: str | None, service: str | None) -> queries.Window:
    try:
        return queries.window(frm, to, service)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="from / to must be ISO timestamps") from exc


@router.get("/meta")
async def meta() -> dict[str, Any]:
    s = get_settings()
    ok = await queries.tables_ok()
    return {
        "standard": "PDS", "version": "1.0", "service": "restro", "env": s.app_env, "git_sha": version.git_sha(),
        "tracing_enabled": s.ops_tracing_enabled, "tables_ok": ok, "registry": queries.registry(),
        "label_keys": ["turn_kind", "llm_model", "event", "note_status"],
        "features": {"batches": False, "http": True, "llm": True, "resources": False, "rollups": False,
                     "cost": False, "logs": logs_mod.LOG_DIR.exists()},
        "budget": {"reply_p50_ms": 3000, "reply_p95_ms": 5000, "tap_reply_ms": 1000, "webhook_ms": 300},
    }


@router.get("/overview")
async def overview(frm: str | None = From, to: str | None = To, service: str | None = None) -> dict[str, Any]:
    return await queries.overview(_w(frm, to, service))  # the page loads /insights on its own


@router.get("/live")
async def live() -> dict[str, Any]:
    return await queries.live()


@router.get("/runs")
async def runs(request: Request, frm: str | None = From, to: str | None = To, service: str | None = None,
               before: datetime | None = None, limit: int = 50) -> dict[str, Any]:
    filters = {k: v for k, v in request.query_params.items() if k not in ("from", "to", "service", "before", "limit")}
    rows = await queries.runs(_w(frm, to, service), filters, before, limit)
    return {"runs": rows, "next": rows[-1]["started_at"] if len(rows) >= max(min(limit, 200), 1) else None}


@router.get("/runs/{run_id}")
async def run(run_id: str) -> dict[str, Any]:
    try:
        detail = await queries.run_detail(run_id)
    except Exception as exc:  # a malformed id
        raise HTTPException(status_code=404) from exc
    if detail is None:
        raise HTTPException(status_code=404)
    return detail


@router.get("/subjects/{subject_type}/{subject_id}/runs")
async def subject_runs(subject_type: str, subject_id: str) -> dict[str, Any]:
    return {"runs": await queries.subject_runs(subject_type, subject_id)}


@router.get("/endpoints")
async def endpoints(frm: str | None = From, to: str | None = To, service: str | None = None) -> dict[str, Any]:
    return {"endpoints": await queries.endpoints(_w(frm, to, service))}


@router.get("/failures")
async def failures(frm: str | None = From, to: str | None = To, service: str | None = None) -> dict[str, Any]:
    return {"failures": await queries.failures(_w(frm, to, service))}


@router.get("/degradations")
async def degradations(frm: str | None = From, to: str | None = To, service: str | None = None) -> dict[str, Any]:
    return {"degradations": await queries.degradation_counts(_w(frm, to, service))}


@router.get("/degradations/{reason}/runs")
async def degradation_runs(reason: str, frm: str | None = From, to: str | None = To,
                           service: str | None = None) -> dict[str, Any]:
    return {"runs": await queries.degradation_runs(_w(frm, to, service), reason)}


@router.get("/latency")
async def latency(frm: str | None = From, to: str | None = To, service: str | None = None, level: int = 0,
                  name: str | None = None, group_by: str | None = None) -> dict[str, Any]:
    w = _w(frm, to, service)
    if level not in (0, 1, 2, 3):
        raise HTTPException(status_code=400)
    try:
        if name:
            return await queries.latency_detail(w, name, level, group_by)
        return {"level": level, "rows": await queries.latency_table(w, level)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown group_by") from exc


@router.get("/latency/compare")
async def latency_compare(name: str, key: str, a: str, b: str, frm: str | None = From, to: str | None = To,
                          service: str | None = None) -> dict[str, Any]:
    try:
        return await queries.latency_compare(_w(frm, to, service), name, key, a, b)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown key") from exc


@router.get("/latency/cohorts")
async def cohorts(name: str, key: str, frm: str | None = From, to: str | None = To,
                  service: str | None = None) -> dict[str, Any]:
    try:
        return {"values": await queries.cohort_values(_w(frm, to, service), name, key)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown key") from exc


@router.get("/latency/scatter")
async def scatter(name: str, frm: str | None = From, to: str | None = To, service: str | None = None) -> dict[str, Any]:
    return {"points": await queries.latency_scatter(_w(frm, to, service), name)}


@router.get("/dependencies")
async def dependencies(frm: str | None = From, to: str | None = To, service: str | None = None) -> dict[str, Any]:
    return await queries.dependencies(_w(frm, to, service))


@router.get("/dependencies/{kind}/{target}")
async def dependency(kind: str, target: str, frm: str | None = From, to: str | None = To,
                     service: str | None = None) -> dict[str, Any]:
    return await queries.dependency_detail(_w(frm, to, service), kind, target)


@router.get("/efficiency")
async def efficiency(frm: str | None = From, to: str | None = To, service: str | None = None) -> dict[str, Any]:
    return await queries.efficiency(_w(frm, to, service))


@router.get("/insights")
async def insights(frm: str | None = From, to: str | None = To, service: str | None = None,
                   dismissed: bool = False) -> dict[str, Any]:
    w = insights_mod.default_window() if frm is None and to is None else _w(frm, to, service)
    return {"window": {"from": w.start, "to": w.end}, "findings": await insights_mod.insights(w, dismissed)}


class Dismiss(BaseModel):
    note: str | None = Field(default=None, max_length=500)


@router.post("/insights/{key}/dismiss")
async def dismiss(key: str, body: Dismiss) -> dict[str, bool]:
    if len(key) > 200:
        raise HTTPException(status_code=400)
    async with pool_mod.get_pool().acquire() as c:
        await c.execute("insert into ops_insight_state (finding_key, note) values ($1, $2) on conflict (finding_key) "
                        "do update set note = excluded.note, dismissed_at = now()", key, body.note)
    return {"dismissed": True}


@router.post("/insights/{key}/restore")
async def restore(key: str) -> dict[str, bool]:
    async with pool_mod.get_pool().acquire() as c:
        await c.execute("delete from ops_insight_state where finding_key = $1", key)
    return {"restored": True}


@router.get("/logs")
async def logs(level: str | None = None, logger: str | None = None, run_id: str | None = None,
               service: str | None = None, q: str | None = None, before: str | None = None, after: str | None = None,
               limit: int = 200) -> dict[str, Any]:
    return logs_mod.read(level=level, logger=logger, run_id=run_id, service=service, q=q, before=before, after=after,
                         limit=min(max(limit, 1), 500))
