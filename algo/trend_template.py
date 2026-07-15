"""Minervini Trend Template (7-point structural uptrend checklist)."""

import pandas as pd


def check_trend_template(df: pd.DataFrame) -> int:
    if len(df) < 200:
        return 0

    df = df.sort_values('date').reset_index(drop=True)

    current_close = df['close'].iloc[-1]
    sma_50 = df['close'].rolling(50).mean().iloc[-1]
    sma_150 = df['close'].rolling(150).mean().iloc[-1]
    sma_200 = df['close'].rolling(200).mean().iloc[-1]
    sma_200_1m_ago = df['close'].rolling(200).mean().iloc[-21]

    low_52w = df['close'].tail(252).min()
    high_52w = df['close'].tail(252).max()

    score = 0
    if current_close > sma_150 and current_close > sma_200:
        score += 1
    if sma_150 > sma_200:
        score += 1
    if sma_200 > sma_200_1m_ago:
        score += 1
    if sma_50 > sma_150 and sma_50 > sma_200:
        score += 1
    if current_close > sma_50:
        score += 1
    if current_close >= 1.30 * low_52w:
        score += 1
    if current_close >= 0.75 * high_52w:
        score += 1

    return score
