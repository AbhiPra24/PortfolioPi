import asyncio
import logging
from datetime import datetime, timedelta

import asyncpg
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from app_config import settings
from core.breeze_client import BreezeClient
from core.session_store import get_session

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_historical_with_retry(breeze, stock_code, from_date, to_date):
    return breeze.get_historical_data_v2(
        interval="1day",
        from_date=from_date,
        to_date=to_date,
        stock_code="NIFTY" if stock_code == "_NIFTY50" else stock_code,
        exchange_code="NSE",
        product_type="cash"
    )

async def run_backfill():
    logger.info("Starting manual backfill script (3-year depth in 90-day chunks)...")
    token = await get_session()
    if not token:
        logger.error("Session missing. Please login again.")
        return

    breeze = BreezeClient(token)

    db = await asyncpg.connect(dsn=settings.database_url.get_secret_value())
    try:
        ticker_rows = await db.fetch("SELECT DISTINCT stock_code FROM holdings_snapshot UNION SELECT stock_code FROM watchlist")
        tickers = [row["stock_code"] for row in ticker_rows]

        all_tickers = list(set(tickers + ["_NIFTY50"]))

        target_depth_days = 1095
        chunk_days = 90

        today_dt = datetime.now()
        start_dt = today_dt - timedelta(days=target_depth_days)

        for ticker in all_tickers:
            current_dt = start_dt
            while current_dt < today_dt:
                chunk_end_dt = current_dt + timedelta(days=chunk_days)
                if chunk_end_dt > today_dt:
                    chunk_end_dt = today_dt

                from_date_str = current_dt.strftime("%Y-%m-%dT00:00:00.000Z")
                to_date_str = chunk_end_dt.strftime("%Y-%m-%dT00:00:00.000Z")

                # Skip chunk if already fully populated
                chunk_from_str = current_dt.strftime("%Y-%m-%d")
                chunk_to_str = chunk_end_dt.strftime("%Y-%m-%d")
                existing = await db.fetchval(
                    "SELECT COUNT(*) FROM ohlcv_cache WHERE stock_code = $1 AND date >= $2 AND date <= $3",
                    ticker, chunk_from_str, chunk_to_str,
                )
                if existing > 0:
                    current_dt = chunk_end_dt + timedelta(days=1)
                    continue

                try:
                    hist_data = await asyncio.to_thread(get_historical_with_retry, breeze, ticker, from_date_str, to_date_str)

                    if isinstance(hist_data, dict) and hist_data.get("Success"):
                        records = []
                        for row in hist_data.get("Success", []):
                            records.append((
                                ticker,
                                row.get("datetime"),
                                float(row.get("open", 0)),
                                float(row.get("high", 0)),
                                float(row.get("low", 0)),
                                float(row.get("close", 0)),
                                int(row.get("volume", 0))
                            ))
                        if records:
                            await db.executemany("""
                                INSERT INTO ohlcv_cache
                                (stock_code, date, open, high, low, close, volume)
                                VALUES ($1, $2, $3, $4, $5, $6, $7)
                                ON CONFLICT (stock_code, date) DO NOTHING
                            """, records)
                        logger.info(f"{ticker} {current_dt.strftime('%Y-%m-%d')} to {chunk_end_dt.strftime('%Y-%m-%d')}: {len(records)} candles fetched")
                    else:
                        logger.warning(f"{ticker} {current_dt.strftime('%Y-%m-%d')} to {chunk_end_dt.strftime('%Y-%m-%d')}: failed or no data returned")

                except Exception as e:
                    logger.error(f"{ticker} {current_dt.strftime('%Y-%m-%d')} to {chunk_end_dt.strftime('%Y-%m-%d')} failed: {e}, will continue with next chunk")

                current_dt = chunk_end_dt + timedelta(days=1)
                await asyncio.sleep(1.5)

        logger.info("Backfill complete. Generating summary table...")
        summary = await db.fetch("""
            SELECT stock_code, COUNT(*) as days_cached, MIN(date) as earliest, MAX(date) as latest
            FROM ohlcv_cache
            GROUP BY stock_code
            ORDER BY days_cached ASC;
        """)
        df = pd.DataFrame(summary, columns=["stock_code", "total_rows_cached", "min_date", "max_date"])
        print("\n=== BACKFILL SUMMARY ===")
        print(df.to_string(index=False))
        print("========================\n")
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(run_backfill())
