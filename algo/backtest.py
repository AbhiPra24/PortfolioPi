"""Historical backtesting engine for validating the portfolio action classifier."""

import asyncpg
import pandas as pd

from app_config import settings


async def run_backtest(dsn=None):
    """
    Replay composite scoring logic over ohlcv_cache history.
    Calculates 5, 10, and 20 day forward returns for signals.
    """
    if not dsn:
        dsn = settings.database_url.get_secret_value()

    results = []

    from algo.action_classifier import get_stock_action

    db = await asyncpg.connect(dsn=dsn)
    try:
        # Fetch NIFTY50
        nifty_data = await db.fetch(
            "SELECT date, close, open, high, low, volume FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date ASC"
        )
        nifty_df = pd.DataFrame(nifty_data, columns=['date', 'close', 'open', 'high', 'low', 'volume'])
        for col in ['close', 'open', 'high', 'low', 'volume']:
            nifty_df[col] = nifty_df[col].astype(float)

        # Fetch distinct stocks
        stock_rows = await db.fetch("SELECT DISTINCT stock_code FROM ohlcv_cache WHERE stock_code != '_NIFTY50'")
        stocks = [row["stock_code"] for row in stock_rows]

        for stock in stocks:
            data = await db.fetch(
                "SELECT date, close, open, high, low, volume FROM ohlcv_cache WHERE stock_code = $1 ORDER BY date ASC",
                stock,
            )
            if len(data) < 201:
                continue

            df = pd.DataFrame(data, columns=['date', 'close', 'open', 'high', 'low', 'volume'])
            for col in ['close', 'open', 'high', 'low', 'volume']:
                df[col] = df[col].astype(float)

            # Replay scoring logic
            for i in range(200, len(df)):
                row = df.iloc[i]
                current_date = row['date']

                # Pass slicing
                stock_slice = df.iloc[:i + 1]
                nifty_slice = nifty_df[nifty_df['date'] <= current_date]

                if len(nifty_slice) < 200:
                    continue

                # Test as if we are holding it to see SELL signals, or test as if we aren't to see BUY signals
                # Let's test non-holding for BUY signals
                res = get_stock_action(stock, stock_slice, nifty_slice, is_holding=False)
                action = res['action']

                ret_5d = (df['close'].iloc[i + 5] / row['close']) - 1 if i + 5 < len(df) else None
                ret_10d = (df['close'].iloc[i + 10] / row['close']) - 1 if i + 10 < len(df) else None
                ret_20d = (df['close'].iloc[i + 20] / row['close']) - 1 if i + 20 < len(df) else None

                results.append({
                    'stock_code': stock,
                    'date': current_date,
                    'action': action,
                    'rationale': res['rationale'],
                    'fwd_ret_5d': ret_5d,
                    'fwd_ret_10d': ret_10d,
                    'fwd_ret_20d': ret_20d
                })
    finally:
        await db.close()

    return pd.DataFrame(results)

if __name__ == "__main__":
    import asyncio
    df = asyncio.run(run_backtest())
    print("Backtest Results Summary:")
    print(f"Total signals generated: {len(df)}")
    if not df.empty:
        print(df.describe())
        print("\nFirst few results:")
        print(df.head())
