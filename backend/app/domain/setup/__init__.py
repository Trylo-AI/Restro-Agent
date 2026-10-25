"""Owner setup (docs/console.md#setup): café settings, tables and QR text, team.

Settings save in one call (opening hours, last-order cutoff, tone, feedback delay, café alert number). Tables get
`qr_text` "Hi, I'm at Table <n>" from their label. Team: invite by email through Supabase Auth (the invite email
comes from Supabase), change role, deactivate. Owner rows can't be deactivated; the last active owner can't be
demoted. A change takes effect on the person's next request (auth re-reads the membership every request).
Audit rows hold what changed, never an email or a full phone number.
"""

import re
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Any

import asyncpg
import httpx

from app.core.auth import Principal
from app.core.config import get_settings
from app.core.errors import BadRequest, NotFound, VersionConflict
from app.db.tx import tx
from app.domain import audit

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _actor(p: Principal) -> audit.Actor:
    return audit.Actor(p.user_id, p.role)


# ── settings ──────────────────────────────────────────────────────────────

def validate_hours(hours: dict[str, Any]) -> dict[str, list[list[str]]]:
    """{"mon": [["08:00", "23:00"]], ...}; an end before the start runs past midnight; empty = always open."""
    out: dict[str, list[list[str]]] = {}
    for day, windows in hours.items():
        if day not in DAYS or not isinstance(windows, list) or len(windows) > 3:
            raise BadRequest("Opening hours: use mon–sun with up to 3 windows a day.")
        clean = []
        for w in windows:
            if not (isinstance(w, list) and len(w) == 2 and all(isinstance(t, str) and TIME.match(t) for t in w)) or w[0] == w[1]:
                raise BadRequest("Opening hours: each window is two different times like 08:00 and 23:00.")
            clean.append([w[0], w[1]])
        out[day] = clean
    return out


async def save_settings(p: Principal, body: dict[str, Any]) -> dict[str, Any]:
    async with tx() as c:
        row = await c.fetchrow("select opening_hours, cutoff_minutes, settings from cafes where id = $1 for update", p.cafe_id)
        if row is None:
            raise NotFound()
        settings = dict(row["settings"])
        hours, cutoff = row["opening_hours"], row["cutoff_minutes"]
        changed: list[str] = []
        if "opening_hours" in body:
            new_hours = validate_hours(body["opening_hours"] or {})
            if new_hours != hours:
                hours = new_hours
                changed.append("opening_hours")
        if "cutoff_minutes" in body and body["cutoff_minutes"] != cutoff:
            cutoff = body["cutoff_minutes"]
            changed.append("cutoff_minutes")
        for key in ("tone", "feedback_delay_minutes", "alert_phone"):
            if key in body and body[key] != settings.get(key):
                settings[key] = body[key]
                changed.append(key)
        if not changed:
            return _settings_out(hours, cutoff, settings)
        await c.execute("update cafes set opening_hours = $2, cutoff_minutes = $3, settings = $4 where id = $1",
                        p.cafe_id, hours, cutoff, settings)
        await audit.write(c, p.cafe_id, _actor(p), "settings.update", {"fields": changed})
    return _settings_out(hours, cutoff, settings)


def _settings_out(hours: dict[str, Any], cutoff: int, settings: dict[str, Any]) -> dict[str, Any]:
    return {"opening_hours": hours, "cutoff_minutes": cutoff, "tone": settings.get("tone", "warm"),
            "feedback_delay_minutes": int(settings.get("feedback_delay_minutes") or 30),
            "alert_phone": settings.get("alert_phone")}


# ── tables ────────────────────────────────────────────────────────────────

def qr_text(label: str) -> str:
    """"T5" → "Hi, I'm at Table 5"; "Patio 2" → "Hi, I'm at Table Patio 2"."""
    m = re.fullmatch(r"[Tt]\s*(\d+)", label.strip())
    return f"Hi, I'm at Table {m.group(1) if m else label.strip()}"


