from telegram.ext import Application
from config import settings

async def broadcast_message(app: Application, message: str):
    for owner_id in settings.owner_ids_list:
        try:
            await app.bot.send_message(chat_id=owner_id, text=message)
        except Exception as e:
            # log error
            pass
