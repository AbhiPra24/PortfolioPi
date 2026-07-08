from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.ext import Application
import logging
import aiosqlite
from config import settings
from .session_store import get_session
from .breeze_client import BreezeClient, SessionExpiredError

logger = logging.getLogger(__name__)

async def record_heartbeat(job_name: str):
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("INSERT INTO job_heartbeats (job_name) VALUES (?)", (job_name,))
        await db.commit()

async def daily_digest_job(app: Application):
    logger.info("Running daily digest job...")
    token = await get_session()
    if not token:
        # TODO broadcast warning
        return
        
    try:
        breeze = BreezeClient(token)
        # TODO fetch holdings, run screener, broadcast
        
        await record_heartbeat('daily_digest')
    except SessionExpiredError:
        logger.error("Session expired.")
        # TODO broadcast warning

def start_scheduler(app: Application):
    scheduler = AsyncIOScheduler()
    scheduler.add_job(daily_digest_job, 'cron', hour=8, minute=0, args=[app], timezone='Asia/Kolkata')
    # For testing, you might want to run it on startup or specific intervals
    # scheduler.add_job(daily_digest_job, 'interval', minutes=5, args=[app])
    scheduler.start()
