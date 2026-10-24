"""Simulator fault hooks (local only). Every function is a no-op unless the simulator is on."""

import httpx

from app.core.config import get_settings


async def consume_fault(kind: str) -> str | None:
    """`send` · `llm` · `turn`. Each fault armed in the simulator fires once."""
    s = get_settings()
    if s.app_env != "local" or not s.simulator_on or not s.simulator_token:
        return None
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.post(s.api_url.rstrip("/") + "/dev/sim/_faults/consume", json={"kind": kind},
                                  headers={"X-Sim-Token": s.simulator_token.get_secret_value()})
            return r.json().get("fault") if r.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def push_event(wa_id: str, kind: str, **data: object) -> None:
    """Behind-the-scenes event log entry for the simulator (ids and timings only)."""
    s = get_settings()
    if s.app_env != "local" or not s.simulator_on or not s.simulator_token:
        return
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            await client.post(s.api_url.rstrip("/") + "/dev/sim/_events", json={"wa_id": wa_id, "kind": kind, "data": data},
                              headers={"X-Sim-Token": s.simulator_token.get_secret_value()})
    except httpx.HTTPError:
        return
