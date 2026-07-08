def calculate_composite_score(rsi14, macd_line, macd_signal, current_price, sma50, sma200, pct_from_52w, vol_ratio):
    score = 0
    # RSI between 40 and 60 is bullish momentum building
    if 40 <= rsi14 <= 60:
        score += 10
    # MACD crossover
    if macd_line > macd_signal:
        score += 20
    # Price above SMAs
    if current_price > sma50:
        score += 15
    if current_price > sma200:
        score += 10
    # Golden cross
    if sma50 > sma200:
        score += 20
    # Proximity to 52w high (within 5%)
    if 0 <= pct_from_52w <= 5:
        score += 15
    # Volume spike > 1.5x
    if vol_ratio > 1.5:
        score += 10
        
    return score
