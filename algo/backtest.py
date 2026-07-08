import aiosqlite
import pandas as pd

from config import settings

from .ranking import calculate_composite_score


async def run_backtest(db_path=None):
    """
    Replay composite scoring logic over ohlcv_cache history.
    Calculates 5, 10, and 20 day forward returns for signals.
    """
    if not db_path:
        db_path = settings.db_path

    results = []

    async with aiosqlite.connect(db_path) as db:
        # Fetch distinct stocks
        async with db.execute("SELECT DISTINCT stock_code FROM ohlcv_cache") as cursor:
            stocks = [row[0] for row in await cursor.fetchall()]

        for stock in stocks:
            async with db.execute("SELECT date, close, high, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date ASC", (stock,)) as cur:
                data = await cur.fetchall()
                if len(data) < 250:
                    continue

                df = pd.DataFrame(data, columns=['date', 'close', 'high', 'volume'])
                df['close'] = df['close'].astype(float)
                df['high'] = df['high'].astype(float)
                df['volume'] = df['volume'].astype(float)

                # Calculate indicators vectorially for performance
                # RSI 14
                delta = df['close'].diff()
                gain = delta.where(delta > 0, 0.0)
                loss = -delta.where(delta < 0, 0.0)
                avg_gain = gain.rolling(window=14).mean()
                avg_loss = loss.rolling(window=14).mean()
                rs = avg_gain / avg_loss
                df['rsi14'] = 100 - (100 / (1 + rs))
                df['rsi14'] = df['rsi14'].fillna(50.0)

                # MACD
                exp1 = df['close'].ewm(span=12, adjust=False).mean()
                exp2 = df['close'].ewm(span=26, adjust=False).mean()
                df['macd_line'] = exp1 - exp2
                df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()

                # SMAs
                df['sma50'] = df['close'].rolling(window=50).mean()
                df['sma200'] = df['close'].rolling(window=200).mean()

                # 52w High Proximity
                df['high_52w'] = df['high'].rolling(window=252).max().fillna(df['high'].rolling(window=250).max())
                df['pct_from_52w'] = ((df['high_52w'] - df['close']) / df['high_52w']) * 100.0

                # Volume Spike
                avg_vol = df['volume'].rolling(window=20).mean()
                df['vol_ratio'] = df['volume'] / avg_vol
                df['vol_ratio'] = df['vol_ratio'].replace([float('inf'), -float('inf')], 0.0).fillna(1.0)

                # Replay scoring logic
                for i in range(250, len(df)):
                    row = df.iloc[i]

                    score = calculate_composite_score(
                        row['rsi14'], row['macd_line'], row['macd_signal'],
                        row['close'], row['sma50'], row['sma200'],
                        row['pct_from_52w'], row['vol_ratio']
                    )

                    # Assuming we record signals where score is > 0 (or a specific threshold)
                    if score > 0:
                        ret_5d = (df['close'].iloc[i+5] / row['close']) - 1 if i+5 < len(df) else None
                        ret_10d = (df['close'].iloc[i+10] / row['close']) - 1 if i+10 < len(df) else None
                        ret_20d = (df['close'].iloc[i+20] / row['close']) - 1 if i+20 < len(df) else None

                        results.append({
                            'stock_code': stock,
                            'date': row['date'],
                            'score': score,
                            'fwd_ret_5d': ret_5d,
                            'fwd_ret_10d': ret_10d,
                            'fwd_ret_20d': ret_20d
                        })

    return pd.DataFrame(results)
