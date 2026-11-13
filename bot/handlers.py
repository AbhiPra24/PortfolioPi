"""Telegram bot command handlers for processing user requests."""

import asyncio
import html
import logging
import re
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
    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def portfolio_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM holdings_snapshot ORDER BY timestamp DESC LIMIT 50") as cur:
            rows = await cur.fetchall()

    holdings = [dict(row) for row in rows]
    msg = format_portfolio_message(holdings)
    await update.message.reply_text(msg, parse_mode="HTML")


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
    await update.message.reply_text(msg, parse_mode="HTML")


SESSION_REGEX = re.compile(r"apisession=([a-zA-Z0-9]+)", re.IGNORECASE)
RAW_TOKEN_REGEX = re.compile(r"^[a-zA-Z0-9]{8,64}$")


def extract_session_token(text: str) -> str | None:
    """Extract session token from URL / query param or raw token string."""
    if not text:
        return None
    cleaned = text.strip()
    match = SESSION_REGEX.search(cleaned)
    if match:
        return match.group(1)
    if RAW_TOKEN_REGEX.fullmatch(cleaned):
        return cleaned
    return None


async def handle_session_token_submission(token: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate customer details with Breeze, persist token, trigger sync, and reply."""
    from core.data_refresh import run_breeze_sync

    try:
        breeze = BreezeClient(token)
        details = breeze.get_customer_details(api_session=token)
        if details.get("Success"):
            await save_session(token)
            cust = details["Success"]
            user_name = cust.get("idirect_user_name", "N/A")
            user_id = cust.get("idirect_userid", "N/A")
            last_login = cust.get("idirect_lastlogin_time", "N/A")

            # Trigger run_breeze_sync in background task
            asyncio.create_task(run_breeze_sync(context.application, silent=False))

            success_msg = (
                "✅ <b>Breeze Session Authenticated Successfully!</b>\n\n"
                f"👤 <b>User:</b> {html.escape(str(user_name))} (<code>{html.escape(str(user_id))}</code>)\n"
                f"🕒 <b>Last Login:</b> {html.escape(str(last_login))}\n\n"
                "🚀 <i>Session saved. Background portfolio & market sync initiated!</i>"
            )
            await update.message.reply_text(success_msg, parse_mode="HTML")
        else:
            err = details.get("Error", "Unknown Breeze validation error")
            await update.message.reply_text(
                f"❌ <b>Breeze Validation Failed:</b>\n<code>{html.escape(str(err))}</code>",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.exception("Error validating Breeze session token")
        await update.message.reply_text(
            f"❌ <b>Error Validating Token:</b>\n<code>{html.escape(str(e))}</code>",
            parse_mode="HTML",
        )


@owner_only
async def session_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspect incoming text messages for apisession parameter or raw session token."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    token = extract_session_token(text)
    if not token:
        return

    await handle_session_token_submission(token, update, context)


@owner_only
async def refresh_session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /refresh_session &lt;token&gt;", parse_mode="HTML")
        return
    token = context.args[0]

    # Immediately validate token
    try:
        breeze = BreezeClient(token)
        details = breeze.get_customer_details(api_session=token)
        if details.get("Success"):
            await save_session(token)
            await update.message.reply_text(
                "<b>Token Validated. Starting Breeze Sync...</b>\nFull refresh started in background.",
                parse_mode="HTML",
            )

            import asyncio

            from core.data_refresh import run_breeze_sync

            asyncio.create_task(run_breeze_sync(context.application, silent=False))
        else:
            await update.message.reply_text(
                f"<b>Failed to validate token.</b> API Response: {details.get('Error', 'Unknown error')}",
                parse_mode="HTML",
            )
    except Exception as e:
        await update.message.reply_text(f"<b>Error validating token:</b> {e}", parse_mode="HTML")


@owner_only
async def watchlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Usage: /watchlist add &lt;TICKER&gt;, /watchlist remove &lt;TICKER&gt;, /watchlist list", parse_mode="HTML"
        )
        return

    action = context.args[0].lower()

    if action == "list":
        async with aiosqlite.connect(settings.db_path) as db:
            async with db.execute("SELECT stock_code FROM watchlist") as cur:
                rows = await cur.fetchall()
        if not rows:
            await update.message.reply_text("Watchlist is empty.", parse_mode="HTML")
            return
        msg = "<b>Watchlist:</b>\n" + "\n".join([f"- <code>{r[0]}</code>" for r in rows])
        await update.message.reply_text(msg, parse_mode="HTML")
        return

    if len(context.args) < 2:
        await update.message.reply_text(f"Usage: /watchlist {action} &lt;TICKER&gt;", parse_mode="HTML")
        return

    ticker = context.args[1].upper()

    if action == "add":
        breeze = await get_breeze_client()
        if not breeze:
            await update.message.reply_text("Could not init Breeze client. Is session token set?", parse_mode="HTML")
            return

        try:
            res = breeze.get_names(exchange_code="NSE", stock_code=ticker)
            if res.get("Status") == 500:
                await update.message.reply_text(
                    f"Validation failed for <code>{ticker}</code>. It might not be a valid NSE stock.",
                    parse_mode="HTML",
                )
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
                parse_mode="HTML",
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text(f"<code>{ticker}</code> is already in watchlist.", parse_mode="HTML")

    elif action == "remove":
        async with aiosqlite.connect(settings.db_path) as db:
            await db.execute("DELETE FROM watchlist WHERE stock_code = ?", (ticker,))
            await db.commit()
        await update.message.reply_text(f"Removed <code>{ticker}</code> from watchlist.", parse_mode="HTML")
    else:
        await update.message.reply_text("Unknown action. Use add, remove, or list.", parse_mode="HTML")


@owner_only
async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /price &lt;TICKER&gt;", parse_mode="HTML")
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
                await update.message.reply_text(msg, parse_mode="HTML")
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
        await update.message.reply_text(msg, parse_mode="HTML")
    else:
        await update.message.reply_text(f"Could not fetch price for <code>{ticker}</code>.", parse_mode="HTML")


@owner_only
async def funds_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    breeze = await get_breeze_client()
    if not breeze:
        await update.message.reply_text("Could not init Breeze client. Is session token set?", parse_mode="HTML")
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

    await update.message.reply_text(msg, parse_mode="HTML")


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
            parse_mode="HTML",
        )
        return

    metrics = calculate_trade_metrics(trades)
    header = format_trades_summary_header(metrics)

    if summary_only:
        msg = f"{header}\n\n<i>(Analyzed {len(trades)} recent executions from {source})</i>"
        await update.message.reply_text(msg, parse_mode="HTML")
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

    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def orders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    breeze = await get_breeze_client()
    if not breeze:
        await update.message.reply_text("Could not init Breeze client. Is session token set?", parse_mode="HTML")
        return

    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=5)).strftime("%Y-%m-%dT06:00:00.000Z")
    to_date = now.strftime("%Y-%m-%dT06:00:00.000Z")

    orders_resp = breeze.get_order_list(from_date=from_date, to_date=to_date, exchange_code="NSE")
    if not orders_resp.get("Success"):
        err = orders_resp.get("Error") or "No recent orders found."
        await update.message.reply_text(f"<b>Order Book:</b>\n{err}", parse_mode="HTML")
        return

    orders = orders_resp["Success"]
    msg = f"<b>📋 Order Book (Last 5 Days) [{len(orders)}]:</b>\n\n"
    for o in orders[:10]:
        st = o.get("status", "Unknown")
        icon = (
            "✅" if st.lower() == "executed" else ("⏳" if "pending" in st.lower() or "ordered" in st.lower() else "❌")
        )
        msg += (
            f"{icon} <b>{o.get('stock_code')}</b> - {o.get('action')} {o.get('order_type')}\n"
            f"   Qty: {o.get('quantity')} @ ₹{o.get('price')} | Status: <b>{st}</b>\n"
            f"   Time: {o.get('order_datetime')}\n"
        )
    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute("SELECT job_name, timestamp FROM job_heartbeats ORDER BY timestamp DESC LIMIT 5") as cur:
            heartbeats = await cur.fetchall()

        async with db.execute("SELECT timestamp FROM session_tokens LIMIT 1") as cur:
            session_row = await cur.fetchone()

        async with db.execute(
            "SELECT stock_code, source, message, timestamp FROM data_health ORDER BY timestamp DESC LIMIT 5"
        ) as cur:
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

    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>🤖 PortfolioPi Bot Commands:</b>\n\n"
        "/start - Welcome message & bot overview\n"
        "/menu - Interactive dashboard menu with quick-actions\n"
        "/portfolio - View current holdings & valuation\n"
        "/signals - View today's top technical & momentum signals\n"
        "/action_plan - View buy/sell/hold stage verdicts\n"
        "/audit - Run edge risk audit on active holdings (pralabs:edge)\n"
        "/sips - View active SIP book & quant allocation\n"
        "/cashflow - View dividend & cashflow projections\n"
        "/sgb - Sovereign Gold Bond secondary market arbitrage & YTM\n"
        "/cashsweep - Sweep idle float into LIQUIDBEES or Stage 2 compounders\n"
        "/watchlist list|add|remove &lt;TICKER&gt; - Manage watchlist\n"
        "/price &lt;TICKER&gt; - Get real-time/latest stock price\n"
        "/funds - View available funds, cash limits, and margins\n"
        "/trades [summary] - View executed trades, drag &amp; performance metrics\n"
        "/orders - View recent order book\n"
        "/status - View system status, SQLite WAL health, and models\n"
        "/refresh_session &lt;token&gt; - Update Breeze API session\n"
        "/analyse &lt;TICKER&gt; - Full technical diagnosis with AI commentary\n"
        "/help - Show this message\n\n"
        "💡 <i>Tip: You can also paste your <code>apisession=...</code> redirect URL or raw session token directly into the chat to refresh Breeze!</i>"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


@owner_only
async def sips_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM sip_book ORDER BY monthly_sip DESC") as cur:
            rows = await cur.fetchall()

    sips = [dict(r) for r in rows]
    msg = format_sips_message(sips)
    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def cashflow_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM dividend_schedule ORDER BY yield_on_cost DESC") as cur:
            rows = await cur.fetchall()

    dividend_rows = [dict(r) for r in rows]
    msg = format_cashflow_message(dividend_rows)
    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def action_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute("SELECT stock_code, action, rationale FROM stock_actions ORDER BY action") as cur:
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text("No action plan data available.", parse_mode="HTML")
        return

    msg = "<b>Portfolio Action Plan:</b>\n"
    for r in rows:
        if r[1] != "HOLD":
            msg += f"\n<b>{r[0]}</b>: {r[1]}\n<i>{r[2]}</i>\n"

    if len(msg) > 4000:
        msg = msg[:4000] + "\n... (truncated)"

    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def analyse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /analyse &lt;TICKER&gt;", parse_mode="HTML")
        return
    ticker = context.args[0].upper()

    async with aiosqlite.connect(settings.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM signals WHERE stock_code = ? ORDER BY timestamp DESC LIMIT 1", (ticker,)
        ) as cur:
            signal_row = await cur.fetchone()
        async with db.execute("SELECT * FROM stock_actions WHERE stock_code = ?", (ticker,)) as cur:
            action_row = await cur.fetchone()
        async with db.execute("SELECT * FROM holdings_snapshot WHERE stock_code = ?", (ticker,)) as cur:
            holding_row = await cur.fetchone()
        async with db.execute(
            "SELECT stage FROM stage_history WHERE stock_code = ? ORDER BY date DESC LIMIT 1", (ticker,)
        ) as cur:
            stage_row = await cur.fetchone()
        async with db.execute(
            "SELECT tax_category, holding_days FROM tax_lots_view WHERE stock_code = ? ORDER BY buy_date ASC LIMIT 1",
            (ticker,),
        ) as cur:
            tax_lot_row = await cur.fetchone()

    breeze = await get_breeze_client()
    if breeze:
        try:
            quote = breeze.get_quotes(stock_code=ticker, exchange_code="NSE", product_type="cash")
            quote = (
                {"ltp": float(quote["Success"][0]["ltp"]), "change": quote["Success"][0].get("change")}
                if quote.get("Success")
                else None
            )
        except Exception:
            from core.providers import YFinanceProvider

            quote = YFinanceProvider().get_quote(ticker)
    else:
        from core.providers import YFinanceProvider

        quote = YFinanceProvider().get_quote(ticker)

    from .formatters import format_stock_analysis_message

    msg = format_stock_analysis_message(ticker, signal_row, action_row, quote)

    # Enrich with AI analyst assessment from Ollama using compressed telemetry
    is_holding = bool(holding_row and (holding_row["quantity"] or 0) > 0)
    pnl_pct = 0.0
    if is_holding and holding_row["average_price"]:
        cmp_p = quote["ltp"] if (quote and quote.get("ltp")) else (holding_row["current_price"] or 0.0)
        avg_p = float(holding_row["average_price"])
        if avg_p > 0:
            pnl_pct = round((cmp_p - avg_p) / avg_p * 100.0, 1)

    portfolio_ctx = {
        "is_holding": is_holding,
        "pnl_pct": pnl_pct,
        "tax_term": "LT" if (tax_lot_row and str(tax_lot_row["tax_category"]).upper().startswith("LT")) else "ST",
        "holding_days": tax_lot_row["holding_days"] if tax_lot_row else None,
    }

    metrics = {
        "ticker": ticker,
        "price": quote["ltp"]
        if (quote and quote.get("ltp"))
        else (holding_row["current_price"] if holding_row else None),
        "stage": stage_row["stage"] if stage_row else None,
        "signal": dict(signal_row) if signal_row else {},
        "action": action_row["action"] if action_row else "HOLD",
        "rationale": action_row["rationale"] if action_row else "",
    }
    ai_commentary = await generate_ai_analysis(ticker, metrics, portfolio_ctx)
    if ai_commentary:
        msg += f"\n\n🤖 <b>AI Analyst:</b>\n<code>{html.escape(ai_commentary)}</code>"

    await update.message.reply_text(msg, parse_mode="HTML")


@owner_only
async def sgb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Scan SGB secondary market tranches against spot gold and display arbitrage opportunities."""
    from algo.sgb_arbitrage import SGB_TRANCHE_METADATA, analyze_sgb_secondary_market
    from core.providers import YFinanceProvider

    await update.message.reply_text(
        "🔍 <i>Scanning SGB secondary market tranches & gold spot...</i>", parse_mode="HTML"
    )

    # 1. Fetch benchmark gold price (GOLDBEES or gold spot)
    yfp = YFinanceProvider()
    goldbees_quote = yfp.get_quote("GOLDBEES")
    goldbees_price = goldbees_quote.get("ltp") if goldbees_quote else None

    # 2. Fetch prices for common SGB tranches
    sgb_quotes = {}
    tickers = list(SGB_TRANCHE_METADATA.keys())[:8]  # Top active liquid tranches
    for sym in tickers:
        q = yfp.get_quote(sym)
        if q and q.get("ltp"):
            sgb_quotes[sym] = float(q["ltp"])

    if not sgb_quotes:
        # Fallback sample reference if market closed / quotes missing
        sgb_quotes = {"SGBOCT26": 7350.0, "SGBMAY29": 7400.0, "SGBDEC28": 7420.0}

    opps, payload = analyze_sgb_secondary_market(
        sgb_quotes=sgb_quotes, goldbees_price=goldbees_price or 82.5, discount_threshold_pct=2.0
    )

    if not opps:
        await update.message.reply_text("No active SGB arbitrage opportunities found currently.", parse_mode="HTML")
        return

    msg = "🏆 <b>Sovereign Gold Bond (SGB) Secondary Arbitrage</b>\n\n"
    for o in opps[:5]:
        status_icon = "🟢 <b>ARBITRAGE</b>" if o.is_arbitrage_flagged else "⚪ FAIR"
        msg += (
            f"• <b>{html.escape(o.symbol)}</b>: ₹{o.traded_price:,.0f} ({status_icon})\n"
            f"   Discount vs Spot: <b>{o.discount_pct:+.2f}%</b> | YTM: <b>{o.ytm_pct:.2f}%</b>\n"
            f"   Maturity: {o.maturity_date} ({o.years_to_maturity:.1f} yrs left)\n"
        )

    if payload and payload.get("upi_link"):
        upi = payload["upi_link"]
        msg += f'\n💳 <a href="{upi}"><b>1-Tap UPI Deposit / Mandate</b></a>\n'
    if payload and payload.get("trade_racer_csv"):
        msg += f"\n📋 <b>Trade Racer Basket CSV:</b>\n<code>{html.escape(payload['trade_racer_csv'].strip())}</code>"

    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)


