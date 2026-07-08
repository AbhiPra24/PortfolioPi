import asyncio
import logging
from bot.app import create_app
from core.database import init_db
from core.scheduler import start_scheduler

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def main():
    await init_db()
    app = create_app()
    start_scheduler(app)
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Run forever
    stop_signal = asyncio.Event()
    await stop_signal.wait()

if __name__ == "__main__":
    asyncio.run(main())
