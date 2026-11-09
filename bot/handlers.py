"""Telegram bot command handlers for processing user requests."""

import html
import logging
import sqlite3

import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app_config import settings
from core.ai_analyst import generate_ai_analysis
from core.breeze_client import BreezeClient
from core.session_store import get_session, save_session
from .formatters import (
    format_cashflow_message,
    format_portfolio_message,
    format_signals_message,
    format_sips_message,
)
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
        details = breeze.get_customer_details(api_session=token)
        if details.get("Success"):
            await save_session(token)
            await update.message.reply_text("<b>Token Validated. Starting Breeze Sync...</b>\nFull refresh started in background.", parse_mode='HTML')

            import asyncio
            from core.data_refresh import run_breeze_sync
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
            logger.error(f"Error validating ticker {ticker}: {e}")

        try:
            async with aiosqlite.connect(settings.db_path) as db:
                await db.execute("INSERT INTO watchlist (stock_code) VALUES (?)", (ticker,))
                await db.commit()

            # Queue auto-backfill request for the new ticker (Phase 3)
            async with aiosqlite.connect(settings.db_path) as db:
                await db.execute("INSERT INTO backfill_requests (stock_code) VALUES (?)", (ticker,))
                await db.commit()

            await update.message.reply_text(
                f"Added <code>{ticker}</code> to watchlist! Triggered historical backfill in background.",
                parse_mode='HTML'
            )
        except sqlite3.IntegrityError:
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
    if breeze:
        try:
            quote = breeze.get_quotes(stock_code=ticker, exchange_code="NSE", product_type="cash")
            if quote.get("Success"):
                data = quote["Success"][0]
                ltp = data.get("ltp")
                change = data.get("change")
                msg = f"<b>{ticker}</b>: ₹{ltp}"
                if change:
                    msg += f" ({change}%)"
                await update.message.reply_text(msg, parse_mode='HTML')
                return
        except Exception as e:
            logger.warning(f"Breeze quote failed for {ticker}, falling back to yfinance: {e}")

    # Fallback to yfinance
    from core.providers import YFinanceProvider
    provider = YFinanceProvider()
    quote = provider.get_quote(ticker)
    if quote:
        msg = f"<b>{ticker}</b>: ₹{quote['ltp']}"
        if quote.get("change") is not None:
            msg += f" ({quote['change']:+.2f}%)"
        await update.message.reply_text(msg, parse_mode='HTML')
    else:
        await update.message.reply_text(f"Could not fetch price for <code>{ticker}</code>.", parse_mode='HTML')


@owner_only
async def funds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    breeze = await get_breeze_client()
    if not breeze:
        await update.message.reply_text("Could not init Breeze client. Is session token set?", parse_mode='HTML')
        return

    funds = breeze.get_funds()
    margin = breeze.get_margin(exchange_code="NSE")

    msg = "<b>💰 Account Funds & Margins:</b>\n\n"
    if funds.get("Success"):
        data = funds["Success"]
        unalloc = float(data.get("unallocated_balance", 0) or 0)
        alloc_eq = float(data.get("allocated_equity", 0) or 0)
        alloc_fno = float(data.get("allocated_fno", 0) or 0)
        msg += (
            f"<b>Unallocated Cash:</b> ₹{unalloc:,.2f}\n"
            f"<b>Allocated Equity:</b> ₹{alloc_eq:,.2f}\n"
            f"<b>Allocated F&O:</b> ₹{alloc_fno:,.2f}\n"
        )
    else:
        msg += f"Funds info unavailable: {funds.get('Error')}\n"

    if margin.get("Success"):
        mdata = margin["Success"]
        cash_lim = float(mdata.get("cash_limit", 0) or 0)
        blk = float(mdata.get("block_by_trade", 0) or 0)
        msg += f"<b>Cash Limit (NSE):</b> ₹{cash_lim:,.2f}\n"
        if blk > 0:
            msg += f"<b>Blocked by Trade:</b> ₹{blk:,.2f}\n"

    await update.message.reply_text(msg, parse_mode='HTML')


