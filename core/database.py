"""SQLite database initialization and schema setup."""

import logging
import os

import aiosqlite

from app_config import settings

logger = logging.getLogger(__name__)


# No migration framework exists — new fields on an existing entity must be a new table (CREATE TABLE IF NOT EXISTS is a no-op on an already-created table), never an ALTER TABLE or a column addition to an existing CREATE statement.
async def init_db():
    logger.info(f"Initializing SQLite DB in WAL mode at {settings.db_path}...")
    db_dir = os.path.dirname(settings.db_path)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

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

        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_value_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                total_invested REAL,
                total_current_value REAL,
                total_pnl REAL
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS stock_metadata (
                stock_code TEXT PRIMARY KEY,
                sector TEXT,
                industry TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS sip_book (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                stock_code TEXT,
                asset_category TEXT NOT NULL,
                monthly_sip REAL NOT NULL,
                execution_day INTEGER DEFAULT 31,
                status TEXT DEFAULT 'ACTIVE',
                quant_stage INTEGER DEFAULT 2,
                target_action TEXT DEFAULT 'CONTINUE',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS dividend_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                stock_code TEXT,
                asset_type TEXT NOT NULL,
                expected_dpu REAL NOT NULL,
                payout_frequency TEXT,
                last_ex_date TEXT,
                next_expected_payout TEXT,
                est_annual_cashflow REAL,
                yield_on_cost REAL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS trades_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT,
                trade_id TEXT UNIQUE,
                trade_date TEXT,
                stock_code TEXT,
                action TEXT,
                quantity INTEGER,
                average_cost REAL,
                ltp REAL,
                brokerage_amount REAL,
                total_taxes REAL,
                product_type TEXT,
                exchange_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS orders_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE,
                order_datetime TEXT,
                stock_code TEXT,
                action TEXT,
                order_type TEXT,
                quantity INTEGER,
                price REAL,
                average_price REAL,
                status TEXT,
                exchange_code TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Ensure critical unique constraints / indexes exist for upserts
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_holdings_snapshot_stock_code ON holdings_snapshot(stock_code);")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ticker_mapping_stock_code ON ticker_mapping(stock_code);")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_history_trade_id ON trades_history(trade_id);")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_history_order_id ON orders_history(order_id);")

        await db.commit()

    logger.info("Database tables initialized.")
