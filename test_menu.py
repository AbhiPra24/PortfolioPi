import asyncio

from telegram import Bot

from app_config import settings
from bot.app import COMMANDS


async def main():
    bot = Bot(settings.telegram_bot_token.get_secret_value())
    await bot.set_my_commands(COMMANDS)
    print("Commands set!")

asyncio.run(main())
