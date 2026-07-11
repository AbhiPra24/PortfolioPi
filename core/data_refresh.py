"""Automated pipelines for Breeze ground-truth sync and Market Data refresh."""

import asyncio
import logging
from datetime import datetime, timedelta

import dateutil.parser
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from algo.screener import run_screener
from bot.broadcaster import broadcast_message
from bot.formatters import format_portfolio_message, format_signals_message
from core.breeze_client import BreezeClient, SessionExpiredError
from core.db import get_pool
from core.session_store import get_session

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

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_names_with_retry(breeze, stock_code):
    exchange = "NSE"
    return breeze.get_names(exchange_code=exchange, stock_code=stock_code)


async def run_breeze_sync(app=None, silent=True):
    """
    Breeze ground-truth sync. Only runs if session is valid.
    Fetches demat/portfolio quantities and does deep OHLCV backfill from Breeze.
    Resolves ticker mappings opportunistically.
    """
    logger.info("Starting run_breeze_sync")
    try:
        token = await get_session()
        if not token:
            logger.info("run_breeze_sync skipped: Session missing.")
            if not silent and app: await broadcast_message(app, "🚨 Sync Failed: Session missing.")
            return

        try:
            breeze = BreezeClient(token)
        except SessionExpiredError:
            logger.info("run_breeze_sync skipped: Session expired.")
            if not silent and app: await broadcast_message(app, "🚨 Sync Failed: Session expired.")
            return

        pool = get_pool()
        async with pool.acquire() as db:
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
                            "average_price": float(item.get("average_price", 0) or 0)
                        }
            if isinstance(port_holdings, dict) and port_holdings.get("Success"):
                for item in port_holdings.get("Success", []):
                    stock = item.get("stock_code")
                    if stock:
                        if stock in holdings_data:
                            holdings_data[stock]["quantity"] = max(holdings_data[stock]["quantity"], int(item.get("quantity", 0) or 0))
                            holdings_data[stock]["average_price"] = float(item.get("average_price", 0) or holdings_data[stock]["average_price"])
                        else:
                            holdings_data[stock] = {
                                "stock_code": stock,
                                "quantity": int(item.get("quantity", 0) or 0),
                                "average_price": float(item.get("average_price", 0) or 0)
                            }

            # We DO NOT update current_price here anymore, only quantity and avg price. We rely on market_data_job for price.
            # But we must insert if it's new.
            if holdings_data:
                for stock, data in holdings_data.items():
                    # Insert or update holding ground truth
                    await db.execute("""
                        INSERT INTO holdings_snapshot (stock_code, quantity, average_price, current_price)
                        VALUES ($1, $2, $3, 0)
                        ON CONFLICT(stock_code) DO UPDATE SET
                            quantity=excluded.quantity,
                            average_price=excluded.average_price
                    """, stock, data["quantity"], data["average_price"])

                # Record portfolio history (invested vs current value)
                totals_row = await db.fetchrow(
                    "SELECT SUM(quantity * average_price), SUM(quantity * current_price) FROM holdings_snapshot"
                )
                if totals_row and totals_row[0] is not None:
                    total_invested, total_current = totals_row[0], totals_row[1]
                    await db.execute("""
                        INSERT INTO portfolio_value_history (total_invested, total_current_value, total_pnl)
                        VALUES ($1, $2, $3)
                    """, total_invested, total_current, total_current - total_invested)

            # 2. Fetch Watchlist
            watchlist_rows = await db.fetch("SELECT stock_code FROM watchlist")
            watchlist = [row["stock_code"] for row in watchlist_rows]

            all_tickers = list(set(list(holdings_data.keys()) + watchlist))

            # 3. Resolve Mappings Opportunistically
            new_mappings_count = 0
            for ticker in all_tickers:
                mapping = await db.fetchrow("SELECT nse_symbol FROM ticker_mapping WHERE stock_code = $1", ticker)

                if not mapping:
                    try:
                        res = await asyncio.to_thread(get_names_with_retry, breeze, ticker)
                        if isinstance(res, dict) and res.get("Success"):
                            success_list = res.get("Success", [])
                            if success_list:
                                item = success_list[0]
                                nse_symbol = item.get("short_name", ticker)  # fallback to ticker if short_name is empty
                                isin = item.get("isin", "")
                                await db.execute("""
                                    INSERT INTO ticker_mapping (stock_code, nse_symbol, isin) VALUES ($1, $2, $3)
                                    ON CONFLICT (stock_code) DO UPDATE SET
                                        nse_symbol=excluded.nse_symbol, isin=excluded.isin
                                """, ticker, nse_symbol, isin)
                                new_mappings_count += 1
                                logger.info(f"Resolved mapping for {ticker}: {nse_symbol} ({isin})")
                    except Exception as e:
                        logger.error(f"Failed to resolve mapping for {ticker}: {e}")
                    await asyncio.sleep(1)

            # _NIFTY50 is built-in
            await db.execute("""
                INSERT INTO ticker_mapping (stock_code, nse_symbol, isin) VALUES ('_NIFTY50', 'NIFTY 50', '')
                ON CONFLICT (stock_code) DO NOTHING
            """)

            all_tickers.append("_NIFTY50")

            # 4. Backfill OHLCV cache (Breeze has better historical depth)
            for ticker in all_tickers:
                max_date = await db.fetchval("SELECT MAX(date) FROM ohlcv_cache WHERE stock_code = $1", ticker)

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
                                INSERT INTO ohlcv_cache
                                (stock_code, date, open, high, low, close, volume)
                                VALUES ($1, $2, $3, $4, $5, $6, $7)
                                ON CONFLICT (stock_code, date) DO NOTHING
                            """, records)
                except Exception as e:
                    logger.error(f"Failed to fetch historical data for {ticker}: {e}")
                    await db.execute(
                        "INSERT INTO data_health (stock_code, source, status, message) VALUES ($1, 'breeze', 'failed', $2)",
                        ticker, str(e),
                    )

                await asyncio.sleep(1)

            # 5. Record heartbeat
            await db.execute("INSERT INTO job_heartbeats (job_name) VALUES ($1)", "run_breeze_sync")

            if not silent and app:
                # Return summary to the user if requested (like from /refresh_session)
                total_holdings = len(holdings_data)
                total_val = await db.fetchval("SELECT SUM(quantity * current_price) FROM holdings_snapshot")
                total_val = total_val if total_val else 0.0

                msg = f"✅ Session verified.\nPortfolio synced: {total_holdings} holdings, ₹{total_val:,.2f} current value, {new_mappings_count} new tickers mapped."
                await broadcast_message(app, msg)

    except Exception as e:
        logger.exception("Error in run_breeze_sync")
        import html
        if not silent and app: await broadcast_message(app, f"🚨 Unhandled Error in Breeze Sync:\n{html.escape(str(e))}")


async def _normalize_and_upsert_ohlcv(db, ticker, rows):
    if not rows:
        return
    records = [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows]
    await db.executemany("""
        INSERT INTO ohlcv_cache (stock_code, date, open, high, low, close, volume)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (stock_code, date) DO NOTHING
    """, records)

async def backfill_ticker_history(db, ticker, provider, depth_days=1095, chunk_days=90):
    """Chunked historical backfill, provider-agnostic. Used for brand-new tickers
    (zero rows) and reused by the watchlist-add auto-backfill in Phase 3."""
    today_dt = datetime.now()
    start_dt = today_dt - timedelta(days=depth_days)
    current_dt = start_dt
    total_rows = 0
    while current_dt < today_dt:
        chunk_end_dt = min(current_dt + timedelta(days=chunk_days), today_dt)
        rows = await asyncio.to_thread(provider.get_historical_ohlcv, ticker, current_dt, chunk_end_dt)
        await _normalize_and_upsert_ohlcv(db, ticker, rows)
        total_rows += len(rows)
        current_dt = chunk_end_dt + timedelta(days=1)
        await asyncio.sleep(1.5)
    return total_rows

async def run_market_data_refresh(app=None, send_digest=False):
    """Market data refresh: Uses Breeze if session available, fallback to yfinance."""
    from core.providers import YFinanceProvider, BreezeProvider
    from algo.screener import run_screener
    from algo.action_classifier import get_stock_action
    from core.breeze_client import BreezeClient, SessionExpiredError
    from core.session_store import get_session
    import pandas as pd

    provider = YFinanceProvider()
    source_name = "yfinance"

    token = await get_session()
    if token:
        try:
            breeze = BreezeClient(token)
            provider = BreezeProvider(breeze)
            source_name = "breeze"
        except SessionExpiredError:
            logger.info("Breeze session expired, falling back to yfinance")

    logger.info(f"Starting run_market_data_refresh ({source_name})")
    try:
        pool = get_pool()
        async with pool.acquire() as db:
            holdings_rows = await db.fetch("SELECT stock_code FROM holdings_snapshot")
            holdings = [r["stock_code"] for r in holdings_rows]
            watchlist_rows = await db.fetch("SELECT stock_code FROM watchlist")
            watchlist = [r["stock_code"] for r in watchlist_rows]

            all_tickers = list(set(holdings + watchlist + ["_NIFTY50"]))

            for ticker in all_tickers:
                max_date = await db.fetchval("SELECT MAX(date) FROM ohlcv_cache WHERE stock_code = $1", ticker)

                if max_date is None:
                    try:
                        await backfill_ticker_history(db, ticker, provider)
                    except Exception as e:
                        logger.error(f"{source_name} backfill failed for {ticker}: {e}")
                        await db.execute(
                            "INSERT INTO data_health (stock_code, source, status, message) VALUES ($1, $2, 'failed', $3)",
                            ticker, source_name, str(e),
                        )
                else:
                    try:
                        from_dt = datetime.strptime(max_date[:10], "%Y-%m-%d")
                    except Exception:
                        from_dt = datetime.now() - timedelta(days=365)
                    from_dt += timedelta(days=1)
                    to_dt = datetime.now()
                    if from_dt <= to_dt:
                        try:
                            rows = await asyncio.to_thread(provider.get_historical_ohlcv, ticker, from_dt, to_dt)
                            await _normalize_and_upsert_ohlcv(db, ticker, rows)
                        except Exception as e:
                            logger.error(f"{source_name} fetch failed for {ticker}: {e}")
                            await db.execute(
                                "INSERT INTO data_health (stock_code, source, status, message) VALUES ($1, $2, 'failed', $3)",
                                ticker, source_name, str(e),
                            )

                # Keep current_price fresh for the Portfolio auto-refresh (holdings only).
                if ticker in holdings:
                    quote = await asyncio.to_thread(provider.get_quote, ticker)
                    if quote and quote.get("ltp"):
                        await db.execute(
                            "UPDATE holdings_snapshot SET current_price = $1, timestamp = NOW() WHERE stock_code = $2",
                            quote["ltp"], ticker,
                        )

                await asyncio.sleep(1)

            # 2. Run screener
            signals = await run_screener()

            # 3. Run Action Classifier
            nifty_rows = await db.fetch(
                "SELECT stock_code, date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date"
            )
            nifty_df = pd.DataFrame(nifty_rows, columns=['stock_code', 'date', 'open', 'high', 'low', 'close', 'volume'])

            action_inserts = []
            stage_inserts = []
            for ticker in list(set(holdings + watchlist)):
                stock_rows = await db.fetch(
                    "SELECT stock_code, date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = $1 ORDER BY date",
                    ticker,
                )
                if stock_rows:
                    stock_df = pd.DataFrame(stock_rows, columns=['stock_code', 'date', 'open', 'high', 'low', 'close', 'volume'])
                    res = get_stock_action(ticker, stock_df, nifty_df, ticker in holdings)
                    action_inserts.append((ticker, res['action'], res['rationale']))

                    last_date = stock_df.iloc[-1]['date']
                    date_str = last_date[:10]
                    stage_inserts.append((ticker, date_str, res['stage'], res['sma_150'], res['slope']))

            if action_inserts:
                async with db.transaction():
                    await db.execute("DELETE FROM stock_actions")
                    await db.executemany(
                        "INSERT INTO stock_actions (stock_code, action, rationale) VALUES ($1, $2, $3)",
                        action_inserts,
                    )

            if stage_inserts:
                await db.executemany("""
                    INSERT INTO stage_history (stock_code, date, stage, sma_150, slope)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (stock_code, date) DO UPDATE SET
                        stage=excluded.stage, sma_150=excluded.sma_150, slope=excluded.slope
                """, stage_inserts)

            # Record heartbeat
            await db.execute("INSERT INTO job_heartbeats (job_name) VALUES ($1)", "run_market_data_refresh")

            # 4. Format HTML digest and broadcast ONLY IF requested (daily digest)
            if send_digest and app:
                holdings_rows2 = await db.fetch("SELECT stock_code, quantity, average_price, current_price FROM holdings_snapshot")
                holdings_list = [{"stock_code": r["stock_code"], "quantity": r["quantity"], "average_price": r["average_price"], "current_price": r["current_price"]} for r in holdings_rows2]

                port_msg = format_portfolio_message(holdings_list)
                sig_msg = format_signals_message(signals)

                action_msg = ""
                if action_inserts:
                    action_msg = "<b>Action Plan Alerts:</b>\n"
                    for r in action_inserts:
                        if "SELL" in r[1] or "TRIM" in r[1]:
                            action_msg += f"🚨 <b>{r[0]}</b>: {r[1]} - <i>{r[2]}</i>\n"

                digest = f"{port_msg}\n\n{sig_msg}\n\n{action_msg}"
                await broadcast_message(app, digest)

    except Exception:
        logger.exception("Error in run_market_data_refresh")
