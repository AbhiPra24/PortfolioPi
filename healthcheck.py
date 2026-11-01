"""Docker healthcheck for the bot container.

Checks SQLite database readability and integrity via aiosqlite / sqlite3,
verifying that the database file exists, is readable, passes integrity check,
and has had a job heartbeat within the past 24 hours.
"""

import asyncio
import os
import sys

import aiosqlite

from app_config import settings


async def check():
    db_path = settings.db_path
    if not os.path.exists(db_path):
        sys.exit(1)

    try:
        async with aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True) as db:
            async with db.execute("PRAGMA quick_check;") as cur:
                row = await cur.fetchone()
                if not row or row[0] != "ok":
                    sys.exit(1)

            async with db.execute(
                "SELECT 1 FROM job_heartbeats WHERE timestamp >= datetime('now', '-24 hours') LIMIT 1"
            ) as cur:
                val = await cur.fetchone()

        # Healthy if DB is intact and has had a heartbeat within 24h
        sys.exit(0 if val is not None else 1)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(check())
