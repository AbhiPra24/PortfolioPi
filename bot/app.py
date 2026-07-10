"""Telegram bot initialization and long-polling entry point."""

from telegram import BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler

from app_config import settings

from .handlers import (
    funds_command,
    help_command,
    portfolio_command,
    price_command,
    refresh_session_command,
    signals_command,
    start_command,
    status_command,
    watchlist_command,
    action_plan_command,
    analyse_command,
    menu_command,
    menu_callback_handler,
)

COMMANDS = [
    BotCommand("start", "Welcome message"),
    BotCommand("menu", "Show interactive menu"),
    BotCommand("portfolio", "View current holdings"),
    BotCommand("signals", "View today's signals"),
    BotCommand("analyse", "Full analysis for one ticker"),
    BotCommand("action_plan", "View buy/sell/hold verdicts"),
    BotCommand("watchlist", "Manage watchlist"),
    BotCommand("price", "Get current stock price"),
    BotCommand("funds", "View available funds"),
    BotCommand("status", "System status & data health"),
    BotCommand("refresh_session", "Update Breeze API session"),
    BotCommand("help", "Show all commands"),
]

async def _post_init(app: Application):
    await app.bot.set_my_commands(COMMANDS)

def create_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token.get_secret_value()).post_init(_post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CallbackQueryHandler(menu_callback_handler))
    app.add_handler(CommandHandler("portfolio", portfolio_command))
    app.add_handler(CommandHandler("signals", signals_command))
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("funds", funds_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("refresh_session", refresh_session_command))
    app.add_handler(CommandHandler("action_plan", action_plan_command))
    app.add_handler(CommandHandler("analyse", analyse_command))
    app.add_handler(CommandHandler("help", help_command))

    return app
