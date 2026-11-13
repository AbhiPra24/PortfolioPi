"""Formats raw data and algorithm outputs into readable Telegram messages."""

from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def format_portfolio_message(holdings: list) -> str:
    if not holdings:
        return "Your portfolio is currently empty or hasn't been fetched."
    msg = "📊 <b>Portfolio Summary</b>\n\n"
    total_val = sum(h.get("current_price", 0) * h.get("quantity", 0) for h in holdings)
    total_cost = sum(h.get("average_price", 0) * h.get("quantity", 0) for h in holdings)
    pnl = total_val - total_cost
    pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0

    msg += f"<b>Total Value:</b> ₹{total_val:,.2f}\n"
    msg += f"<b>Total Cost:</b> ₹{total_cost:,.2f}\n"
    msg += f"<b>Unrealized P&L:</b> ₹{pnl:,.2f} ({pnl_pct:.2f}%)\n\n"

    msg += "Top Holdings:\n"
    # sort by value
    sorted_h = sorted(holdings, key=lambda x: x.get("current_price", 0) * x.get("quantity", 0), reverse=True)[:5]
    for h in sorted_h:
        val = h.get("current_price", 0) * h.get("quantity", 0)
        msg += f"- <code>{h.get('stock_code')}</code>: {h.get('quantity')} shs @ ₹{h.get('average_price', 0):.2f} (Val: ₹{val:,.2f})\n"
    return msg


def format_signals_message(signals: list, actions_by_stock=None) -> str:
    if not signals:
        return "No signals generated for today."

    msg = "🎯 <b>Top Watchlist Signals</b>\n\n"
    # filter out None scores
    valid_signals = [s for s in signals if s[8] is not None]
    if not valid_signals:
        return "No signals generated for today (insufficient history)."

    # sort by composite score
    sorted_signals = sorted(valid_signals, key=lambda x: x[8], reverse=True)[:10]

    for s in sorted_signals:
        stock, rsi, macd, macd_sig, sma50, sma200, pct_52w, vol_ratio, score = s

        score_str = f"{score:.0f}" if score is not None else "N/A"
        rsi_str = f"{rsi:.1f}" if rsi is not None else "N/A"
        vol_str = f"{vol_ratio:.1f}" if vol_ratio is not None else "N/A"
        sma50_str = f"{sma50:.1f}" if sma50 is not None else "N/A"
        sma200_str = f"{sma200:.1f}" if sma200 is not None else "N/A"
        pct_str = f"{pct_52w:.1f}" if pct_52w is not None else "N/A"

        verdict_str = ""
        if actions_by_stock and stock in actions_by_stock:
            verdict_str = f" — <b>{actions_by_stock[stock]}</b>"

        msg += f"<b>{stock}</b> (Score: {score_str}){verdict_str}\n"
        msg += f"  RSI: {rsi_str} | Vol Spike: {vol_str}x\n"
        msg += f"  SMA50: {sma50_str} | SMA200: {sma200_str}\n"
        msg += f"  52w Dist: {pct_str}%\n\n"
    return msg


def format_stock_analysis_message(ticker, signal_row, action_row, quote):
    lines = [f"<b>{ticker} Analysis</b>"]
    if quote:
        lines.append(
            f"Price: ₹{quote['ltp']:.2f}" + (f" ({quote['change']:+.2f})" if quote.get("change") is not None else "")
        )
    if signal_row:
        lines.append(
            f"RSI(14): {signal_row['rsi14']:.1f} | MACD: {signal_row['macd_line']:.2f}/{signal_row['macd_signal']:.2f}"
        )
        lines.append(
            f"SMA50/200: {signal_row['sma50']:.2f}/{signal_row['sma200']:.2f} | Score: {signal_row['composite_score']:.0f}"
        )
    if action_row:
        lines.append(f"\n<b>Verdict: {action_row['action']}</b>\n<i>{action_row['rationale']}</i>")
    return "\n".join(lines)


