"""SQLite database initialization and connection utilities."""

import logging

import aiosqlite

from app_config import settings

logger = logging.getLogger(__name__)

# No migration framework exists — new fields on an existing entity must be a new table (CREATE TABLE IF NOT EXISTS is a no-op on an already-created table), never an ALTER TABLE or a column addition to an existing CREATE statement.
async def init_db():
    logger.info("Initializing SQLite DB in WAL mode...")
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS holdings_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT UNIQUE,
                quantity INTEGER,
                average_price REAL,
                current_price REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT UNIQUE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS ohlcv_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT,
                date TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                UNIQUE(stock_code, date)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT,
                rsi14 REAL,
                macd_line REAL,
                macd_signal REAL,
                sma50 REAL,
                sma200 REAL,
                pct_from_52w_high REAL,
                volume_ratio_20d REAL,
                composite_score REAL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS job_heartbeats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS refresh_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                processed_at DATETIME
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS stage_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT,
                date TEXT,
                stage INTEGER,
                sma_150 REAL,
                slope REAL,
                UNIQUE(stock_code, date)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS stock_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT UNIQUE,
                action TEXT,
                rationale TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS ticker_mapping (
                stock_code TEXT PRIMARY KEY,
                nse_symbol TEXT,
                isin TEXT,
                resolved_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS backfill_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT,
                requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                processed_at DATETIME,
                status TEXT,
                error TEXT
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS data_health (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT,
                source TEXT,
                status TEXT,
                message TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.commit()
