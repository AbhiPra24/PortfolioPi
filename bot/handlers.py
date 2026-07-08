from telegram import Update
from telegram.ext import ContextTypes
from .middleware import owner_only
from core.session_store import save_session
from .formatters import format_portfolio_message, format_signals_message
import aiosqlite
from config import settings

@owner_only
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM holdings_snapshot ORDER BY timestamp DESC LIMIT 50") as cur:
            rows = await cur.fetchall()
            
    holdings = [dict(row) for row in rows]
    msg = format_portfolio_message(holdings)
    await update.message.reply_text(msg, parse_mode='Markdown')

@owner_only
async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute("""
            SELECT stock_code, rsi14, macd_line, macd_signal, sma50, sma200, pct_from_52w_high, volume_ratio_20d, composite_score 
            FROM signals 
            WHERE timestamp >= datetime('now', '-1 day')
            ORDER BY composite_score DESC LIMIT 10
        """) as cur:
            rows = await cur.fetchall()
            
    msg = format_signals_message(rows)
    await update.message.reply_text(msg, parse_mode='Markdown')

@owner_only
async def refresh_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /refresh_session <token>")
        return
    token = context.args[0]
    await save_session(token)
    await update.message.reply_text("Session token saved! The daily digest will use this token.")

@owner_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "Commands:\n"
        "/portfolio - View current holdings\n"
        "/signals - View today's signals\n"
        "/refresh_session <token> - Update Breeze API session\n"
        "/help - Show this message"
    )
    await update.message.reply_text(help_text)
