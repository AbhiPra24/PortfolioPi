"""Automated pipelines for Breeze ground-truth sync and Market Data refresh."""

import asyncio
import logging
from datetime import datetime, timedelta

import aiosqlite
import dateutil.parser
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from algo.screener import run_screener
from bot.broadcaster import broadcast_message
from bot.formatters import format_portfolio_message, format_signals_message
from app_config import settings
from core.breeze_client import BreezeClient, SessionExpiredError
from core.session_store import get_session
from core.market_data import get_market_data

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
                        VALUES (?, ?, ?, 0)
                        ON CONFLICT(stock_code) DO UPDATE SET
                            quantity=excluded.quantity,
                            average_price=excluded.average_price
                    """, (stock, data["quantity"], data["average_price"]))
                await db.commit()

            # 2. Fetch Watchlist
            async with db.execute("SELECT stock_code FROM watchlist") as cursor:
                watchlist = [row[0] for row in await cursor.fetchall()]

            all_tickers = list(set(list(holdings_data.keys()) + watchlist))

            # 3. Resolve Mappings Opportunistically
            new_mappings_count = 0
            for ticker in all_tickers:
                async with db.execute("SELECT nse_symbol FROM ticker_mapping WHERE stock_code = ?", (ticker,)) as cursor:
                    mapping = await cursor.fetchone()
                
                if not mapping:
                    try:
                        res = await asyncio.to_thread(get_names_with_retry, breeze, ticker)
                        if isinstance(res, dict) and res.get("Success"):
                            success_list = res.get("Success", [])
                            if success_list:
                                item = success_list[0]
                                nse_symbol = item.get("short_name", ticker) # fallback to ticker if short_name is empty
                                isin = item.get("isin", "")
                                await db.execute("INSERT OR REPLACE INTO ticker_mapping (stock_code, nse_symbol, isin) VALUES (?, ?, ?)", (ticker, nse_symbol, isin))
                                await db.commit()
                                new_mappings_count += 1
                                logger.info(f"Resolved mapping for {ticker}: {nse_symbol} ({isin})")
                    except Exception as e:
                        logger.error(f"Failed to resolve mapping for {ticker}: {e}")
                    await asyncio.sleep(1)

            # _NIFTY50 is built-in
            await db.execute("INSERT OR IGNORE INTO ticker_mapping (stock_code, nse_symbol, isin) VALUES ('_NIFTY50', 'NIFTY 50', '')")
            await db.commit()

            all_tickers.append("_NIFTY50")

            # 4. Backfill OHLCV cache (Breeze has better historical depth)
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

            # 5. Record heartbeat
            await db.execute("INSERT INTO job_heartbeats (job_name) VALUES (?)", ("run_breeze_sync",))
            
            # Record explicit sync timestamp in another table or just rely on job_heartbeats
            await db.commit()
            
            if not silent and app:
                # Return summary to the user if requested (like from /refresh_session)
                total_holdings = len(holdings_data)
                async with db.execute("SELECT SUM(quantity * current_price) FROM holdings_snapshot") as cur:
                    total_val_row = await cur.fetchone()
                    total_val = total_val_row[0] if total_val_row and total_val_row[0] else 0.0
                
                msg = f"✅ Session verified.\nPortfolio synced: {total_holdings} holdings, ₹{total_val:,.2f} current value, {new_mappings_count} new tickers mapped."
                await broadcast_message(app, msg)

    except Exception as e:
        logger.exception("Error in run_breeze_sync")
        import html
        if not silent and app: await broadcast_message(app, f"🚨 Unhandled Error in Breeze Sync:\n{html.escape(str(e))}")


async def run_market_data_refresh(app=None, send_digest=False):
    """
    Market Data refresh using yfinance. Runs independently of Breeze session.
    Updates current prices and runs screener/action-plan.
    """
    logger.info("Starting run_market_data_refresh")
    try:
        async with aiosqlite.connect(settings.db_path) as db:
            # 1. Fetch all mapped tickers for holdings and watchlist
            async with db.execute("""
                SELECT DISTINCT m.stock_code, m.nse_symbol 
                FROM ticker_mapping m
                LEFT JOIN holdings_snapshot h ON m.stock_code = h.stock_code
                LEFT JOIN watchlist w ON m.stock_code = w.stock_code
                WHERE h.stock_code IS NOT NULL OR w.stock_code IS NOT NULL OR m.stock_code = '_NIFTY50'
            """) as cursor:
                tickers_to_fetch = await cursor.fetchall()
            
            for stock_code, nse_symbol in tickers_to_fetch:
                # Fetch from alternative source
                market_data = await get_market_data(nse_symbol)
                if not market_data:
                    continue # Skip gracefully on failure
                
                # Update current price in holdings
                if stock_code != "_NIFTY50":
                    await db.execute("""
                        UPDATE holdings_snapshot 
                        SET current_price = ?, timestamp = CURRENT_TIMESTAMP
                        WHERE stock_code = ?
                    """, (market_data["current_price"], stock_code))
                
                # Append recent OHLCV
                if market_data["ohlcv"]:
                    records = []
                    for row in market_data["ohlcv"]:
                        records.append((
                            stock_code,
                            row["date"],
                            row["open"],
                            row["high"],
                            row["low"],
                            row["close"],
                            row["volume"]
                        ))
                    await db.executemany("""
                        INSERT OR IGNORE INTO ohlcv_cache 
                        (stock_code, date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, records)
                await db.commit()
                await asyncio.sleep(0.5)

            # 2. Run screener
            signals = await run_screener()

            # 3. Run Action Classifier
            async with db.execute("SELECT stock_code, date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date") as cursor:
                nifty_rows = await cursor.fetchall()
                nifty_df = pd.DataFrame(nifty_rows, columns=['stock_code', 'date', 'open', 'high', 'low', 'close', 'volume'])
                
            action_inserts = []
            stage_inserts = []
            
            async with db.execute("SELECT stock_code FROM holdings_snapshot") as cur:
                held_tickers = {row[0] for row in await cur.fetchall()}
                
            for stock_code, _ in tickers_to_fetch:
                if stock_code == "_NIFTY50": continue
                async with db.execute("SELECT stock_code, date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date", (stock_code,)) as cursor:
                    stock_rows = await cursor.fetchall()
                    if len(stock_rows) > 0:
                        stock_df = pd.DataFrame(stock_rows, columns=['stock_code', 'date', 'open', 'high', 'low', 'close', 'volume'])
                        from algo.action_classifier import get_stock_action
                        is_holding = stock_code in held_tickers
                        res = get_stock_action(stock_code, stock_df, nifty_df, is_holding)
                        
                        action_inserts.append((stock_code, res['action'], res['rationale']))
                        last_date = stock_df.iloc[-1]['date']
                        stage_inserts.append((stock_code, last_date, res['stage'], res['sma_150'], res['slope']))

            if action_inserts:
                await db.execute("DELETE FROM stock_actions")
                await db.executemany("""
                    INSERT INTO stock_actions (stock_code, action, rationale)
                    VALUES (?, ?, ?)
                """, action_inserts)
                
            if stage_inserts:
                await db.executemany("""
                    INSERT OR REPLACE INTO stage_history (stock_code, date, stage, sma_150, slope)
                    VALUES (?, ?, ?, ?, ?)
                """, stage_inserts)
            
            await db.commit()
            
            # Record heartbeat
            await db.execute("INSERT INTO job_heartbeats (job_name) VALUES (?)", ("run_market_data_refresh",))
            await db.commit()

            # 4. Format HTML digest and broadcast ONLY IF requested (daily digest)
            if send_digest and app:
                async with db.execute("SELECT stock_code, quantity, average_price, current_price FROM holdings_snapshot") as cur:
                    holdings_list = [{"stock_code": r[0], "quantity": r[1], "average_price": r[2], "current_price": r[3]} for r in await cur.fetchall()]
                    
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

    except Exception as e:
        logger.exception("Error in run_market_data_refresh")

