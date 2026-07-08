"""Telegram bot initialization and long-polling entry point."""

from telegram.ext import Application, CommandHandler

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
)


def create_app() -> Application:
    app = Application.builder().token(settings.telegram_bot_token.get_secret_value()).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("portfolio", portfolio_command))
    app.add_handler(CommandHandler("signals", signals_command))
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("funds", funds_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("refresh_session", refresh_session_command))
    app.add_handler(CommandHandler("action_plan", action_plan_command))
    app.add_handler(CommandHandler("help", help_command))

    return app
