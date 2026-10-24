"""Console request check (docs/data-and-api.md#console-request-check), steps 1-4.

1. Verify the Supabase JWT against the project's JWKS (signature, exp, aud, iss) → 401.
2. Café from `X-Cafe-Id`; the domain then loads the resource with that cafe_id, so another café's row is 404.
   Missing or malformed header → 404.
3. Active membership for (user, café) → else 403. platform_admin support mode arrives later → 403 for now.
4. Role rank ≥ the endpoint minimum → else 403.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import jwt
from fastapi import Depends, Header

from app.core.config import get_settings
from app.core.errors import Forbidden, NotFound, Unauthenticated
from app.db.repositories import memberships
from app.db.repositories.memberships import Membership

ROLE_RANK = {"shift_manager": 1, "manager": 2, "owner": 3}
ALLOWED_ALGS = ("ES256", "RS256", "EdDSA")


@dataclass(frozen=True)
class Principal:
    user_id: str
    cafe_id: str
    role: str
    display_name: str | None
    support: bool = False

    def at_least(self, role: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[role]


class JwksCache:
    """Project signing keys, cached 10 min; an unknown kid triggers at most one refetch per minute."""

    def __init__(self, url: str, ttl: float = 600, min_refetch: float = 60) -> None:
        self.url, self.ttl, self.min_refetch = url, ttl, min_refetch
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    async def fetch(self) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(self.url)
            r.raise_for_status()
            return dict(r.json())

    async def key(self, kid: str | None) -> Any:
        now = time.monotonic()
        stale = now - self._fetched_at > self.ttl
        unknown = kid not in self._keys and now - self._fetched_at > self.min_refetch
        if not self._keys or stale or unknown:
            jwks = await self.fetch()
            self._keys = {k.get("kid"): jwt.PyJWK(k).key for k in jwks.get("keys", [])}
            self._fetched_at = now
        if kid in self._keys:
            return self._keys[kid]
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise Unauthenticated()


_jwks: JwksCache | None = None


def jwks() -> JwksCache:
    global _jwks
    if _jwks is None:
        _jwks = JwksCache(get_settings().jwks_url)
    return _jwks


async def verify_token(token: str, cache: JwksCache | None = None) -> dict[str, Any]:
    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg")
        if alg not in ALLOWED_ALGS:
            raise Unauthenticated()
        key = await (cache or jwks()).key(header.get("kid"))
        claims: dict[str, Any] = jwt.decode(
            token, key, algorithms=[alg], audience=settings.supabase_jwt_audience, issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except (jwt.PyJWTError, httpx.HTTPError, ValueError) as exc:
        raise Unauthenticated() from exc
    return claims


MembershipLoader = Callable[[str, str], Awaitable[Membership | None]]


async def load_membership_from_db(cafe_id: str, user_id: str) -> Membership | None:
    from app.db.tx import conn

    async with conn() as c:
        return await memberships.get(c, cafe_id, user_id)


def membership_loader() -> MembershipLoader:
    """Dependency so tests can swap the DB lookup."""
    return load_membership_from_db


def _parse_cafe_id(value: str | None) -> str:
    try:
        return str(uuid.UUID(value or ""))
    except ValueError as exc:
        raise NotFound() from exc


def require_role(min_role: str) -> Callable[..., Awaitable[Principal]]:
    if min_role not in ROLE_RANK:
        raise ValueError(min_role)

    async def dependency(
        authorization: str | None = Header(default=None),
        x_cafe_id: str | None = Header(default=None),
        load: MembershipLoader = Depends(membership_loader),
    ) -> Principal:
        if not authorization or not authorization.startswith("Bearer "):
            raise Unauthenticated()
        claims = await verify_token(authorization.removeprefix("Bearer ").strip())
        cafe_id = _parse_cafe_id(x_cafe_id)
        membership = await load(cafe_id, str(claims["sub"]))
        if membership is None or not membership.active:
            raise Forbidden()
        if ROLE_RANK.get(membership.role, 0) < ROLE_RANK[min_role]:
            raise Forbidden()
        return Principal(user_id=membership.user_id, cafe_id=cafe_id, role=membership.role,
                         display_name=membership.display_name)

    return dependency


shift_manager = require_role("shift_manager")
manager = require_role("manager")
owner = require_role("owner")
