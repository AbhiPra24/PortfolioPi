"""Docker healthcheck for the bot container.

Replaces the old `sqlite3 ... | grep 1` CMD-SHELL check — connects directly
via asyncpg (already a dependency) rather than requiring a psql/postgresql-client
CLI just for this one check.
"""

import asyncio
import sys

import asyncpg

from app_config import settings


async def check():
    conn = await asyncpg.connect(dsn=settings.database_url.get_secret_value())
    try:
        val = await conn.fetchval(
            "SELECT 1 FROM job_heartbeats WHERE timestamp >= NOW() - INTERVAL '24 hours' LIMIT 1"
        )
    finally:
        await conn.close()
    sys.exit(0 if val is not None else 1)


if __name__ == "__main__":
    asyncio.run(check())
