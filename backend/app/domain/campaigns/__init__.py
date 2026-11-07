"""Campaigns (docs/backend.md#campaigns). Owner only. Audience = segment ∩ latest consent `opted_in`.

draft → scheduled (approved marketing template, at least one recipient, a future time, number connected) →
sending (`campaign_start` snapshots one recipient per eligible customer with a unique code; later opt-ins aren't
added) → completed (nothing queued or deferred), or cancelled from scheduled or sending (open-decision default:
queued recipients are not sent).

Sender: one `campaign-send` task per recipient, spaced below the number's throughput, within the day's limit from
the messaging tier (over it → `deferred`, never dropped; `campaign_resume` re-queues them when the day has room).
Each send re-checks the campaign, the recipient, consent and the template; the send layer checks consent again.

Codes are valid through the café-local day of `valid_until`: the message tells that date and the verdict uses it
(`campaigns.redemption`). Every draft save writes `campaign.draft` (ids, field names, the percentage; no offer text).
"""

import math
import secrets
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.auth import Principal
from app.core.errors import AppError, BadRequest, NotFound
from app.core.logging import get_logger
from app.db.repositories import cafes
from app.db.repositories import campaigns as repo
from app.db.tx import conn, tx
from app.domain import audit, outbound
from app.integrations import tasks
from app.integrations.whatsapp import messages as m
from app.ops import tracing

log = get_logger(__name__)

DRAFT_FIELDS = ("name", "segment", "template_name", "offer_text", "discount_percent", "code_prefix", "valid_until")
DEFAULT_DAILY_LIMIT = 250  # Meta's limit before the business is verified; the number's tier raises it
TIERS = {"TIER_250": 250, "TIER_1K": 1000, "TIER_10K": 10_000, "TIER_100K": 100_000, "TIER_UNLIMITED": 1_000_000}
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
SEND_SPACING_S = 0.5


class CampaignStateError(AppError):
    status, code, default_message = 422, "invalid_transition", "This campaign can't do that now."


def _actor(p: Principal) -> audit.Actor:
    return audit.Actor(p.user_id, p.role)


def daily_limit(health: dict[str, Any]) -> int:
    return TIERS.get(str(health.get("messaging_tier") or ""), DEFAULT_DAILY_LIMIT)


def new_code(prefix: str | None) -> str:
    return f"{prefix or 'OFFER'}-{''.join(secrets.choice(CODE_CHARS) for _ in range(3))}"


# ── owner actions ─────────────────────────────────────────────────────────

async def save_draft(p: Principal, campaign_id: str | None, f: dict[str, Any]) -> dict[str, Any]:
    async with tx() as c:
        if campaign_id is None:
            camp = await repo.insert(c, p.cafe_id, f)
            changed = [k for k in DRAFT_FIELDS if f.get(k) is not None]
        else:
            existing = await repo.get(c, p.cafe_id, campaign_id)
            if existing is None:
                raise NotFound()
            updated = await repo.update_draft(c, p.cafe_id, campaign_id, f)
            if updated is None:
                raise CampaignStateError("Only a draft can be edited.")
            camp = updated
            changed = [k for k in DRAFT_FIELDS if getattr(existing, k) != getattr(camp, k)]
        await audit.write(c, p.cafe_id, _actor(p), "campaign.draft",
                          {"campaign_id": camp.id, "new": campaign_id is None, "fields": sorted(changed),
                           "discount_percent": camp.discount_percent})
    return camp.as_dict()


async def preview(p: Principal, segment: str, template_name: str) -> dict[str, Any]:
    async with conn() as c:
        in_segment, will = await repo.audience_counts(c, p.cafe_id, segment)
        health = await cafes.health(c, p.cafe_id)
        template = await repo.template_status(c, p.cafe_id, template_name)
    limit = daily_limit(health)
    return {"in_segment": in_segment, "excluded": in_segment - will, "will_receive": will, "daily_limit": limit,
            "days_needed": max(1, math.ceil(will / limit)) if will else 0, "estimated_cost": None,
            "quality": health.get("quality"), "template_status": template or "missing"}


async def schedule(p: Principal, campaign_id: str, when: datetime) -> dict[str, Any]:
    if when <= datetime.now(UTC):
        raise CampaignStateError("Pick a time in the future.")
    async with tx() as c:
        camp = await repo.get(c, p.cafe_id, campaign_id, lock=True)
        if camp is None:
            raise NotFound()
        if camp.status != "draft":
            raise CampaignStateError("Only a draft can be scheduled.")
        if await repo.template_status(c, p.cafe_id, camp.template_name) != "approved":
            raise CampaignStateError("The template isn't approved by WhatsApp yet.")
        cafe = await cafes.get(c, p.cafe_id)
        if cafe is None or cafe.status == "disconnected":
            raise CampaignStateError("WhatsApp is disconnected: nothing can be sent.")
        _, will = await repo.audience_counts(c, p.cafe_id, camp.segment)
        if will == 0:
            raise CampaignStateError("Nobody in this audience has opted in to offers.")
        if not camp.code_prefix:
            raise BadRequest("Add a code prefix so customers get a code to redeem.")
        updated = await repo.set_status(c, p.cafe_id, campaign_id, ("draft",), "scheduled", when)
        await audit.write(c, p.cafe_id, _actor(p), "campaign.schedule",
                          {"campaign_id": campaign_id, "scheduled_at": when.isoformat(), "audience": will})
    return updated.as_dict()  # type: ignore[union-attr]


