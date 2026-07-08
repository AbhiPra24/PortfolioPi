"""Background APScheduler for daily data refresh and nightly database backups."""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from .data_refresh import run_breeze_sync, run_market_data_refresh

import aiosqlite
from app_config import settings

logger = logging.getLogger(__name__)

async def daily_digest_job(app: Application):
    logger.info("Running daily breeze sync & digest job...")
    await run_breeze_sync(app, silent=True)
    await run_market_data_refresh(app, send_digest=True)

async def market_data_job(app: Application):
    logger.info("Running market data refresh...")
    await run_market_data_refresh(app, send_digest=False)

async def process_refresh_requests_job(app: Application):
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            async with db.execute("SELECT id FROM refresh_requests WHERE processed_at IS NULL ORDER BY requested_at ASC LIMIT 1") as cur:
                row = await cur.fetchone()
            
            if row:
                req_id = row[0]
                logger.info(f"Processing manual refresh request {req_id}...")
                await run_breeze_sync(app, silent=True)
                await run_market_data_refresh(app, send_digest=True)
                await db.execute("UPDATE refresh_requests SET processed_at = CURRENT_TIMESTAMP WHERE id = ?", (req_id,))
                await db.commit()
    except Exception as e:
        logger.error(f"Error checking refresh_requests: {e}")

async def db_backup_job():
    try:
        import os
        from datetime import datetime
        import time
        
        backup_dir = os.path.join(os.path.dirname(settings.db_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)
        
        today_str = datetime.now().strftime("%Y%m%d")
        backup_file = os.path.join(backup_dir, f"portfoliopi_{today_str}.db")
        
        if os.path.exists(backup_file):
            os.remove(backup_file)
            
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute(f"VACUUM INTO '{backup_file}'")
            
        logger.info(f"Database backed up to {backup_file}")
        
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
    
    # Run market data refresh every 30 minutes during market hours (9:15 to 15:30) Monday to Friday
    # Easiest way with cron:
    scheduler.add_job(market_data_job, 'cron', day_of_week='mon-fri', hour='9-15', minute='0,30', args=[app], timezone='Asia/Kolkata')
    
    scheduler.add_job(db_backup_job, 'cron', hour=23, minute=30, timezone='Asia/Kolkata')
    scheduler.add_job(process_refresh_requests_job, 'interval', seconds=60, args=[app])
    scheduler.start()