def format_sips_message(sips: list) -> str:
    if not sips:
        return "No active SIPs found in the database."

    total_monthly = sum(s.get("monthly_sip", 0) for s in sips)
    msg = f"📋 <b>Active SIP Book ({len(sips)} Instruments)</b>\n"
    msg += f"<b>Total Monthly Inflow:</b> ₹{total_monthly:,.0f}\n\n"

    # Group by target_action
    grouped = {}
    for s in sips:
        action = s.get("target_action") or "HOLD"
        grouped.setdefault(action, []).append(s)

    order = [
        ("BUY MORE / CONTINUE", "🟢 <b>CONTINUE / BUY MORE</b>"),
        ("HOLD / ACCUMULATE", "⚪ <b>HOLD / STEADY</b>"),
        ("TRIM / PAUSE SIP", "🟡 <b>PAUSE SIP / TRIM</b>"),
        ("SELL / STOP SIP", "🔴 <b>STOP SIP / REALLOCATE</b>"),
    ]

    for action_key, header in order:
        items = grouped.get(action_key, [])
        if items:
            subtotal = sum(i.get("monthly_sip", 0) for i in items)
            msg += f"{header} (₹{subtotal:,.0f}/mo):\n"
            for item in items:
                ticker = item.get("ticker")
                amount = item.get("monthly_sip", 0)
                msg += f"  • <code>{ticker}</code>: ₹{amount:,.0f}/mo\n"
            msg += "\n"

    # Any remaining unlisted actions
    known_keys = {k for k, _ in order}
    other_items = [s for s in sips if (s.get("target_action") or "HOLD") not in known_keys]
    if other_items:
        msg += "<b>Other Instruments:</b>\n"
        for item in other_items:
            msg += f"  • <code>{item.get('ticker')}</code>: ₹{item.get('monthly_sip', 0):,.0f}/mo ({item.get('target_action')})\n"

    return msg.strip()


def format_cashflow_message(dividend_rows: list, total_holdings_count: int = 0) -> str:
    if not dividend_rows:
        return "No dividend schedule data available."

    total_annual = sum(float(r.get("est_annual_cashflow") or 0) for r in dividend_rows)
    monthly_runrate = total_annual / 12.0

    msg = "💰 <b>Portfolio Dividend & Cashflow Engine</b>\n\n"
    msg += f"<b>Estimated Annual Cashflow:</b> ₹{total_annual:,.0f} / year\n"
    msg += f"<b>Monthly Baseline Run-rate:</b> ~₹{monthly_runrate:,.0f} / month\n\n"

    msg += "<b>Top Yield-on-Cost (YoC) Generators:</b>\n"
    sorted_yoc = sorted(dividend_rows, key=lambda x: float(x.get("yield_on_cost") or 0), reverse=True)
    for r in sorted_yoc[:8]:
        ticker = r.get("ticker")
        yoc = float(r.get("yield_on_cost") or 0)
        est = float(r.get("est_annual_cashflow") or 0)
        freq = r.get("payout_frequency", "PERIODIC")
        msg += f"  • <code>{ticker}</code>: YoC <b>{yoc:.2f}%</b> (₹{est:,.0f}/yr, {freq})\n"

    msg += "\n<i>Projections scale automatically as monthly SIPs compound.</i>"
    return msg


