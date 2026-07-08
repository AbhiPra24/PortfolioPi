from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes

from app_config import settings


def owner_only(func):
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in settings.owner_ids_list:
            await update.message.reply_text("Unauthorized access.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper
