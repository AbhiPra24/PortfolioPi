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

def format_signals_message(signals: list) -> str:
    if not signals:
        return "No signals generated for today."

    msg = "🎯 <b>Top Watchlist Signals</b>\n\n"
    # sort by composite score
    sorted_signals = sorted(signals, key=lambda x: x[8], reverse=True)[:10]

    for s in sorted_signals:
        stock, rsi, macd, macd_sig, sma50, sma200, pct_52w, vol_ratio, score = s
        msg += f"<b>{stock}</b> (Score: {score:.0f})\n"
        msg += f"  RSI: {rsi:.1f} | Vol Spike: {vol_ratio:.1f}x\n"
        msg += f"  SMA50: {sma50:.1f} | SMA200: {sma200:.1f}\n"
        msg += f"  52w Dist: {pct_52w:.1f}%\n\n"
    return msg
