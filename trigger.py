import asyncio

from bot.app import create_app
from core.data_refresh import run_market_data_refresh
from core.database import init_db


async def main():
    await init_db()
    app = create_app()
    await run_market_data_refresh(app, silent=True)
    print("Market data refresh done.")


asyncio.run(main())