@owner_only
async def trades_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from scripts.analyse_trade_performance import (
        calculate_trade_metrics,
        format_trades_summary_header,
        get_trades_from_db,
    )

    args = context.args or []
    summary_only = bool(args and args[0].lower() in ("summary", "stats", "metrics"))

    trades = []
    source = "live"

    breeze = await get_breeze_client()
    if breeze:
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=30 if summary_only else 7)).strftime("%Y-%m-%dT06:00:00.000Z")
        to_date = now.strftime("%Y-%m-%dT06:00:00.000Z")
        trades_resp = breeze.get_trade_list(from_date=from_date, to_date=to_date, exchange_code="NSE")
        if trades_resp.get("Success"):
            trades = trades_resp["Success"]

    if not trades:
        # Fallback to local database trades_history
        trades = await get_trades_from_db(settings.db_path, limit=50 if summary_only else 15)
        source = "db"

    if not trades:
        await update.message.reply_text(
            "<b>Recent Trades:</b>\nNo trade executions found in the last 7 days (or local database).",
            parse_mode='HTML'
        )
        return

    metrics = calculate_trade_metrics(trades)
    header = format_trades_summary_header(metrics)

    if summary_only:
        msg = f"{header}\n\n<i>(Analyzed {len(trades)} recent executions from {source})</i>"
        await update.message.reply_text(msg, parse_mode='HTML')
        return

    msg = f"{header}\n\n<b>Recent Executions:</b>\n"
    for t in trades[:8]:
        action_emoji = "🟢" if str(t.get("action", "")).lower() == "buy" else "🔴"
        cost = float(t.get("average_cost", 0) or 0)
        ltp = float(t.get("ltp") or t.get("price") or 0)
        brok = float(t.get("brokerage_amount", 0) or 0)
        taxes = float(t.get("total_taxes", 0) or 0)
        t_date = str(t.get("trade_date", ""))
        if len(t_date) > 16:
            t_date = t_date[:16].replace("T", " ")

        msg += (
            f"{action_emoji} <b>{t.get('stock_code')}</b> ({t.get('action')})\n"
            f"   Qty: {t.get('quantity')} @ ₹{ltp:,.2f} | Turn: ₹{cost:,.2f}\n"
            f"   Drag: ₹{(brok + taxes):.2f} (Brok: ₹{brok:.1f}, Tax: ₹{taxes:.1f}) | {t_date}\n"
        )

    if len(trades) > 8:
        msg += f"\n<i>...and {len(trades) - 8} more trades. Use /trades summary for full stats.</i>"

    await update.message.reply_text(msg, parse_mode='HTML')


@owner_only
async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    breeze = await get_breeze_client()
    if not breeze:
        await update.message.reply_text("Could not init Breeze client. Is session token set?", parse_mode='HTML')
        return

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=5)).strftime("%Y-%m-%dT06:00:00.000Z")
    to_date = now.strftime("%Y-%m-%dT06:00:00.000Z")

    orders_resp = breeze.get_order_list(from_date=from_date, to_date=to_date, exchange_code="NSE")
    if not orders_resp.get("Success"):
        err = orders_resp.get("Error") or "No recent orders found."
        await update.message.reply_text(f"<b>Order Book:</b>\n{err}", parse_mode='HTML')
        return

    orders = orders_resp["Success"]
    msg = f"<b>📋 Order Book (Last 5 Days) [{len(orders)}]:</b>\n\n"
    for o in orders[:10]:
        st = o.get("status", "Unknown")
        icon = "✅" if st.lower() == "executed" else ("⏳" if "pending" in st.lower() or "ordered" in st.lower() else "❌")
        msg += (
            f"{icon} <b>{o.get('stock_code')}</b> - {o.get('action')} {o.get('order_type')}\n"
            f"   Qty: {o.get('quantity')} @ ₹{o.get('price')} | Status: <b>{st}</b>\n"
            f"   Time: {o.get('order_datetime')}\n"
        )
    await update.message.reply_text(msg, parse_mode='HTML')


