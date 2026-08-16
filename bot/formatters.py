"""Formats raw data and algorithm outputs into readable Telegram messages."""

def format_portfolio_message(holdings: list) -> str:
    if not holdings:
        return "Your portfolio is currently empty or hasn't been fetched."
    msg = "📊 <b>Portfolio Summary</b>\n\n"
    total_val = sum(h.get('current_price', 0) * h.get('quantity', 0) for h in holdings)
    total_cost = sum(h.get('average_price', 0) * h.get('quantity', 0) for h in holdings)
    pnl = total_val - total_cost
    pnl_pct = (pnl / total_cost * 100) if total_cost > 0 else 0

    msg += f"<b>Total Value:</b> ₹{total_val:,.2f}\n"
    msg += f"<b>Total Cost:</b> ₹{total_cost:,.2f}\n"
    msg += f"<b>Unrealized P&L:</b> ₹{pnl:,.2f} ({pnl_pct:.2f}%)\n\n"

    msg += "Top Holdings:\n"
    # sort by value
    sorted_h = sorted(holdings, key=lambda x: x.get('current_price', 0) * x.get('quantity', 0), reverse=True)[:5]
    for h in sorted_h:
        val = h.get('current_price', 0) * h.get('quantity', 0)
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
        lines.append(f"Price: ₹{quote['ltp']:.2f}" + (f" ({quote['change']:+.2f})" if quote.get('change') is not None else ""))
    if signal_row:
        lines.append(f"RSI(14): {signal_row['rsi14']:.1f} | MACD: {signal_row['macd_line']:.2f}/{signal_row['macd_signal']:.2f}")
        lines.append(f"SMA50/200: {signal_row['sma50']:.2f}/{signal_row['sma200']:.2f} | Score: {signal_row['composite_score']:.0f}")
    if action_row:
        lines.append(f"\n<b>Verdict: {action_row['action']}</b>\n<i>{action_row['rationale']}</i>")
    return "\n".join(lines)


def format_sips_message(sips: list) -> str:
    if not sips:
        return "No active SIPs found in the database."

    total_monthly = sum(s.get('monthly_sip', 0) for s in sips)
    msg = f"📋 <b>Active SIP Book ({len(sips)} Instruments)</b>\n"
    msg += f"<b>Total Monthly Inflow:</b> ₹{total_monthly:,.0f}\n\n"

    # Group by target_action
    grouped = {}
    for s in sips:
        action = s.get('target_action') or 'HOLD'
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
            subtotal = sum(i.get('monthly_sip', 0) for i in items)
            msg += f"{header} (₹{subtotal:,.0f}/mo):\n"
            for item in items:
                ticker = item.get('ticker')
                amount = item.get('monthly_sip', 0)
                msg += f"  • <code>{ticker}</code>: ₹{amount:,.0f}/mo\n"
            msg += "\n"

    # Any remaining unlisted actions
    known_keys = {k for k, _ in order}
    other_items = [s for s in sips if (s.get('target_action') or 'HOLD') not in known_keys]
    if other_items:
        msg += "<b>Other Instruments:</b>\n"
        for item in other_items:
            msg += f"  • <code>{item.get('ticker')}</code>: ₹{item.get('monthly_sip', 0):,.0f}/mo ({item.get('target_action')})\n"

    return msg.strip()


def format_cashflow_message(dividend_rows: list, total_holdings_count: int = 0) -> str:
    if not dividend_rows:
        return "No dividend schedule data available."

    total_annual = sum(r.get('est_annual_cashflow', 0) for r in dividend_rows)
    monthly_runrate = total_annual / 12.0

    msg = "💰 <b>Portfolio Dividend & Cashflow Engine</b>\n\n"
    msg += f"<b>Estimated Annual Cashflow:</b> ₹{total_annual:,.0f} / year\n"
    msg += f"<b>Monthly Baseline Run-rate:</b> ~₹{monthly_runrate:,.0f} / month\n\n"

    msg += "<b>Top Yield-on-Cost (YoC) Generators:</b>\n"
    sorted_yoc = sorted(dividend_rows, key=lambda x: x.get('yield_on_cost', 0), reverse=True)
    for r in sorted_yoc[:8]:
        ticker = r.get('ticker')
        yoc = r.get('yield_on_cost', 0)
        est = r.get('est_annual_cashflow', 0)
        freq = r.get('payout_frequency', 'PERIODIC')
        msg += f"  • <code>{ticker}</code>: YoC <b>{yoc:.2f}%</b> (₹{est:,.0f}/yr, {freq})\n"

    msg += "\n<i>Projections scale automatically as monthly SIPs compound.</i>"
    return msg

