"""Historical backtesting engine for validating the portfolio action classifier."""

import aiosqlite
import pandas as pd

from app_config import settings

from .ranking import calculate_composite_score


async def run_backtest(db_path=None):
    """
    Replay composite scoring logic over ohlcv_cache history.
    Calculates 5, 10, and 20 day forward returns for signals.
    """
    if not db_path:
        db_path = settings.db_path

    results = []

    from algo.action_classifier import get_stock_action

    async with aiosqlite.connect(db_path) as db:
        # Fetch NIFTY50
        async with db.execute("SELECT date, close, open, high, low, volume FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date ASC") as cur:
            nifty_data = await cur.fetchall()
            nifty_df = pd.DataFrame(nifty_data, columns=['date', 'close', 'open', 'high', 'low', 'volume'])
            for col in ['close', 'open', 'high', 'low', 'volume']:
                nifty_df[col] = nifty_df[col].astype(float)

        # Fetch distinct stocks
        async with db.execute("SELECT DISTINCT stock_code FROM ohlcv_cache WHERE stock_code != '_NIFTY50'") as cursor:
            stocks = [row[0] for row in await cursor.fetchall()]

        for stock in stocks:
            async with db.execute("SELECT date, close, open, high, low, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date ASC", (stock,)) as cur:
                data = await cur.fetchall()
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
                    stock_slice = df.iloc[:i+1]
                    nifty_slice = nifty_df[nifty_df['date'] <= current_date]
                    
                    if len(nifty_slice) < 200:
                        continue

                    # Test as if we are holding it to see SELL signals, or test as if we aren't to see BUY signals
                    # Let's test non-holding for BUY signals
                    res = get_stock_action(stock, stock_slice, nifty_slice, is_holding=False)
                    action = res['action']

                    action = res['action']

                    ret_5d = (df['close'].iloc[i+5] / row['close']) - 1 if i+5 < len(df) else None
                    ret_10d = (df['close'].iloc[i+10] / row['close']) - 1 if i+10 < len(df) else None
                    ret_20d = (df['close'].iloc[i+20] / row['close']) - 1 if i+20 < len(df) else None

                    results.append({
                        'stock_code': stock,
                        'date': current_date,
                        'action': action,
                        'rationale': res['rationale'],
                        'fwd_ret_5d': ret_5d,
                        'fwd_ret_10d': ret_10d,
                        'fwd_ret_20d': ret_20d
                    })

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
