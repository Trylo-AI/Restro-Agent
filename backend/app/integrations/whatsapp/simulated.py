"""Meta-format webhook payloads for the simulator (local only), signed exactly like Meta signs them."""

import json
import secrets
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.signature import sign


def new_wamid(direction: str) -> str:
    return f"wamid.SIM{direction}.{secrets.token_hex(12)}"


def _envelope(phone_number_id: str, value: dict[str, Any]) -> dict[str, Any]:
    value = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "919000000001", "phone_number_id": phone_number_id}, **value}
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "SIMWABA", "changes": [{"field": "messages", "value": value}]}]}


def message_payload(phone_number_id: str, wa_id: str, name: str | None, kind: str, *, text: str | None = None,
                    reply_id: str | None = None, reply_title: str | None = None,
                    list_reply: bool = False) -> tuple[dict[str, Any], str]:
    wamid = new_wamid("IN")
    msg: dict[str, Any] = {"from": wa_id, "id": wamid, "timestamp": str(int(time.time())), "type": kind}
    if kind == "text":
        msg["text"] = {"body": text or ""}
    elif kind == "interactive":
        key = "list_reply" if list_reply else "button_reply"
        msg["interactive"] = {"type": key, key: {"id": reply_id, "title": reply_title or ""}}
    elif kind in ("image", "audio", "sticker", "video", "document"):
        msg[kind] = {"id": f"media.{secrets.token_hex(6)}", "mime_type": "application/octet-stream"}
    value = {"contacts": [{"profile": {"name": name or "Sim"}, "wa_id": wa_id}], "messages": [msg]}
    return _envelope(phone_number_id, value), wamid


def status_payload(phone_number_id: str, wamid: str, status: str, recipient: str | None,
                   callback_data: str | None = None, category: str = "service",
                   billable: bool = False) -> dict[str, Any]:
    st: dict[str, Any] = {"id": wamid, "status": status, "timestamp": str(int(time.time())),
                          "recipient_id": recipient or "", "pricing": {"category": category, "billable": billable,
                                                                       "pricing_model": "PMP"}}
    if callback_data:
        st["biz_opaque_callback_data"] = callback_data
    return _envelope(phone_number_id, {"statuses": [st]})


async def post_signed(body: dict[str, Any]) -> int:
    settings = get_settings()
    raw = json.dumps(body, separators=(",", ":")).encode()
    headers = {"Content-Type": "application/json",
               "X-Hub-Signature-256": sign(raw, settings.meta_app_secret.get_secret_value())}
    # Generous: a slow network to the DB shows as a slow message in the simulator, not an error.
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(settings.api_url.rstrip("/") + "/webhooks/whatsapp", content=raw, headers=headers)
        return r.status_code
