"""Utility for broadcasting proactive messages to authorized Telegram users."""

import logging
import os

from telegram.ext import Application

from app_config import settings

logger = logging.getLogger(__name__)

async def broadcast_message(app: Application, message: str):
    for owner_id in settings.owner_ids_list:
        try:
            # Telegram max length is 4096. Chunk it if needed.
            if len(message) > 4000:
                chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
                for chunk in chunks:
                    await app.bot.send_message(chat_id=owner_id, text=chunk, parse_mode='HTML')
            else:
                await app.bot.send_message(chat_id=owner_id, text=message, parse_mode='HTML')
        except Exception:
            logger.exception(f"Failed to send message to {owner_id}")
            os.makedirs("logs", exist_ok=True)
            with open("logs/undelivered_alerts.log", "a") as f:
                f.write(f"Failed to send to {owner_id}: {message}\n")
