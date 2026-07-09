"""Telegram bot command handlers for processing user requests."""

import logging

import aiosqlite
from telegram import Update
from telegram.ext import ContextTypes

from app_config import settings
from core.breeze_client import BreezeClient
from core.session_store import get_session, save_session

from .formatters import format_portfolio_message, format_signals_message
from .middleware import owner_only

logger = logging.getLogger(__name__)

async def get_breeze_client() -> BreezeClient | None:
    token = await get_session()
    if not token:
        return None
    try:
        return BreezeClient(token)
    except Exception as e:
        logger.error(f"Failed to init breeze client: {e}")
        return None

@owner_only
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "🤖 <b>Welcome to PortfolioPi Bot!</b>\n\n"
        "I will help you monitor your portfolio and execute your algorithms.\n"
        "Use /help to see all available commands."
    )
    await update.message.reply_text(msg, parse_mode='HTML')

@owner_only
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM holdings_snapshot ORDER BY timestamp DESC LIMIT 50") as cur:
            rows = await cur.fetchall()

    holdings = [dict(row) for row in rows]
    msg = format_portfolio_message(holdings)
    await update.message.reply_text(msg, parse_mode='HTML')

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
    await update.message.reply_text(msg, parse_mode='HTML')

@owner_only
async def refresh_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /refresh_session &lt;token&gt;", parse_mode='HTML')
        return
    token = context.args[0]

    # Immediately validate token
    try:
        breeze = BreezeClient(token)
        details = breeze.get_customer_details()
        if details.get("Success"):
            await save_session(token)
            await update.message.reply_text("<b>Token Validated. Starting Breeze Sync...</b>\nFull refresh started in background.", parse_mode='HTML')
            
            from core.data_refresh import run_breeze_sync
            import asyncio
            asyncio.create_task(run_breeze_sync(context.application, silent=False))
        else:
            await update.message.reply_text(f"<b>Failed to validate token.</b> API Response: {details.get('Error', 'Unknown error')}", parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"<b>Error validating token:</b> {e}", parse_mode='HTML')

@owner_only
async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /watchlist add &lt;TICKER&gt;, /watchlist remove &lt;TICKER&gt;, /watchlist list", parse_mode='HTML')
        return

    action = context.args[0].lower()

    if action == "list":
        async with aiosqlite.connect(settings.db_path) as db:
            async with db.execute("SELECT stock_code FROM watchlist") as cur:
                rows = await cur.fetchall()
        if not rows:
            await update.message.reply_text("Watchlist is empty.", parse_mode='HTML')
            return
        msg = "<b>Watchlist:</b>\n" + "\n".join([f"- <code>{r[0]}</code>" for r in rows])
        await update.message.reply_text(msg, parse_mode='HTML')
        return

    if len(context.args) < 2:
        await update.message.reply_text(f"Usage: /watchlist {action} &lt;TICKER&gt;", parse_mode='HTML')
        return

    ticker = context.args[1].upper()

    if action == "add":
        breeze = await get_breeze_client()
        if not breeze:
            await update.message.reply_text("Could not init Breeze client. Is session token set?", parse_mode='HTML')
            return

        try:
            res = breeze.get_names(exchange_code="NSE", stock_code=ticker)
            if res.get("Status") == 500:
                await update.message.reply_text(f"Validation failed for <code>{ticker}</code>. It might not be a valid NSE stock.", parse_mode='HTML')
                return
        except Exception as e:
            await update.message.reply_text(f"Validation failed for <code>{ticker}</code>: {e}", parse_mode='HTML')
            return

        async with aiosqlite.connect(settings.db_path) as db:
            try:
                await db.execute("INSERT INTO watchlist (stock_code) VALUES (?)", (ticker,))
                await db.commit()
                await update.message.reply_text(f"Added <code>{ticker}</code> to watchlist.", parse_mode='HTML')
            except aiosqlite.IntegrityError:
                await update.message.reply_text(f"<code>{ticker}</code> is already in watchlist.", parse_mode='HTML')

    elif action == "remove":
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute("DELETE FROM watchlist WHERE stock_code = ?", (ticker,))
            await db.commit()
            await update.message.reply_text(f"Removed <code>{ticker}</code> from watchlist.", parse_mode='HTML')
    else:
        await update.message.reply_text("Unknown action. Use add, remove, or list.", parse_mode='HTML')

