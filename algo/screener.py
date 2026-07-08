import pandas as pd
import aiosqlite
from .indicators import calculate_rsi, calculate_macd, calculate_sma, proximity_to_52w_high, volume_spike
from config import settings

async def run_screener():
    signals_data = []
    async with aiosqlite.connect(settings.db_path) as db:
        # Get unique stock codes from holdings and watchlist
        async with db.execute("SELECT stock_code FROM holdings_snapshot UNION SELECT stock_code FROM watchlist") as cursor:
            rows = await cursor.fetchall()
            stocks = [row[0] for row in rows]
            
        for stock in stocks:
            async with db.execute("SELECT date, close, high, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date ASC", (stock,)) as cur:
                data = await cur.fetchall()
                if len(data) < 50: # Need at least 50 days for basic SMAs
                    continue
                
                df = pd.DataFrame(data, columns=['date', 'close', 'high', 'volume'])
                
                close_series = df['close']
                volume_series = df['volume']
                current_price = close_series.iloc[-1]
                current_volume = volume_series.iloc[-1]
                high_52w = df['high'].rolling(window=252).max().iloc[-1] if len(df) >= 252 else df['high'].max()
                
                rsi14 = calculate_rsi(close_series, 14)
                macd_line, macd_signal = calculate_macd(close_series)
                sma50 = calculate_sma(close_series, 50)
                sma200 = calculate_sma(close_series, 200) if len(df) >= 200 else 0.0
                pct_from_52w = proximity_to_52w_high(current_price, high_52w)
                vol_ratio = volume_spike(current_volume, volume_series)
                
                from .ranking import calculate_composite_score
                score = calculate_composite_score(rsi14, macd_line, macd_signal, current_price, sma50, sma200, pct_from_52w, vol_ratio)
                
                signals_data.append((stock, float(rsi14), float(macd_line), float(macd_signal), float(sma50), float(sma200), float(pct_from_52w), float(vol_ratio), float(score)))
        
        # Insert signals
        if signals_data:
            await db.executemany("""
                INSERT INTO signals 
                (stock_code, rsi14, macd_line, macd_signal, sma50, sma200, pct_from_52w_high, volume_ratio_20d, composite_score) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, signals_data)
            await db.commit()
    return signals_data