def build_morning_brief_keyboard() -> InlineKeyboardMarkup:
    """1-tap drilldown buttons attached below the 5-second morning verdict."""
    keyboard = [
        [
            InlineKeyboardButton("📋 Action Plan & CSV", callback_data="cmd_action_plan"),
            InlineKeyboardButton("🎯 View Watchlist Signals", callback_data="cmd_signals"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def format_morning_brief(
    portfolio_summary: dict,
    audited_holdings: list[dict],
    sip_changes: list[str],
    cash_sweep_info: Optional[dict] = None,
) -> str:
    """
    Renders the 08:00 AM IST Defensive Verdict Brief (Inverted Pyramid).
    Expects ONLY owned holdings (`is_held == True`) in `audited_holdings`.
    """
    exits = [h for h in audited_holdings if h.get("action") in ("EXIT", "SELL_100_IMMEDIATELY", "SELL")]
    trims = [h for h in audited_holdings if h.get("action") in ("TRIM", "LOCK_PROFIT", "PAIRED_TAX_NEUTRAL_TRIM")]
    adds = [h for h in audited_holdings if h.get("action") in ("BUY_MORE", "ADD_WINNER")]

    # Partition remaining steady holdings by Weinstein stage / asset class
    actionable_tickers = {h["ticker"] for h in (exits + trims + adds)}
    steady = [h for h in audited_holdings if h["ticker"] not in actionable_tickers]

    stg2_cnt = sum(1 for h in steady if h.get("stage") == 2 and h.get("asset_class") == "EQUITY")
    stg1_cnt = sum(1 for h in steady if h.get("stage") == 1 and h.get("asset_class") == "EQUITY")
    yield_macro_cnt = len(steady) - stg2_cnt - stg1_cnt

    total_actions = len(exits) + len(trims) + len(adds)
    status_line = (
        f"<b>STATUS: ⚡ ACTION REQUIRED ({total_actions} Orders)</b>"
        if total_actions > 0
        else "<b>STATUS: ✅ ALL CLEAR (0 Orders Pending)</b>"
    )

    val_lakhs = portfolio_summary.get("current_val_lakhs", 0.0)
    pnl_pct = portfolio_summary.get("pnl_pct", 0.0)
    pnl_k = portfolio_summary.get("unrealized_pnl", 0.0) / 1000.0
    cash = portfolio_summary.get("cash", 0.0)

    # Tier 1: 1-Second Verdict Header
    lines = [
        "🚦 <b>PORTFOLIOPI | 08:00 IST</b>",
        status_line,
        f"💰 <b>₹{val_lakhs:.2f}L</b> ({pnl_pct:+.2f}% | {pnl_k:+.1f}k) • 💵 Cash: <b>₹{cash:,.0f}</b>",
    ]

    # Tier 2: Hard Capital Preservation (Exits)
    if exits:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔴 <b>IMMEDIATE EXITS (Stop / Stage 4)</b>")
        for h in exits:
            qty = h.get("qty", 0)
            directive = h.get("directive", "Chandelier stop breached; liquidate 100%.")
            lines.append(f"• <b>{h['ticker']}</b> (Qty: {qty}) — {directive}")

    # Tier 3: Tactical Profit Locks & Risk Caps (Trims)
    if trims:
        if not exits:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("\n🟡 <b>TACTICAL TRIMS & PROFIT LOCKS</b>")
        for h in trims:
            directive = h.get("directive", "Lock partial gains; trail core stop.")
            lines.append(f"• <b>{h['ticker']}</b> — {directive}")

    # Tier 3b: High-Conviction Pullback Adds on Existing Winners
    if adds:
        if not exits and not trims:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("\n➕ <b>ADD TO WINNERS (Stage 2 Pullback)</b>")
        for h in adds:
            directive = h.get("directive", "Institutional bounce zone (0.5x ATR).")
            lines.append(f"• <b>{h['ticker']}</b> — {directive}")

    # Tier 4: SIP Pauses & Idle Cash Float Exceptions
    has_sweep = bool(cash_sweep_info and cash_sweep_info.get("eligible"))
    if sip_changes or has_sweep:
        if not (exits or trims or adds):
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("\n🔵 <b>SIP & FLOAT UPDATES</b>")
        if sip_changes:
            lines.append(f"• <b>Paused/Stopped SIPs ({len(sip_changes)}):</b> {', '.join(sip_changes)}")
        if has_sweep:
            float_amt = cash_sweep_info.get("idle_cash", cash)
            target = cash_sweep_info.get("target_etf", "LIQUIDBEES")
            lines.append(f"• <b>Cash Sweep:</b> ₹{float_amt:,.0f} idle float ready → <code>{target}</code>")

    # Tier 5: Healthy Baseline Collapse
    lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"🟢 <b>{len(steady)} Holdings Steady</b> "
        f"({stg2_cnt} Stage 2 • {stg1_cnt} Stage 1 • {yield_macro_cnt} Yield/Macro)"
    )
    if total_actions == 0:
        lines.append("<i>No stop breaches, Stage 4 breakdowns, or overweight alerts today.</i>")
    else:
        lines.append("<i>All remaining trailing stops & 200-SMA guardrails intact.</i>")

    return "\n".join(lines)


def format_premarket_opportunities(
    candidates: list[dict],
    available_cash: float,
    max_per_trade: float = 15000.0,
) -> str:
    """
    Renders the 09:00 AM IST Offensive Breakout Alert (unowned watchlist only).
    """
    lines = [
        "🎯 <b>PORTFOLIOPI | 09:00 PRE-MARKET BREAKOUTS</b>",
        f"💵 Deployable Cash Float: <b>₹{available_cash:,.0f}</b> (Cap: ₹{max_per_trade:,.0f}/idea)",
        "━━━━━━━━━━━━━━━━━━━━",
    ]

    for c in candidates[:5]:
        cmp = c.get("cmp", 0.0)
        stop = c.get("stop_loss", 0.0)
        risk_pct = ((cmp - stop) / cmp * 100) if cmp > 0 else 0.0
        vol_ratio = c.get("vol_ratio", 1.0)
        rs_score = c.get("rs_score", 0)
        sug_qty = int(min(available_cash, max_per_trade) // cmp) if cmp > 0 else 0

        lines.append(
            f"🚀 <b>{c['ticker']}</b> @ ₹{cmp:,.2f} (Minervini 7/7 • RS {rs_score})\n"
            f"   └ Vol: <b>{vol_ratio:.1f}x</b> | Stop: ₹{stop:,.2f} (-{risk_pct:.1f}%) | Sug Qty: <b>{sug_qty}</b>"
        )

    lines.append("\n<i>Tap /signals for full technical matrix or /action_plan for Trade Racer CSV.</i>")
    return "\n".join(lines)


# Backwards compatibility alias
format_premarket_opportunities_message = format_premarket_opportunities
