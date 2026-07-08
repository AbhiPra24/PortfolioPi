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
        
        msg += f"<b>{stock}</b> (Score: {score_str})\n"
        msg += f"  RSI: {rsi_str} | Vol Spike: {vol_str}x\n"
        msg += f"  SMA50: {sma50_str} | SMA200: {sma200_str}\n"
        msg += f"  52w Dist: {pct_str}%\n\n"
    return msg