@owner_only
async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /price &lt;TICKER&gt;", parse_mode='HTML')
        return
    ticker = context.args[0].upper()

    breeze = await get_breeze_client()
    if not breeze:
        await update.message.reply_text("Could not init Breeze client.", parse_mode='HTML')
        return

    try:
        res = breeze.get_quotes(stock_code=ticker, exchange_code="NSE", product_type="cash")
        if res.get("Success") and len(res["Success"]) > 0:
            data = res["Success"][0]
            ltp = data.get("ltp", "N/A")
            change = data.get("change", "N/A")
            await update.message.reply_text(f"<b>{ticker}</b>: ₹{ltp} ({change})", parse_mode='HTML')
        else:
            await update.message.reply_text(f"Could not fetch price for <code>{ticker}</code>. Response: {res}", parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"Error fetching price: {e}", parse_mode='HTML')

@owner_only
async def funds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    breeze = await get_breeze_client()
    if not breeze:
        await update.message.reply_text("Could not init Breeze client.", parse_mode='HTML')
        return

    try:
        res = breeze.get_funds()
        if res.get("Success"):
            msg = "<b>Funds Summary:</b>\n"
            for fund in res["Success"]:
                msg += f"Total Limit: ₹{fund.get('TotalLimit', 'N/A')}\n"
                msg += f"Available Limit: ₹{fund.get('AvailableLimit', 'N/A')}\n"
                msg += f"Allocated Funds: ₹{fund.get('AllocatedFunds', 'N/A')}\n"
            await update.message.reply_text(msg, parse_mode='HTML')
        else:
            await update.message.reply_text(f"Failed to fetch funds. {res}", parse_mode='HTML')
    except Exception as e:
        await update.message.reply_text(f"Error fetching funds: {e}", parse_mode='HTML')

@owner_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute("SELECT job_name, timestamp FROM job_heartbeats ORDER BY timestamp DESC LIMIT 5") as cur:
            heartbeats = await cur.fetchall()

        async with db.execute("SELECT timestamp FROM session_tokens LIMIT 1") as cur:
            session_row = await cur.fetchone()

    msg = "<b>System Status</b>\n\n"

    if session_row:
        msg += f"<b>Session Last Updated:</b> {session_row[0]}\n"
    else:
        msg += "<b>Session:</b> Not set\n"

    msg += "\n<b>Recent Job Heartbeats:</b>\n"
    if heartbeats:
        for hb in heartbeats:
            msg += f"- <code>{hb[0]}</code>: {hb[1]}\n"
    else:
        msg += "No recent heartbeats found.\n"

    await update.message.reply_text(msg, parse_mode='HTML')

@owner_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>Commands:</b>\n"
        "/start - Welcome message\n"
        "/portfolio - View current holdings\n"
        "/signals - View today's signals\n"
        "/action_plan - View portfolio action classification\n"
        "/watchlist list|add|remove &lt;TICKER&gt; - Manage watchlist\n"
        "/price &lt;TICKER&gt; - Get current stock price\n"
        "/funds - View available funds\n"
        "/status - View system status\n"
        "/refresh_session &lt;token&gt; - Update Breeze API session\n"
        "/help - Show this message"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

@owner_only
async def action_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute("SELECT stock_code, action, rationale FROM stock_actions ORDER BY action") as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text("No action plan data available.", parse_mode='HTML')
        return

    msg = "<b>Portfolio Action Plan:</b>\n"
    for r in rows:
        if r[1] != "HOLD":
            msg += f"\n<b>{r[0]}</b>: {r[1]}\n<i>{r[2]}</i>\n"
    
    if len(msg) > 4000:
        msg = msg[:4000] + "\n... (truncated)"
        
    await update.message.reply_text(msg, parse_mode='HTML')