@owner_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute("SELECT job_name, timestamp FROM job_heartbeats ORDER BY timestamp DESC LIMIT 5") as cur:
            heartbeats = await cur.fetchall()

        async with db.execute("SELECT timestamp FROM session_tokens LIMIT 1") as cur:
            session_row = await cur.fetchone()

        async with db.execute("SELECT stock_code, source, message, timestamp FROM data_health ORDER BY timestamp DESC LIMIT 5") as cur:
            failures = await cur.fetchall()

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

    msg += "\n<b>Recent Data Health Issues:</b>\n"
    if failures:
        for f in failures:
            msg += f"- ⚠️ <code>{f[0]}</code> ({f[1]}): {f[2]} at {f[3]}\n"
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
        "/sips - View active SIP book & quant allocation\n"
        "/cashflow - View dividend & cashflow projections\n"
        "/watchlist list|add|remove &lt;TICKER&gt; - Manage watchlist\n"
        "/price &lt;TICKER&gt; - Get current stock price\n"
        "/funds - View available funds and margins\n"
        "/trades [summary] - View executed trades, drag &amp; performance metrics\n"
        "/orders - View recent order book\n"
        "/status - View system status\n"
        "/refresh_session &lt;token&gt; - Update Breeze API session\n"
        "/analyse &lt;TICKER&gt; - Get technicals, AI analyst, and action guidance\n"
        "/help - Show this message"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')


@owner_only
async def sips_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sip_book ORDER BY monthly_sip DESC") as cur:
            rows = await cur.fetchall()

    sips = [dict(r) for r in rows]
    msg = format_sips_message(sips)
    await update.message.reply_text(msg, parse_mode='HTML')


@owner_only
async def cashflow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM dividend_schedule ORDER BY yield_on_cost DESC") as cur:
            rows = await cur.fetchall()

    dividend_rows = [dict(r) for r in rows]
    msg = format_cashflow_message(dividend_rows)
    await update.message.reply_text(msg, parse_mode='HTML')


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


@owner_only
async def analyse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /analyse &lt;TICKER&gt;", parse_mode='HTML')
        return
    ticker = context.args[0].upper()

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM signals WHERE stock_code = ? ORDER BY timestamp DESC LIMIT 1", (ticker,)) as cur:
            signal_row = await cur.fetchone()
        async with db.execute("SELECT * FROM stock_actions WHERE stock_code = ?", (ticker,)) as cur:
            action_row = await cur.fetchone()

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

    # Enrich with AI analyst assessment from Ollama
    metrics = {
        "quote": quote,
        "signal": dict(signal_row) if signal_row else {},
        "action": dict(action_row) if action_row else {},
    }
    ai_commentary = await generate_ai_analysis(ticker, metrics)
    if ai_commentary:
        msg += f"\n\n🤖 <b>AI Analyst Commentary:</b>\n{html.escape(ai_commentary)}"

    await update.message.reply_text(msg, parse_mode='HTML')


@owner_only
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 Portfolio", callback_data="portfolio"), InlineKeyboardButton("📈 Signals", callback_data="signals")],
        [InlineKeyboardButton("🎯 Action Plan", callback_data="action_plan"), InlineKeyboardButton("💰 Funds", callback_data="funds")],
        [InlineKeyboardButton("🧾 Trades", callback_data="trades"), InlineKeyboardButton("📋 Orders", callback_data="orders")],
        [InlineKeyboardButton("📋 SIP Book", callback_data="sips"), InlineKeyboardButton("💵 Cashflow", callback_data="cashflow")],
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
        "trades": trades_command,
        "orders": orders_command,
        "sips": sips_command,
        "cashflow": cashflow_command,
        "status": status_command,
    }
    handler = dispatch.get(query.data)
    if handler:
        update.message = query.message
        await handler(update, context)