async def save_table(p: Principal, table_id: str | None, label: str, area: str | None, seats: int | None) -> dict[str, Any]:
    label = label.strip()
    try:
        async with tx() as c:
            if table_id is None:
                row = await c.fetchrow(
                    """insert into tables (cafe_id, label, area, seats, qr_text) values ($1, $2, $3, $4, $5)
                       returning id::text, label, area, seats, qr_text""", p.cafe_id, label, area, seats, qr_text(label))
            else:
                row = await c.fetchrow(
                    """update tables set label = $3, area = $4, seats = $5, qr_text = $6 where cafe_id = $1 and id = $2
                       returning id::text, label, area, seats, qr_text""", p.cafe_id, table_id, label, area, seats,
                    qr_text(label))
                if row is None:
                    raise NotFound()
            await audit.write(c, p.cafe_id, _actor(p), "table.update",
                              {"table_id": row["id"], "label": label, "new": table_id is None})
    except asyncpg.UniqueViolationError as exc:
        raise VersionConflict(f"There's already a table called {label}.") from exc
    return dict(row)


# ── team ──────────────────────────────────────────────────────────────────

@dataclass
class Member:
    id: str
    user_id: str
    role: str
    active: bool
    display_name: str | None


Inviter = Callable[[str, str | None], Awaitable[str]]


async def supabase_invite(email: str, display_name: str | None) -> str:
    """Invite by email through Supabase Auth; an existing user is reused. Returns the auth user id."""
    s = get_settings()
    key = s.supabase_secret_key.get_secret_value()
    base = s.supabase_url.rstrip("/")
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(f"{base}/auth/v1/invite", headers=headers,
                              json={"email": email, "data": {"display_name": display_name} if display_name else {}})
        if r.status_code < 300:
            return str(r.json()["id"])
    async with tx() as c:  # already registered: find them (the backend role can read auth.users)
        existing = await c.fetchval("select id::text from auth.users where lower(email) = lower($1)", email)
    if existing:
        return str(existing)
    raise BadRequest("Couldn't send the invite. Check the email address and try again.")


_inviter: Inviter = supabase_invite


def set_inviter(fn: Inviter | None) -> None:
    """Tests replace the Auth call (it sends a real email)."""
    global _inviter
    _inviter = fn or supabase_invite


async def invite(p: Principal, email: str, role: str, display_name: str | None) -> dict[str, Any]:
    user_id = await _inviter(email.strip().lower(), display_name)
    async with tx() as c:
        row = await c.fetchrow(
            """insert into memberships (user_id, cafe_id, role, display_name) values ($1, $2, $3, $4)
               on conflict (user_id, cafe_id) do nothing returning id::text, user_id::text, role, active, display_name""",
            user_id, p.cafe_id, role, display_name)
        if row is None:
            raise VersionConflict("This person is already on the team.")
        await audit.write(c, p.cafe_id, _actor(p), "team.invite", {"membership_id": row["id"], "role": role})
    return dict(row)


async def update_member(p: Principal, membership_id: str, role: str | None, active: bool | None) -> dict[str, Any]:
    async with tx() as c:
        row = await c.fetchrow("""select id::text, user_id::text, role, active, display_name from memberships
                                  where cafe_id = $1 and id = $2 for update""", p.cafe_id, membership_id)
        if row is None:
            raise NotFound()
        m = Member(**dict(row))
        if active is False and m.role == "owner":
            raise BadRequest("An owner can't be deactivated. Change the role first.")
        if role is not None and m.role == "owner" and role != "owner" and m.active:
            owners = await c.fetchval("select count(*) from memberships where cafe_id = $1 and role = 'owner' and active",
                                      p.cafe_id)
            if owners <= 1:
                raise BadRequest("The café needs at least one active owner.")
        new_role = role if role is not None else m.role
        new_active = active if active is not None else m.active
        if (new_role, new_active) == (m.role, m.active):
            return asdict(m)
        await c.execute("update memberships set role = $3, active = $4 where cafe_id = $1 and id = $2",
                        p.cafe_id, membership_id, new_role, new_active)
        await audit.write(c, p.cafe_id, _actor(p), "team.update",
                          {"membership_id": membership_id, "role": new_role, "active": new_active})
        m.role, m.active = new_role, new_active
    return asdict(m)
