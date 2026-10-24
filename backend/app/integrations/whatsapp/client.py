"""WhatsApp transports. Only domain/outbound calls `send`; wa-ack calls `mark_read`.

`meta`: Cloud API over HTTPS. `simulator`: local only; the step row already holds the payload, so the
transport only returns a fake wamid and posts status webhooks back through the real webhook route.
"""

import asyncio
import secrets as pysecrets
from typing import Any, Protocol

import httpx

from app.core import secrets
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.cafes import Cafe
from app.integrations.whatsapp import simulated
from app.integrations.whatsapp.errors import SendClass, SendError, classify_http

log = get_logger(__name__)


class Transport(Protocol):
    async def send(self, cafe: Cafe, to: str, body: dict[str, Any], callback_data: str) -> str: ...
    async def mark_read(self, cafe: Cafe, wamid: str, wa_id: str) -> None: ...


class MetaTransport:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _url(self, cafe: Cafe) -> str:
        return f"https://graph.facebook.com/{get_settings().meta_graph_version}/{cafe.phone_number_id}/messages"

    def _headers(self, cafe: Cafe) -> dict[str, str]:
        if not cafe.token_ref:
            raise SendError(SendClass.TOKEN_INVALID, "no_token_ref")
        return {"Authorization": f"Bearer {secrets.resolve(cafe.token_ref)}"}

    async def send(self, cafe: Cafe, to: str, body: dict[str, Any], callback_data: str) -> str:
        data = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                "biz_opaque_callback_data": callback_data, **body}
        timeout = httpx.Timeout(10.0, connect=3.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                r = await client.post(self._url(cafe), json=data, headers=self._headers(cafe))
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise SendError(SendClass.NOT_SENT_RETRY, "connect") from exc
        except httpx.HTTPError as exc:  # read timeout, dropped connection: it may have gone
            raise SendError(SendClass.AMBIGUOUS, "timeout") from exc
        if r.status_code >= 300:
            code = None
            try:
                code = str((r.json().get("error") or {}).get("code"))
            except ValueError:
                pass
            cls = classify_http(r.status_code, code)
            if cls is SendClass.TOKEN_INVALID and cafe.token_ref:
                secrets.forget(cafe.token_ref)
            raise SendError(cls, code or str(r.status_code))
        return str(r.json()["messages"][0]["id"])

    async def mark_read(self, cafe: Cafe, wamid: str, wa_id: str) -> None:
        data = {"messaging_product": "whatsapp", "status": "read", "message_id": wamid,
                "typing_indicator": {"type": "text"}}
        async with httpx.AsyncClient(timeout=5, transport=self._transport) as client:
            await client.post(self._url(cafe), json=data, headers=self._headers(cafe))


class SimulatorTransport:
    """Local only. Faults armed in the simulator are consumed here (one per arm)."""

    def __init__(self) -> None:
        self._background: set[asyncio.Task[None]] = set()

    async def _dev(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        s = get_settings()
        token = s.simulator_token.get_secret_value() if s.simulator_token else ""
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.request(method, s.api_url.rstrip("/") + path, headers={"X-Sim-Token": token}, **kw)
            return dict(r.json()) if r.status_code == 200 else {}

    async def _fault(self, kind: str) -> str | None:
        try:
            return (await self._dev("POST", "/dev/sim/_faults/consume", json={"kind": kind})).get("fault")
        except httpx.HTTPError:
            return None

    def _later(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _statuses(self, cafe: Cafe, wamid: str, to: str, callback: str, category: str, billable: bool,
                        delay: float = 0.3) -> None:
        await asyncio.sleep(delay)
        for status, pause in (("sent", 0.0), ("delivered", 0.5)):
            await asyncio.sleep(pause)
            await simulated.post_signed(simulated.status_payload(cafe.phone_number_id, wamid, status, to, callback,
                                                                  category, billable))

    async def send(self, cafe: Cafe, to: str, body: dict[str, Any], callback_data: str) -> str:
        fault = await self._fault("send")
        if fault == "send_429":
            raise SendError(SendClass.NOT_SENT_RETRY, "130429")
        wamid = simulated.new_wamid("OUT")
        is_template = body.get("type") == "template"
        category, billable = ("utility", True) if is_template else ("service", False)
        if fault == "send_timeout":
            # The message did go; we just never heard back. The status webhook carries our row id, so
            # send_check can resolve the ambiguous row to `sent` without resending.
            self._later(self._statuses(cafe, wamid, to, callback_data, category, billable, delay=2.0))
            raise SendError(SendClass.AMBIGUOUS, "timeout")
        self._later(self._statuses(cafe, wamid, to, callback_data, category, billable))
        return wamid

    async def mark_read(self, cafe: Cafe, wamid: str, wa_id: str) -> None:
        try:
            await self._dev("POST", "/dev/sim/_events", json={"wa_id": wa_id, "kind": "typing"})
        except httpx.HTTPError:
            log.warning("sim_typing_failed")


_transport: Transport | None = None


def transport() -> Transport:
    global _transport
    if _transport is None:
        _transport = SimulatorTransport() if get_settings().wa_transport == "simulator" else MetaTransport()
    return _transport


def set_transport(t: Transport | None) -> None:
    global _transport
    _transport = t


def callback_token() -> str:
    return pysecrets.token_hex(4)
