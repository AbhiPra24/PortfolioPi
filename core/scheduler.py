import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application

from .data_refresh import run_refresh_pipeline

logger = logging.getLogger(__name__)

async def daily_digest_job(app: Application):
    logger.info("Running daily digest job...")
    await run_refresh_pipeline(app)

def start_scheduler(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_digest_job, 'cron', hour=8, minute=0, args=[app], timezone='Asia/Kolkata')
    # For testing, you might want to run it on startup or specific intervals
    # scheduler.add_job(daily_digest_job, 'interval', minutes=5, args=[app])
    scheduler.start()
