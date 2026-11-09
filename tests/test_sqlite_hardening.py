"""Unit tests verifying SQLite hardening PRAGMA settings."""

import os
import tempfile

import pytest

from core.database import (
    get_db_connection,
    get_sync_db_connection,
)


@pytest.mark.anyio
async def test_async_sqlite_hardening_pragmas():
    """Verify that get_db_connection and connect_db enforce WAL, NORMAL synchronous, busy_timeout >= 5000, and temp_store == MEMORY."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        async with get_db_connection(temp_db_path) as db:
            # Check journal_mode
            async with db.execute("PRAGMA journal_mode;") as cursor:
                row = await cursor.fetchone()
                journal_mode = row[0].lower() if row else None
                assert journal_mode == "wal", f"Expected WAL journal_mode, got {journal_mode}"

            # Check synchronous (NORMAL is 1, OFF is 0, FULL is 2, EXTRA is 3)
            async with db.execute("PRAGMA synchronous;") as cursor:
                row = await cursor.fetchone()
                synchronous = row[0] if row else None
                assert synchronous == 1, f"Expected synchronous == 1 (NORMAL), got {synchronous}"

            # Check busy_timeout
            async with db.execute("PRAGMA busy_timeout;") as cursor:
                row = await cursor.fetchone()
                busy_timeout = row[0] if row else None
                assert busy_timeout is not None and busy_timeout >= 5000, (
                    f"Expected busy_timeout >= 5000, got {busy_timeout}"
                )

            # Check temp_store (MEMORY is 2, DEFAULT is 0, FILE is 1)
            async with db.execute("PRAGMA temp_store;") as cursor:
                row = await cursor.fetchone()
                temp_store = row[0] if row else None
                assert temp_store == 2, f"Expected temp_store == 2 (MEMORY), got {temp_store}"

            # Check cache_size (-64000 is 64MB)
            async with db.execute("PRAGMA cache_size;") as cursor:
                row = await cursor.fetchone()
                cache_size = row[0] if row else None
                assert cache_size == -64000, f"Expected cache_size == -64000, got {cache_size}"
    finally:
        for ext in ["", "-wal", "-shm"]:
            path = temp_db_path + ext
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


def test_sync_sqlite_hardening_pragmas():
    """Verify that get_sync_db_connection and configure_sqlite3_connection enforce WAL and hardening PRAGMAs."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        temp_db_path = f.name

    try:
        conn = get_sync_db_connection(temp_db_path)
        try:
            cur = conn.cursor()

            # Check journal_mode
            cur.execute("PRAGMA journal_mode;")
            journal_mode = cur.fetchone()[0].lower()
            assert journal_mode == "wal", f"Expected WAL journal_mode, got {journal_mode}"

            # Check synchronous (NORMAL is 1)
            cur.execute("PRAGMA synchronous;")
            synchronous = cur.fetchone()[0]
            assert synchronous == 1, f"Expected synchronous == 1 (NORMAL), got {synchronous}"

            # Check busy_timeout
            cur.execute("PRAGMA busy_timeout;")
            busy_timeout = cur.fetchone()[0]
            assert busy_timeout >= 5000, f"Expected busy_timeout >= 5000, got {busy_timeout}"

            # Check temp_store (MEMORY is 2)
            cur.execute("PRAGMA temp_store;")
            temp_store = cur.fetchone()[0]
            assert temp_store == 2, f"Expected temp_store == 2 (MEMORY), got {temp_store}"

            # Check cache_size
            cur.execute("PRAGMA cache_size;")
            cache_size = cur.fetchone()[0]
            assert cache_size == -64000, f"Expected cache_size == -64000, got {cache_size}"

            cur.close()
        finally:
            conn.close()
    finally:
        for ext in ["", "-wal", "-shm"]:
            path = temp_db_path + ext
            if os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
