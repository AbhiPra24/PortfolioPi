"""Background APScheduler for daily data refresh and nightly database backups."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from core.db import get_pool

from .data_refresh import run_breeze_sync, run_market_data_refresh

logger = logging.getLogger(__name__)

async def daily_digest_job(app: Application):
    logger.info("Running daily breeze sync & digest job...")
    await run_breeze_sync(app, silent=True)
    await run_market_data_refresh(app, send_digest=True)

async def market_data_refresh_job(app: Application):
    logger.info("Running market data refresh...")
    await run_market_data_refresh(app, send_digest=False)

async def process_refresh_requests_job(app: Application):
    try:
        pool = get_pool()
        async with pool.acquire() as db:
            row = await db.fetchrow(
                "SELECT id FROM refresh_requests WHERE processed_at IS NULL ORDER BY requested_at ASC LIMIT 1"
            )

            if row:
                req_id = row["id"]
                logger.info(f"Processing manual refresh request {req_id}...")
                await run_breeze_sync(app, silent=True)
                await run_market_data_refresh(app, send_digest=True)
                await db.execute("UPDATE refresh_requests SET processed_at = NOW() WHERE id = $1", req_id)
    except Exception as e:
        logger.error(f"Error checking refresh_requests: {e}")

async def process_backfill_requests_job(app: Application):
    from core.data_refresh import backfill_ticker_history
    from core.providers import YFinanceProvider
    try:
        pool = get_pool()
        async with pool.acquire() as db:
            row = await db.fetchrow(
                "SELECT id, stock_code FROM backfill_requests WHERE processed_at IS NULL ORDER BY requested_at ASC LIMIT 1"
            )
            if row:
                req_id, stock_code = row["id"], row["stock_code"]
                logger.info(f"Processing backfill request {req_id} for {stock_code}...")
                try:
                    await backfill_ticker_history(db, stock_code, YFinanceProvider())
                    await db.execute(
                        "UPDATE backfill_requests SET processed_at = NOW(), status = 'ok' WHERE id = $1", req_id
                    )
                except Exception as e:
                    await db.execute(
                        "UPDATE backfill_requests SET processed_at = NOW(), status = 'failed', error = $1 WHERE id = $2",
                        str(e), req_id,
                    )
                    await db.execute(
                        "INSERT INTO data_health (stock_code, source, status, message) VALUES ($1, 'yfinance', 'failed', $2)",
                        stock_code, str(e),
                    )
    except Exception as e:
        logger.error(f"Error checking backfill_requests: {e}")

async def db_backup_job():
    try:
        import gzip
        import os
        import time
        from datetime import datetime

        pool = get_pool()
        backup_dir = os.path.join("data", "backups")
        os.makedirs(backup_dir, exist_ok=True)

        today_str = datetime.now().strftime("%Y%m%d")
        tables = [
            "holdings_snapshot", "watchlist", "ohlcv_cache", "signals", "job_heartbeats",
            "session_tokens", "refresh_requests", "stage_history", "stock_actions",
            "ticker_mapping", "backfill_requests", "data_health", "portfolio_value_history",
            "stock_metadata",
        ]

        async with pool.acquire() as db:
            for table in tables:
                backup_file = os.path.join(backup_dir, f"{table}_{today_str}.csv.gz")
                if os.path.exists(backup_file):
                    os.remove(backup_file)
                with gzip.open(backup_file, "wb") as f:
                    await db.copy_from_query(f"SELECT * FROM {table}", output=f, format="csv", header=True)

        logger.info(f"Database backed up to {backup_dir} ({len(tables)} tables)")

        # Cleanup backups older than 7 days
        now = time.time()
        for f in os.listdir(backup_dir):
            f_path = os.path.join(backup_dir, f)
            if os.stat(f_path).st_mtime < now - 7 * 86400:
                os.remove(f_path)
                logger.info(f"Deleted old backup {f_path}")

    except Exception as e:
        logger.error(f"Database backup failed: {e}")

def start_scheduler(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_digest_job, 'cron', hour=8, minute=0, args=[app], timezone='Asia/Kolkata')

    scheduler.add_job(market_data_refresh_job, 'interval', minutes=60, args=[app])

    scheduler.add_job(db_backup_job, 'cron', hour=23, minute=30, timezone='Asia/Kolkata')
    scheduler.add_job(process_refresh_requests_job, 'interval', seconds=60, args=[app])
    scheduler.add_job(process_backfill_requests_job, 'interval', seconds=20, args=[app])
    scheduler.start()
