"""Formats raw data and algorithm outputs into readable Telegram messages."""

from typing import Optional


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


def format_morning_brief(
    portfolio_summary: dict,
    audited_holdings: list,
    sip_changes: list,
    stage_breakdown: Optional[dict] = None,
    cash_sweep_info: Optional[dict] = None,
) -> str:
    """Format daily 08:00 AM IST morning brief using Management by Exception (Inverted Pyramid).

    Tiers:
    - Tier 1: 1-Second Verdict (Action Required vs All Clear, Net Value, P&L, Cash)
    - Tier 2: Hard Capital Preservation (🔴 Immediate Exits)
    - Tier 3: Tactical Profit Locks & Risk Caps (🟡 Trims / Locks)
    - Tier 4: SIP & Float Exceptions (🔵 Paused SIPs, Idle Cash Sweep)
    - Tier 5: The Healthy Baseline (🟢 Aggregate Steady Count)
    """
    exits = [h for h in audited_holdings if h.get("action") in ("SELL", "EXIT", "STOP_LOSS")]
    trims = [h for h in audited_holdings if h.get("action") in ("TRIM", "LOCK_PROFIT", "REDUCE")]
    adds = [h for h in audited_holdings if h.get("action") in ("BUY_MORE", "ADD_WINNER")]

    total_actions = len(exits) + len(trims) + len(adds)
    if total_actions > 0:
        status_line = f"<b>STATUS: ⚡ ACTION REQUIRED ({total_actions} Order{'s' if total_actions > 1 else ''})</b>"
    else:
        status_line = "<b>STATUS: ✅ ALL CLEAR (0 Orders Pending)</b>"

    current_val = portfolio_summary.get("current_val", 0.0)
    current_val_lakhs = current_val / 100000.0
    pnl = portfolio_summary.get("pnl", 0.0)
    pnl_pct = portfolio_summary.get("pnl_pct", 0.0)
    cash = portfolio_summary.get("cash", 0.0)

    pnl_sign = "+" if pnl >= 0 else ""
    pnl_k = pnl / 1000.0

    lines = [
        "🚦 <b>PORTFOLIOPI | 08:00 IST</b>",
        status_line,
        (
            f"💰 <b>₹{current_val_lakhs:.2f}L</b> ({pnl_pct:+.2f}% | {pnl_sign}₹{pnl_k:.1f}k) "
            f"• 💵 Cash: <b>₹{cash:,.0f}</b>"
        ),
    ]

    has_exceptions = False

    if exits:
        has_exceptions = True
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔴 <b>IMMEDIATE EXITS (Stop / Stage 4)</b>")
        for h in exits:
            tkr = h.get("ticker", "")
            qty = h.get("qty", 0)
            directive = h.get("directive", h.get("rationale", "Stop breached"))
            qty_str = f" (Qty: {qty})" if qty else ""
            lines.append(f"• <b>{tkr}</b>{qty_str} — {directive}")

    if trims:
        if not has_exceptions:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        has_exceptions = True
        lines.append("\n🟡 <b>TACTICAL TRIMS & PROFIT LOCKS</b>")
        for h in trims:
            tkr = h.get("ticker", "")
            directive = h.get("directive", h.get("rationale", "Lock profit / trim"))
            lines.append(f"• <b>{tkr}</b> — {directive}")

    if adds:
        if not has_exceptions:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        has_exceptions = True
        lines.append("\n➕ <b>ADD TO WINNERS (Stage 2 Pullback)</b>")
        for h in adds:
            tkr = h.get("ticker", "")
            directive = h.get("directive", h.get("rationale", "Institutional support bounce"))
            lines.append(f"• <b>{tkr}</b> — {directive}")

    # Tier 4: SIP & Float updates
    sip_or_float_lines = []
    if sip_changes:
        sip_or_float_lines.append(f"• <b>Paused SIPs ({len(sip_changes)}):</b> {', '.join(sip_changes)}")
    if cash_sweep_info and cash_sweep_info.get("eligible"):
        target_inst = cash_sweep_info.get("target", "LIQUIDBEES")
        amt = cash_sweep_info.get("amount", 0.0)
        sip_or_float_lines.append(f"• <b>Cash Sweep:</b> ₹{amt:,.1f}k idle float ready → <code>{target_inst}</code>")

    if sip_or_float_lines:
        if not has_exceptions:
            lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        has_exceptions = True
        lines.append("\n🔵 <b>SIP & FLOAT UPDATES</b>")
        lines.extend(sip_or_float_lines)

    # Tier 5: Healthy Baseline
    if has_exceptions:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
    else:
        lines.append("")

    steady_count = len(audited_holdings) - len(exits) - len(trims) - len(adds)
    if steady_count < 0:
        steady_count = 0

    if stage_breakdown:
        s2 = stage_breakdown.get("stage_2", 0)
        s1 = stage_breakdown.get("stage_1", 0)
        s_other = stage_breakdown.get("yield_or_macro", 0)
        breakdown_str = f" ({s2} Stage 2 • {s1} Stage 1 • {s_other} Yield/Macro)"
    else:
        breakdown_str = ""

    lines.append(f"🟢 <b>{steady_count} Holdings Steady</b>{breakdown_str}")
    if total_actions == 0:
        lines.append("<i>No stop breaches, Stage 4 breakdowns, or overweight alerts today.</i>")
    else:
        lines.append("<i>All trailing stops & 200-SMA guardrails intact for remaining assets.</i>")

    return "\n".join(lines).strip()


def format_premarket_opportunities_message(
    candidates: list,
    available_cash: float,
) -> str:
    """Format 09:00 AM IST pre-market deployment alert strictly for unowned watchlist leaders."""
    lines = [
        "🎯 <b>PORTFOLIOPI | 09:00 IST PRE-MARKET RADAR</b>",
        f"💵 <b>Available Deployable Cash:</b> ₹{available_cash:,.0f}\n",
        "🚀 <b>ACTIONABLE WATCHLIST BREAKOUTS (Minervini 7/7):</b>",
    ]
    for c in candidates:
        tkr = c.get("ticker", "")
        cmp = c.get("price", 0.0)
        rs = c.get("rs_score", 80)
        vol = c.get("volume_ratio", 1.5)
        score = c.get("score", 7)
        lines.append(f"• <b>{tkr}</b> @ ₹{cmp:,.2f} | Minervini: {score}/7 | RS: {rs} | Vol: {vol:.1f}x")
        if c.get("entry_note"):
            lines.append(f"  └ <i>{c['entry_note']}</i>")

    lines.append("\n<i>Execution check: Verify pre-open tick & spread before placing limit orders.</i>")
    return "\n".join(lines)
