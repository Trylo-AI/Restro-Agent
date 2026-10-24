"""Wipe all app data in the DEV database (keeps schema, migrations and auth users). Then run generate.py.

Refuses to run unless APP_ENV is local/test and the only cafés are the seed's synthetic ones, so it can never
empty a database that holds a real café.
"""

import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings  # noqa: E402

SEED_PHONE_NUMBER_IDS = {"100000000000001", "100000000000002"}


async def main() -> None:
    s = get_settings()
    if not s.is_local:
        raise SystemExit(f"refusing to reset: APP_ENV={s.app_env}")
    dsn = (s.database_url_session or s.database_url).get_secret_value()
    conn = await asyncpg.connect(dsn, statement_cache_size=0)
    try:
        ids = {r["phone_number_id"] for r in await conn.fetch("select phone_number_id from cafes")}
        if not ids <= SEED_PHONE_NUMBER_IDS:
            raise SystemExit("refusing to reset: found a café that the seed didn't create")
        tables = [r["tablename"] for r in await conn.fetch(
            "select tablename from pg_tables where schemaname = 'public' order by tablename")]
        # Append-only triggers fire on DELETE, not TRUNCATE; TRUNCATE is the dev-only way out.
        await conn.execute("truncate " + ", ".join(f'"{t}"' for t in tables) + " restart identity cascade")
        print(f"wiped {len(tables)} tables")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