async def cancel(p: Principal, campaign_id: str) -> dict[str, Any]:
    async with tx() as c:
        updated = await repo.set_status(c, p.cafe_id, campaign_id, ("scheduled", "sending"), "cancelled")
        if updated is None:
            if await repo.get(c, p.cafe_id, campaign_id) is None:
                raise NotFound()
            raise CampaignStateError("Only a scheduled or sending campaign can be cancelled.")
        await audit.write(c, p.cafe_id, _actor(p), "campaign.cancel", {"campaign_id": campaign_id})
    return updated.as_dict()


# ── jobs ──────────────────────────────────────────────────────────────────

async def _queue_within_limit(cafe_id: str, campaign_id: str) -> int:
    """Queue `campaign-send` tasks for as many recipients as today's limit allows; the rest wait as `deferred`."""
    async with tx() as c:
        room = max(0, daily_limit(await cafes.health(c, cafe_id)) - await repo.sent_today(c, cafe_id))
        deferred = await repo.take(c, cafe_id, campaign_id, "deferred", room)
        await repo.set_recipients(c, cafe_id, deferred, "deferred", "queued")
        queued = await repo.take(c, cafe_id, campaign_id, "queued", 1_000_000)
        to_send, over = queued[:room], queued[room:]
        await repo.set_recipients(c, cafe_id, over, "queued", "deferred")
    for i, rid in enumerate(to_send):
        await tasks.tasks().enqueue("campaign-send", {"cafe_id": cafe_id, "recipient_id": rid}, delay_s=i * SEND_SPACING_S,
                                    name=f"campaign-send:{rid}")
    tracing.count(queued=len(to_send), deferred=len(over))
    return len(to_send)


async def start_due() -> dict[str, int]:
    """`campaign_start`: snapshot recipients of every due scheduled campaign, then queue them."""
    async with conn() as c:
        due = await repo.due(c)
    started = queued = 0
    for cafe_id, campaign_id in due:
        async with tx() as c:
            camp = await repo.get(c, cafe_id, campaign_id, lock=True)
            if camp is None or camp.status != "scheduled":
                continue
            for customer_id in await repo.audience(c, cafe_id, camp.segment):
                for _ in range(5):  # a code collision is rare; try again with new characters
                    if await repo.add_recipient(c, cafe_id, campaign_id, customer_id, new_code(camp.code_prefix)) is not None:
                        break
            await repo.set_status(c, cafe_id, campaign_id, ("scheduled",), "sending")
        started += 1
        queued += await _queue_within_limit(cafe_id, campaign_id)
    return {"started": started, "queued": queued}


async def resume_deferred() -> dict[str, int]:
    """`campaign_resume`: deferred recipients go out when the day has room again; finished campaigns complete."""
    async with conn() as c:
        live = await repo.sending(c)
    queued = completed = 0
    for cafe_id, campaign_id in live:
        queued += await _queue_within_limit(cafe_id, campaign_id)
        completed += await _complete_if_done(cafe_id, campaign_id)
    return {"queued": queued, "completed": completed}


async def _complete_if_done(cafe_id: str, campaign_id: str) -> int:
    async with tx() as c:
        if await repo.remaining(c, cafe_id, campaign_id) == 0:
            return 1 if await repo.set_status(c, cafe_id, campaign_id, ("sending",), "completed") else 0
    return 0


# ── the send task ─────────────────────────────────────────────────────────

def offer_message(camp: repo.Campaign, first_name: str | None, code: str, tz: str = "Asia/Kolkata") -> m.Template:
    """"valid till" is the café-local date of `valid_until`: the last day the code works (decision D14a)."""
    until = camp.valid_until.astimezone(ZoneInfo(tz)).strftime("%d %b") if camp.valid_until else "soon"
    return m.Template(camp.template_name, [first_name or "there", camp.offer_text or "a little something on us", code, until])


async def send_one(cafe_id: str, recipient_id: str) -> str:
    async with conn() as c:
        r = await repo.recipient(c, cafe_id, recipient_id)
        if r is None:
            return "missing"
        camp = await repo.get(c, cafe_id, r["campaign_id"])
        template = await repo.template_status(c, cafe_id, camp.template_name) if camp else None
        tz = await c.fetchval("select timezone from cafes where id = $1", cafe_id)
    if camp is None or camp.status != "sending" or r["status"] != "queued":
        return "skipped"  # cancelled, already sent, or deferred since
    if r["consent"] != "opted_in" or template != "approved":
        async with conn() as c:
            await repo.mark_recipient(c, cafe_id, recipient_id, "failed")
        tracing.count(blocked=1)
        await _complete_if_done(cafe_id, camp.id)
        return "blocked"
    first = (r["name"] or "").split(" ")[0] or None
    status = await outbound.send(cafe_id, outbound.Recipient(customer_id=r["customer_id"]), offer_message(camp, first, r["code"], tz),
                                 f"campaign:{recipient_id}", outbound.Purpose.MARKETING)
    async with conn() as c:
        wamid = await c.fetchval("select wamid from wa_messages where cafe_id = $1 and idempotency_key = $2",
                                 cafe_id, f"campaign:{recipient_id}")
        await repo.mark_recipient(c, cafe_id, recipient_id, "sent" if status in ("sent", "ambiguous") else "failed", wamid)
    await _complete_if_done(cafe_id, camp.id)
    return status or "dropped"
