"""Telegram bot initialization and long-polling entry point."""

from telegram import BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from app_config import settings

from .handlers import (
    action_plan_command,
    analyse_command,
    audit_command,
    cashflow_command,
    cashsweep_command,
    funds_command,
    help_command,
    menu_callback_handler,
    menu_command,
    orders_command,
    portfolio_command,
    price_command,
    refresh_session_command,
    session_message_handler,
    sgb_command,
    signals_command,
    sips_command,
    start_command,
    status_command,
    trades_command,
    watchlist_command,
)

COMMANDS = [
    BotCommand("start", "Welcome message"),
    BotCommand("menu", "Show interactive menu"),
    BotCommand("portfolio", "View current holdings"),
    BotCommand("signals", "View today's signals"),
    BotCommand("sips", "View active SIP book & quant guidance"),
    BotCommand("cashflow", "View dividend & cashflow projections"),
    BotCommand("sgb", "SGB secondary market arbitrage"),
    BotCommand("cashsweep", "Liquid float & cash drag sweeper"),
    BotCommand("analyse", "Full analysis for one ticker"),
    BotCommand("audit", "Run edge risk audit on active holdings"),
    BotCommand("action_plan", "View buy/sell/hold verdicts"),
    BotCommand("watchlist", "Manage watchlist"),
    BotCommand("price", "Get current stock price"),
    BotCommand("funds", "View available funds & margins"),
    BotCommand("trades", "View recent executed trades & brokerage"),
    BotCommand("orders", "View recent orders"),
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
    app.add_handler(CommandHandler("sips", sips_command))
    app.add_handler(CommandHandler("cashflow", cashflow_command))
    app.add_handler(CommandHandler("sgb", sgb_command))
    app.add_handler(CommandHandler("cashsweep", cashsweep_command))
    app.add_handler(CommandHandler("watchlist", watchlist_command))
    app.add_handler(CommandHandler("price", price_command))
    app.add_handler(CommandHandler("funds", funds_command))
    app.add_handler(CommandHandler("trades", trades_command))
    app.add_handler(CommandHandler("orders", orders_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("refresh_session", refresh_session_command))
    app.add_handler(CommandHandler("action_plan", action_plan_command))
    app.add_handler(CommandHandler("analyse", analyse_command))
    app.add_handler(CommandHandler("audit", audit_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), session_message_handler))

    return app


build_application = create_app
