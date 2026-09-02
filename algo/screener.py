"""Tactical screener execution pipeline for daily technical setups."""

import pandas as pd

from core.db import get_pool

from .indicators import calculate_macd, calculate_rsi, calculate_sma, proximity_to_52w_high, volume_spike


async def run_screener():
    signals_data = []
    pool = get_pool()
    async with pool.acquire() as db:
        # Get unique stock codes from holdings and watchlist
        rows = await db.fetch("SELECT stock_code FROM holdings_snapshot UNION SELECT stock_code FROM watchlist")
        stocks = [row["stock_code"] for row in rows]

        for stock in stocks:
            data = await db.fetch(
                "SELECT date, close, high, volume FROM ohlcv_cache WHERE stock_code = $1 ORDER BY date DESC LIMIT 300", stock
            )
            if len(data) < 50:  # Need at least 50 days for basic SMAs
                continue
            data = list(reversed(data))

            # WARNING: OHLCV processing logic does not currently handle stock splits or bonus issues.
            # This may cause large sudden price jumps/drops that distort technical indicators.
            df = pd.DataFrame(data, columns=['date', 'close', 'high', 'volume'])

            close_series = df['close']
            volume_series = df['volume']
            current_price = close_series.iloc[-1]
            current_volume = volume_series.iloc[-1]

            if len(df) >= 250:
                high_52w = df['high'].rolling(window=252).max().iloc[-1] if len(df) >= 252 else df['high'].max()
                pct_from_52w = proximity_to_52w_high(current_price, high_52w)
            else:
                high_52w = None
                pct_from_52w = None

            rsi14 = calculate_rsi(close_series, 14)
            macd_line, macd_signal = calculate_macd(close_series)
            sma50 = calculate_sma(close_series, 50)
            sma200 = calculate_sma(close_series, 200) if len(df) >= 200 else 0.0
            vol_ratio = volume_spike(current_volume, volume_series)

            from .ranking import calculate_composite_score
            if pct_from_52w is not None:
                score = calculate_composite_score(rsi14, macd_line, macd_signal, current_price, sma50, sma200, pct_from_52w, vol_ratio)
            else:
                score = None  # Insufficient history

            signals_data.append((
                stock,
                float(rsi14),
                float(macd_line),
                float(macd_signal),
                float(sma50),
                float(sma200),
                float(pct_from_52w) if pct_from_52w is not None else None,
                float(vol_ratio),
                float(score) if score is not None else None
            ))

        # Insert signals
        if signals_data:
            await db.executemany("""
                INSERT INTO signals
                (stock_code, rsi14, macd_line, macd_signal, sma50, sma200, pct_from_52w_high, volume_ratio_20d, composite_score)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """, signals_data)
    return signals_data
