"""Telegram bot command handlers for processing user requests."""

import logging

import asyncpg
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from core.breeze_client import BreezeClient
from core.db import get_pool
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
    pool = get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT * FROM holdings_snapshot ORDER BY timestamp DESC LIMIT 50")

    holdings = [dict(row) for row in rows]
    msg = format_portfolio_message(holdings)
    await update.message.reply_text(msg, parse_mode='HTML')

@owner_only
async def signals_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("""
            SELECT stock_code, rsi14, macd_line, macd_signal, sma50, sma200, pct_from_52w_high, volume_ratio_20d, composite_score
            FROM signals
            WHERE timestamp >= NOW() - INTERVAL '1 day'
            ORDER BY composite_score DESC LIMIT 10
        """)

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
        details = breeze.get_customer_details(api_session=token)
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
    pool = get_pool()

    if action == "list":
        async with pool.acquire() as db:
            rows = await db.fetch("SELECT stock_code FROM watchlist")
        if not rows:
            await update.message.reply_text("Watchlist is empty.", parse_mode='HTML')
            return
        msg = "<b>Watchlist:</b>\n" + "\n".join([f"- <code>{r['stock_code']}</code>" for r in rows])
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

        async with pool.acquire() as db:
            try:
                await db.execute("INSERT INTO watchlist (stock_code) VALUES ($1)", ticker)
                await update.message.reply_text(f"Added <code>{ticker}</code> to watchlist. Starting backfill...", parse_mode='HTML')

                async def run_backfill():
                    from core.providers import YFinanceProvider
                    from core.data_refresh import backfill_ticker_history
                    conn_pool = get_pool()
                    async with conn_pool.acquire() as conn:
                        try:
                            await backfill_ticker_history(conn, ticker, YFinanceProvider())
                        except Exception as e:
                            logger.error(f"Watchlist add background backfill failed for {ticker}: {e}")

                import asyncio
                asyncio.create_task(run_backfill())
            except asyncpg.UniqueViolationError:
                await update.message.reply_text(f"<code>{ticker}</code> is already in watchlist.", parse_mode='HTML')

    elif action == "remove":
        async with pool.acquire() as db:
            await db.execute("DELETE FROM watchlist WHERE stock_code = $1", ticker)
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
    pool = get_pool()
    async with pool.acquire() as db:
        heartbeats = await db.fetch("SELECT job_name, timestamp FROM job_heartbeats ORDER BY timestamp DESC LIMIT 5")
        session_row = await db.fetchrow("SELECT timestamp FROM session_tokens LIMIT 1")
        failures = await db.fetch("SELECT stock_code, source, message, timestamp FROM data_health ORDER BY timestamp DESC LIMIT 5")

    msg = "<b>System Status</b>\n\n"

    if session_row:
        msg += f"<b>Session Last Updated:</b> {session_row['timestamp']}\n"
    else:
        msg += "<b>Session:</b> Not set\n"

    msg += "\n<b>Recent Job Heartbeats:</b>\n"
    if heartbeats:
        for hb in heartbeats:
            msg += f"- <code>{hb['job_name']}</code>: {hb['timestamp']}\n"
    else:
        msg += "No recent heartbeats found.\n"

    msg += "\n<b>Recent Data Health Issues:</b>\n"
    if failures:
        for f in failures:
            msg += f"- ⚠️ <code>{f['stock_code']}</code> ({f['source']}): {f['message']} at {f['timestamp']}\n"
    else:
        msg += "No recent data-fetch failures.\n"

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
        "/analyse &lt;TICKER&gt; - Get technicals and action guidance\n"
        "/help - Show this message"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

@owner_only
async def action_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pool = get_pool()
    async with pool.acquire() as db:
        rows = await db.fetch("SELECT stock_code, action, rationale FROM stock_actions ORDER BY action")

    if not rows:
        await update.message.reply_text("No action plan data available.", parse_mode='HTML')
        return

    msg = "<b>Portfolio Action Plan:</b>\n"
    for r in rows:
        if r['action'] != "HOLD":
            msg += f"\n<b>{r['stock_code']}</b>: {r['action']}\n<i>{r['rationale']}</i>\n"

    if len(msg) > 4000:
        msg = msg[:4000] + "\n... (truncated)"

    await update.message.reply_text(msg, parse_mode='HTML')

@owner_only
async def analyse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /analyse <TICKER>", parse_mode='HTML')
        return
    ticker = context.args[0].upper()

    pool = get_pool()
    async with pool.acquire() as db:
        signal_row = await db.fetchrow("SELECT * FROM signals WHERE stock_code = $1 ORDER BY timestamp DESC LIMIT 1", ticker)
        action_row = await db.fetchrow("SELECT * FROM stock_actions WHERE stock_code = $1", ticker)

    breeze = await get_breeze_client()
    if breeze:
        try:
            quote = breeze.get_quotes(stock_code=ticker, exchange_code="NSE", product_type="cash")
            quote = {"ltp": float(quote["Success"][0]["ltp"]), "change": quote["Success"][0].get("change")} if quote.get("Success") else None
        except Exception:
            from core.providers import YFinanceProvider
            quote = YFinanceProvider().get_quote(ticker)
    else:
        from core.providers import YFinanceProvider
        quote = YFinanceProvider().get_quote(ticker)

    from .formatters import format_stock_analysis_message
    msg = format_stock_analysis_message(ticker, signal_row, action_row, quote)
    await update.message.reply_text(msg, parse_mode='HTML')

@owner_only
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Portfolio", callback_data="portfolio"), InlineKeyboardButton("📈 Signals", callback_data="signals")],
        [InlineKeyboardButton("🎯 Action Plan", callback_data="action_plan"), InlineKeyboardButton("💰 Funds", callback_data="funds")],
        [InlineKeyboardButton("⚙️ Status", callback_data="status")],
    ]
    await update.message.reply_text("Choose an option:", reply_markup=InlineKeyboardMarkup(keyboard))

@owner_only
async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dispatch = {
        "portfolio": portfolio_command,
        "signals": signals_command,
        "action_plan": action_plan_command,
        "funds": funds_command,
        "status": status_command,
    }
    handler = dispatch.get(query.data)
    if handler:
        update.message = query.message
        await handler(update, context)
