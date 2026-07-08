import asyncio
import logging
from datetime import datetime, timedelta

import aiosqlite
import dateutil.parser
from tenacity import retry, stop_after_attempt, wait_exponential

from algo.screener import run_screener
from bot.broadcaster import broadcast_message
from bot.formatters import format_portfolio_message, format_signals_message
from app_config import settings
from core.breeze_client import BreezeClient, SessionExpiredError
from core.session_store import get_session

logger = logging.getLogger(__name__)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_historical_with_retry(breeze, stock_code, from_date, to_date):
    return breeze.get_historical_data_v2(
        interval="1day",
        from_date=from_date,
        to_date=to_date,
        stock_code=stock_code,
        exchange_code="NSE",
        product_type="cash"
    )

async def run_refresh_pipeline(app=None):
    logger.info("Starting run_refresh_pipeline")
    try:
        token = await get_session()
        if not token:
            if app: await broadcast_message(app, "🚨 Data Refresh Failed: Session missing. Please login again.")
            return

        try:
            breeze = BreezeClient(token)
        except SessionExpiredError:
            if app: await broadcast_message(app, "🚨 Data Refresh Failed: Session expired. Please login again.")
            return

        async with aiosqlite.connect(settings.db_path) as db:
            # 1. Fetch Holdings
            holdings = await asyncio.to_thread(breeze.get_demat_holdings)
            port_holdings = await asyncio.to_thread(breeze.get_portfolio_holding, exchange_code="NSE", from_date="", to_date="")

            holdings_data = {}
            if isinstance(holdings, dict) and holdings.get("Success"):
                for item in holdings.get("Success", []):
                    stock = item.get("stock_code")
                    if stock:
                        holdings_data[stock] = {
                            "stock_code": stock,
                            "quantity": int(item.get("quantity", 0) or 0),
                            "average_price": float(item.get("average_price", 0) or 0),
                            "current_price": float(item.get("current_market_price", 0) or item.get("current_price", 0) or item.get("average_price", 0) or 0)
                        }
            if isinstance(port_holdings, dict) and port_holdings.get("Success"):
                for item in port_holdings.get("Success", []):
                    stock = item.get("stock_code")
                    if stock:
                        if stock in holdings_data:
                            holdings_data[stock]["quantity"] = max(holdings_data[stock]["quantity"], int(item.get("quantity", 0) or 0))
                            holdings_data[stock]["average_price"] = float(item.get("average_price", 0) or holdings_data[stock]["average_price"])
                            if float(item.get("current_market_price", 0) or item.get("current_price", 0) or 0) > 0:
                                holdings_data[stock]["current_price"] = float(item.get("current_market_price", 0) or item.get("current_price", 0))
                        else:
                            holdings_data[stock] = {
                                "stock_code": stock,
                                "quantity": int(item.get("quantity", 0) or 0),
                                "average_price": float(item.get("average_price", 0) or 0),
                                "current_price": float(item.get("current_market_price", 0) or item.get("current_price", 0) or item.get("average_price", 0) or 0)
                            }

            if holdings_data:
                to_insert = [
                    (v["stock_code"], v["quantity"], v["average_price"], v["current_price"])
                    for v in holdings_data.values()
                ]
                await db.executemany("""
                    INSERT INTO holdings_snapshot (stock_code, quantity, average_price, current_price)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(stock_code) DO UPDATE SET
                        quantity=excluded.quantity,
                        average_price=excluded.average_price,
                        current_price=excluded.current_price,
                        timestamp=CURRENT_TIMESTAMP
                """, to_insert)
                await db.commit()

            # 2. Fetch Watchlist
            async with db.execute("SELECT stock_code FROM watchlist") as cursor:
                watchlist = [row[0] for row in await cursor.fetchall()]

            all_tickers = list(set(list(holdings_data.keys()) + watchlist))

            # 3. Backfill OHLCV cache
            for ticker in all_tickers:
                async with db.execute("SELECT MAX(date) FROM ohlcv_cache WHERE stock_code = ?", (ticker,)) as cursor:
                    max_date_row = await cursor.fetchone()

                max_date = max_date_row[0] if max_date_row and max_date_row[0] else None

                to_date_dt = datetime.now()
                to_date_str = to_date_dt.strftime("%Y-%m-%dT00:00:00.000Z")

                if max_date:
                    try:
                        from_date_dt = datetime.strptime(max_date, "%Y-%m-%d %H:%M:%S")
                    except ValueError:
                        try:
                            from_date_dt = dateutil.parser.parse(max_date)
                        except Exception:
                            from_date_dt = to_date_dt - timedelta(days=365)
                    from_date_dt += timedelta(days=1)
                else:
                    from_date_dt = to_date_dt - timedelta(days=365)

                if from_date_dt > to_date_dt:
                    continue

                from_date_str = from_date_dt.strftime("%Y-%m-%dT00:00:00.000Z")

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
                                INSERT OR IGNORE INTO ohlcv_cache 
                                (stock_code, date, open, high, low, close, volume)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                            """, records)
                            await db.commit()
                except Exception as e:
                    logger.error(f"Failed to fetch historical data for {ticker}: {e}")

                await asyncio.sleep(1)

            # 4. Run screener
            signals = await run_screener()

            # 5. Format HTML digest and broadcast
            holdings_list = list(holdings_data.values())
            port_msg = format_portfolio_message(holdings_list)
            sig_msg = format_signals_message(signals)

            digest = f"{port_msg}\n\n{sig_msg}"
            if app: await broadcast_message(app, digest)

            # 6. Record heartbeat on complete success
            await db.execute("INSERT INTO job_heartbeats (job_name) VALUES (?)", ("run_refresh_pipeline",))
            await db.commit()

    except Exception as e:
        logger.exception("Error in run_refresh_pipeline")
        import html
        if app: await broadcast_message(app, f"🚨 Unhandled Error in data refresh pipeline:\n{html.escape(str(e))}")