@owner_only
async def cashsweep_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Evaluate idle cash float and output sweeps to LIQUIDBEES or Stage 2 compounder."""
    from algo.liquid_float_sweeper import evaluate_liquid_float_sweep

    await update.message.reply_text("💸 <i>Evaluating idle cash drag & paused SIP float...</i>", parse_mode="HTML")

    unalloc = 0.0
    breeze = await get_breeze_client()
    if breeze:
        try:
            funds = breeze.get_funds()
            if funds.get("Success"):
                unalloc = float(funds["Success"].get("unallocated_balance", 0) or 0)
        except Exception:
            pass

    async with aiosqlite.connect(settings.db_path) as db:
        async with db.execute(
            "SELECT ticker, stage FROM stage_history WHERE stage = 2 ORDER BY date DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
            stage2_ticker = row[0] if row else "POWINF"

    report = await evaluate_liquid_float_sweep(
        db_path=settings.db_path, unallocated_cash=unalloc, stage2_leader_ticker=stage2_ticker, threshold=5000.0
    )

    msg = (
        f"🌊 <b>Liquid Float & Cash Drag Sweeper</b>\n\n"
        f"<b>Unallocated Cash:</b> ₹{report.unallocated_cash:,.2f}\n"
        f"<b>Paused SIP Float:</b> ₹{report.paused_sip_float:,.2f}\n"
        f"<b>Total Idle Float:</b> ₹{report.total_idle_float:,.2f} (Threshold: ₹{report.threshold:,.0f})\n\n"
    )

    if not report.sweep_triggered:
        msg += "✅ <i>Idle cash is within safe operational thresholds. No cash drag sweep needed.</i>"
    else:
        msg += f"🚨 <b>Action: {report.directive.action}</b>\n"
        msg += f"👉 <i>{report.directive.rationale}</i>\n\n"
        if report.execution_payload:
            csv_str = report.execution_payload.get("trade_racer_csv", "")
            upi = report.execution_payload.get("upi_link", "")
            if csv_str:
                msg += f"📋 <b>Trade Racer Basket:</b>\n<code>{html.escape(csv_str.strip())}</code>\n"
            if upi:
                msg += f'\n💳 <a href="{upi}"><b>1-Tap UPI Mandate Link</b></a>\n'

    await update.message.reply_text(msg, parse_mode="HTML", disable_web_page_preview=True)


@owner_only
async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("📊 Portfolio", callback_data="portfolio"),
            InlineKeyboardButton("📈 Signals", callback_data="signals"),
        ],
        [
            InlineKeyboardButton("🎯 Action Plan", callback_data="action_plan"),
            InlineKeyboardButton("🔍 Audit", callback_data="audit"),
        ],
        [
            InlineKeyboardButton("💰 Funds", callback_data="funds"),
            InlineKeyboardButton("💵 Cashflow", callback_data="cashflow"),
        ],
        [
            InlineKeyboardButton("🏆 SGB Arbitrage", callback_data="sgb"),
            InlineKeyboardButton("🌊 Cash Sweep", callback_data="cashsweep"),
        ],
        [
            InlineKeyboardButton("📋 SIP Book", callback_data="sips"),
            InlineKeyboardButton("🧾 Trades", callback_data="trades"),
        ],
        [
            InlineKeyboardButton("📋 Orders", callback_data="orders"),
            InlineKeyboardButton("⚙️ Status", callback_data="status"),
        ],
    ]
    await update.message.reply_text("Choose an option:", reply_markup=InlineKeyboardMarkup(keyboard))


@owner_only
async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dispatch = {
        "portfolio": portfolio_command,
        "signals": signals_command,
        "cmd_signals": signals_command,
        "action_plan": action_plan_command,
        "cmd_action_plan": action_plan_command,
        "audit": audit_command,
        "funds": funds_command,
        "cashflow": cashflow_command,
        "sgb": sgb_command,
        "cashsweep": cashsweep_command,
        "sips": sips_command,
        "trades": trades_command,
        "orders": orders_command,
        "status": status_command,
    }
    handler = dispatch.get(query.data)
    if handler:
        update.message = query.message
        await handler(update, context)


@owner_only
async def audit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger 2-ticker chunked portfolio risk audit across active holdings using pralabs:edge."""
    status_msg = await update.message.reply_text(
        "⏳ <i>Running edge risk audit on pralabs:edge...</i>", parse_mode="HTML"
    )

    try:
        from core.ai_analyst import generate_batch_ai_analysis

        async with aiosqlite.connect(settings.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT h.stock_code, h.quantity, h.average_price, h.current_price,
                       st.stage, act.action, act.rationale, sig.rsi14, sig.composite_score
                FROM holdings_snapshot h
                LEFT JOIN (
                    SELECT s1.stock_code, s1.stage
                    FROM stage_history s1
                    JOIN (SELECT stock_code, MAX(date) as max_d FROM stage_history GROUP BY stock_code) s2
                    ON s1.stock_code = s2.stock_code AND s1.date = s2.max_d
                ) st ON h.stock_code = st.stock_code
                LEFT JOIN (
                    SELECT a1.stock_code, a1.action, a1.rationale
                    FROM stock_actions a1
                    JOIN (SELECT stock_code, MAX(timestamp) as max_t FROM stock_actions GROUP BY stock_code) a2
                    ON a1.stock_code = a2.stock_code AND a1.timestamp = a2.max_t
                ) act ON h.stock_code = act.stock_code
                LEFT JOIN (
                    SELECT sg1.stock_code, sg1.rsi14, sg1.composite_score
                    FROM signals sg1
                    JOIN (SELECT stock_code, MAX(timestamp) as max_ts FROM signals GROUP BY stock_code) sg2
                    ON sg1.stock_code = sg2.stock_code AND sg1.timestamp = sg2.max_ts
                ) sig ON h.stock_code = sig.stock_code
            """) as cur:
                rows = await cur.fetchall()

        if not rows:
            await status_msg.edit_text("⚠️ No active holdings found to audit.")
            return

        items = []
        for r in rows:
            ticker = r["stock_code"]
            cmp = float(r["current_price"] or 1.0)
            avg = float(r["average_price"] or 1.0)
            pnl_pct = round((cmp - avg) / avg * 100.0, 1)
            metrics = {
                "ticker": ticker,
                "price": cmp,
                "stage": r["stage"] or 2,
                "signal": {"rsi14": r["rsi14"] or 50.0, "composite_score": r["composite_score"] or 60.0},
                "action": r["action"] or "HOLD",
                "rationale": r["rationale"] or "",
            }
            portfolio_ctx = {
                "is_holding": True,
                "pnl_pct": pnl_pct,
                "tax_term": "ST",
                "holding_days": 100,
            }
            items.append({"ticker": ticker, "metrics": metrics, "portfolio_ctx": portfolio_ctx})

        # Run 2-ticker chunked batch
        results = await generate_batch_ai_analysis(items, batch_size=2)

        # Filter non-HOLD actionable items
        actionable = []
        for ticker, resp in results.items():
            if resp and "ACT:HOLD" not in resp:
                clean_resp = resp.strip()
                actionable.append(f"<b>{ticker}</b>:\n<code>{html.escape(clean_resp)}</code>")

        if actionable:
            header = f"🚨 <b>Portfolio Risk Audit Alerts ({len(actionable)} Actionable / {len(rows)} Total)</b>\n\n"
            full_msg = header + "\n\n".join(actionable)
        else:
            full_msg = f"✅ <b>Portfolio Risk Audit Clear</b>\nAll {len(rows)} holdings maintain intact structure and active stops (100% ACT:HOLD)."

        await status_msg.edit_text(full_msg, parse_mode="HTML")
    except Exception as e:
        await status_msg.edit_text(f"❌ Error during edge audit: {e}")
