"""Load-test café (docs/rules.md#load-tests): its own phone_number_id, menu and 60 tables, so a run never touches
Kaapi House or its console. Synthetic data only. Safe to run again.

    backend/.venv/Scripts/python.exe loadtest/seed.py
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "supabase" / "seed"))

from generate import MENU, seed_menu, upsert_cafe  # noqa: E402

from app.core.config import get_settings  # noqa: E402

LOAD_PHONE_NUMBER_ID = "100000000000077"
TABLES = 60


async def seed(conn: asyncpg.Connection) -> str:
    async with conn.transaction():
        cafe_id = await upsert_cafe(conn, "Loadtest Café", LOAD_PHONE_NUMBER_ID, "+91 90000 00077", "919999000977")
        # No opening hours = always open, never near closing: a run at midnight still orders.
        await conn.execute("update cafes set opening_hours = '{}'::jsonb, cutoff_minutes = 0 where id = $1", cafe_id)
        for i in range(1, TABLES + 1):
            await conn.execute(
                """insert into tables (cafe_id, label, area, seats, qr_text) values ($1, $2, 'Inside', 4, $3)
                   on conflict (cafe_id, label) do update set qr_text = excluded.qr_text""",
                cafe_id, f"L{i}", f"Hi, I'm at Table L{i}")
        await seed_menu(conn, cafe_id, MENU)
        for name in ("order_update", "new_order_alert"):
            await conn.execute(
                """insert into templates (cafe_id, name, category, language, status, quality)
                   values ($1, $2, 'utility', 'en', 'approved', 'High')
                   on conflict (cafe_id, name, language) do update set status = 'approved'""", cafe_id, name)
    return cafe_id


async def main() -> None:
    s = get_settings()
    if s.app_env not in ("local", "staging"):
        raise SystemExit(f"refusing to seed a load café in {s.app_env}")
    conn = await asyncpg.connect((s.database_url_session or s.database_url).get_secret_value(), statement_cache_size=0)
    try:
        await seed(conn)
        print(f"Loadtest Café seeded (phone_number_id {LOAD_PHONE_NUMBER_ID}, {TABLES} tables)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
