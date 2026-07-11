"""One-time data migration from SQLite to Supabase (Postgres).

Run via: docker compose run --rm bot python -m scripts.migrate_to_supabase [--truncate]

Reads every row from the live SQLite file, writes it to Supabase preserving
explicit id values, then fixes each table's identity sequence to continue
from the migrated max(id). Safe to rerun with --truncate (wipes destination
tables first, then reloads) — used both for the Phase 2 rehearsal and the
final authoritative load right before the Phase 3 cutover.

No foreign keys exist between these tables, so migration order doesn't matter.
"""

import argparse
import asyncio
import logging
import sqlite3
from datetime import datetime, timezone

import asyncpg

from app_config import settings

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def parse_ts(value):
    """SQLite stores CURRENT_TIMESTAMP as naive UTC text 'YYYY-MM-DD HH:MM:SS'."""
    if value is None:
        return None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


# (table_name, columns in migration order, columns that need SQLite-text -> datetime
# parsing, order-by column for the SELECT). Tables keyed by stock_code (no surrogate
# `id` column) pass has_id=False so no identity-sequence fixup is attempted.
TABLES = [
    ("holdings_snapshot", ["id", "stock_code", "quantity", "average_price", "current_price", "timestamp"], ["timestamp"], "id", True),
    ("watchlist", ["id", "stock_code"], [], "id", True),
    ("ohlcv_cache", ["id", "stock_code", "date", "open", "high", "low", "close", "volume"], [], "id", True),
    ("signals", ["id", "stock_code", "rsi14", "macd_line", "macd_signal", "sma50", "sma200",
                 "pct_from_52w_high", "volume_ratio_20d", "composite_score", "timestamp"], ["timestamp"], "id", True),
    ("job_heartbeats", ["id", "job_name", "timestamp"], ["timestamp"], "id", True),
    ("session_tokens", ["id", "token", "timestamp"], ["timestamp"], "id", True),
    ("refresh_requests", ["id", "requested_at", "processed_at"], ["requested_at", "processed_at"], "id", True),
    ("stage_history", ["id", "stock_code", "date", "stage", "sma_150", "slope"], [], "id", True),
    ("stock_actions", ["id", "stock_code", "action", "rationale", "timestamp"], ["timestamp"], "id", True),
    ("ticker_mapping", ["stock_code", "nse_symbol", "isin", "resolved_at"], ["resolved_at"], "stock_code", False),
    ("backfill_requests", ["id", "stock_code", "requested_at", "processed_at", "status", "error"],
     ["requested_at", "processed_at"], "id", True),
    ("data_health", ["id", "stock_code", "source", "status", "message", "timestamp"], ["timestamp"], "id", True),
    ("portfolio_value_history", ["id", "timestamp", "total_invested", "total_current_value", "total_pnl"],
     ["timestamp"], "id", True),
    ("stock_metadata", ["stock_code", "sector", "industry", "updated_at"], ["updated_at"], "stock_code", False),
]


async def migrate_table(pg_conn, sqlite_conn, table, columns, ts_columns, order_col, has_id, truncate):
    cur = sqlite_conn.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {order_col}")
    rows = cur.fetchall()

    if truncate:
        await pg_conn.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")

    if not rows:
        logger.info(f"{table}: 0 rows in SQLite, nothing to migrate")
        return 0

    processed_rows = []
    for row in rows:
        row_dict = dict(zip(columns, row))
        for ts_col in ts_columns:
            row_dict[ts_col] = parse_ts(row_dict[ts_col])
        processed_rows.append(tuple(row_dict[c] for c in columns))

    placeholders = ", ".join(f"${i + 1}" for i in range(len(columns)))
    insert_sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    await pg_conn.executemany(insert_sql, processed_rows)

    if has_id:
        await pg_conn.execute(
            f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
            f"COALESCE((SELECT MAX(id) FROM {table}), 0) + 1, false)"
        )

    logger.info(f"{table}: migrated {len(rows)} rows")
    return len(rows)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--truncate", action="store_true",
        help="Truncate destination tables before loading (makes the run idempotent/rerunnable)",
    )
    args = parser.parse_args()

    dsn = settings.database_url.get_secret_value()
    if not dsn:
        raise SystemExit("DATABASE_URL is not set in .env")

    sqlite_path = settings.db_path
    logger.info(f"Reading from SQLite: {sqlite_path}")
    sqlite_conn = sqlite3.connect(sqlite_path)

    logger.info("Connecting to Supabase...")
    pg_conn = await asyncpg.connect(dsn=dsn)

    try:
        total = 0
        for table, columns, ts_columns, order_col, has_id in TABLES:
            total += await migrate_table(pg_conn, sqlite_conn, table, columns, ts_columns, order_col, has_id, args.truncate)
        logger.info(f"Migration complete. {total} total row(s) migrated across {len(TABLES)} tables.")
    finally:
        sqlite_conn.close()
        await pg_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
