"""Consent (docs/backend.md#consent). Append-only; the newest row is the state. The AI never touches it.

This slice: opt-out (routing rule 1). The opt-in ask arrives with feedback in Phase 5.
"""

import asyncpg

from app.db.repositories import consents


async def opt_out(conn: asyncpg.Connection, cafe_id: str, customer_id: str, source: str) -> bool:
    """STOP keyword, "Stop offers" button or error 131050. Returns False when already opted out."""
    if source not in ("keyword", "button", "meta_block"):
        raise ValueError(source)
    if await consents.latest_status(conn, cafe_id, customer_id) == "opted_out":
        return False
    await consents.append(conn, cafe_id, customer_id, "opted_out", source)
    return True
