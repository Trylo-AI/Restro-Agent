"""Meta webhook (docs/backend.md#webhook). Signature on raw bytes first; then store, queue, 200."""

import hmac
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from app.core import signature
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories import webhook_events
from app.db.tx import conn
from app.domain.conversation import ingress
from app.integrations.whatsapp.parser import parse

log = get_logger(__name__)
router = APIRouter(tags=["webhooks"])


@router.get("/webhooks/whatsapp", response_model=None)
async def verify(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    expected = get_settings().meta_verify_token.get_secret_value()
    if mode == "subscribe" and token and hmac.compare_digest(token, expected) and challenge:
        return PlainTextResponse(challenge)
    return PlainTextResponse("forbidden", status_code=403)


@router.post("/webhooks/whatsapp")
async def receive(request: Request) -> Response:
    raw = await request.body()
    if not signature.verify(raw, request.headers.get("X-Hub-Signature-256"),
                            get_settings().meta_app_secret.get_secret_value()):
        log.warning("webhook_bad_signature")
        return JSONResponse({"error": {"code": "unauthenticated", "message": "Bad signature."}}, status_code=401)
    try:
        body = json.loads(raw)
    except ValueError:
        log.warning("webhook_bad_json")
        return JSONResponse({"status": "ignored"})
    async with conn() as c:
        event_id = await webhook_events.insert(c, body, True)

    batch = parse(body)
    for message in batch.messages:
        await ingress.ingest_message(message)
    for status in batch.statuses:
        await ingress.ingest_status(status)
    for other in batch.other:
        log.info("webhook_account_event", field=other.field)  # template / quality / account handling: later phases

    async with conn() as c:
        await webhook_events.mark_processed(c, event_id)
    return JSONResponse({"status": "ok"})
