from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class Membership:
    user_id: str
    cafe_id: str
    role: str
    active: bool
    display_name: str | None


async def get(conn: asyncpg.Connection, cafe_id: str, user_id: str) -> Membership | None:
    row = await conn.fetchrow(
        """select user_id::text, cafe_id::text, role, active, display_name
           from memberships where cafe_id = $1 and user_id = $2""",
        cafe_id, user_id,
    )
    return Membership(**dict(row)) if row else None


async def active_with_min_role(conn: asyncpg.Connection, cafe_id: str, roles: tuple[str, ...]) -> list[Membership]:
    rows = await conn.fetch(
        """select user_id::text, cafe_id::text, role, active, display_name
           from memberships where cafe_id = $1 and active and role = any($2::text[])""",
        cafe_id, list(roles),
    )
    return [Membership(**dict(r)) for r in rows]
