import logging

import aiosqlite

from app_config import settings

logger = logging.getLogger(__name__)

async def get_session() -> str | None:
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            async with db.execute("SELECT token FROM session_tokens LIMIT 1") as cursor:
                row = await cursor.fetchone()
                if row:
                    return row[0]
    except Exception as e:
        logger.error(f"Failed to get session: {e}")
    return None

async def save_session(token: str):
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("DELETE FROM session_tokens")
        await db.execute("INSERT INTO session_tokens (token) VALUES (?)", (token,))
        await db.commit()
