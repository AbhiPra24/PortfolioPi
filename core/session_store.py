"""Manages ICICI Breeze API token persistence and retrieval.

Uses direct one-off asyncpg connections rather than the shared pool
(core/db.py) — save_session() is invoked from main.py's OAuth callback
HTTP server, which runs in its own thread with its own asyncio event loop,
and asyncpg pools/connections aren't safe to share across event loops.
Traffic on this table is a handful of calls a day, so pooling buys nothing.
"""

import logging

import asyncpg

from app_config import settings

logger = logging.getLogger(__name__)


async def get_session() -> str | None:
    try:
        conn = await asyncpg.connect(dsn=settings.database_url.get_secret_value())
        try:
            return await conn.fetchval("SELECT token FROM session_tokens LIMIT 1")
        finally:
            await conn.close()
    except Exception as e:
        logger.error(f"Failed to get session: {e}")
    return None


async def save_session(token: str):
    conn = await asyncpg.connect(dsn=settings.database_url.get_secret_value())
    try:
        async with conn.transaction():
            await conn.execute("DELETE FROM session_tokens")
            await conn.execute("INSERT INTO session_tokens (token) VALUES ($1)", token)
    finally:
        await conn.close()
