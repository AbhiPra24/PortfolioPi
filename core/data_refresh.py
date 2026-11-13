"""Automated pipelines for Breeze ground-truth sync and Market Data refresh."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite
import dateutil.parser
import pandas as pd
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application
from tenacity import retry, stop_after_attempt, wait_exponential

from algo.liquid_float_sweeper import calculate_paused_sip_float_async
from algo.screener import run_screener
from algo.trend_template import check_trend_template
from app_config import settings
from bot.broadcaster import broadcast_message
from bot.formatters import (
    format_morning_brief,
    format_premarket_opportunities_message,
)
from core.breeze_client import BreezeClient, SessionExpiredError
from core.portfolio_analytics import update_portfolio_risk_metrics
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
        product_type="cash",
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def get_names_with_retry(breeze, stock_code):
    exchange = "NSE"
    return breeze.get_names(exchange_code=exchange, stock_code=stock_code)


async def sync_breeze_trades_and_orders(breeze: BreezeClient) -> dict:
    """
    Fetch last 7 days of trades and last 5 days of orders from Breeze API,
    and persist/upsert them into trades_history and orders_history.
    """
    stats = {"trades_synced": 0, "orders_synced": 0}
    now = datetime.now(timezone.utc)

    # 1. Fetch trades (last 7 days)
    trade_from = (now - timedelta(days=7)).strftime("%Y-%m-%dT06:00:00.000Z")
    trade_to = now.strftime("%Y-%m-%dT06:00:00.000Z")
    try:
        trades_resp = await asyncio.to_thread(
            breeze.get_trade_list, from_date=trade_from, to_date=trade_to, exchange_code="NSE"
        )
    except Exception as e:
        logger.error(f"Failed to fetch trades from Breeze: {e}")
        trades_resp = None

    # 2. Fetch orders (last 5 days)
    order_from = (now - timedelta(days=5)).strftime("%Y-%m-%dT06:00:00.000Z")
    order_to = now.strftime("%Y-%m-%dT06:00:00.000Z")
    try:
        orders_resp = await asyncio.to_thread(
            breeze.get_order_list, from_date=order_from, to_date=order_to, exchange_code="NSE"
        )
    except Exception as e:
        logger.error(f"Failed to fetch orders from Breeze: {e}")
        orders_resp = None

    async with aiosqlite.connect(settings.db_path) as db:
        if isinstance(trades_resp, dict) and trades_resp.get("Success"):
            trades = trades_resp.get("Success", [])
            for idx, t in enumerate(trades):
                trade_id = str(t.get("trade_id") or t.get("order_id") or f"trade_{idx}").strip()
                if not trade_id:
                    continue
                order_id = str(t.get("order_id") or "").strip()
                trade_date = str(t.get("trade_date") or "").strip()
                stock_code = str(t.get("stock_code") or "").strip()
                action = str(t.get("action") or "").strip()
                try:
                    quantity = int(t.get("quantity", 0) or 0)
                except (ValueError, TypeError):
                    quantity = 0
                try:
                    average_cost = float(t.get("average_cost", 0) or 0)
                except (ValueError, TypeError):
                    average_cost = 0.0
                try:
                    ltp = float(t.get("ltp", 0) or 0)
                except (ValueError, TypeError):
                    ltp = 0.0
                try:
                    brokerage_amount = float(t.get("brokerage_amount", 0) or 0)
                except (ValueError, TypeError):
                    brokerage_amount = 0.0
                try:
                    total_taxes = float(t.get("total_taxes", 0) or 0)
                except (ValueError, TypeError):
                    total_taxes = 0.0
                product_type = str(t.get("product_type") or "").strip()
                exchange_code = str(t.get("exchange_code") or "NSE").strip()

                await db.execute(
                    """
                    INSERT INTO trades_history (
                        order_id, trade_id, trade_date, stock_code, action, quantity,
                        average_cost, ltp, brokerage_amount, total_taxes, product_type, exchange_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(trade_id) DO UPDATE SET
                        order_id=excluded.order_id,
                        trade_date=excluded.trade_date,
                        stock_code=excluded.stock_code,
                        action=excluded.action,
                        quantity=excluded.quantity,
                        average_cost=excluded.average_cost,
                        ltp=excluded.ltp,
                        brokerage_amount=excluded.brokerage_amount,
                        total_taxes=excluded.total_taxes,
                        product_type=excluded.product_type,
                        exchange_code=excluded.exchange_code
                """,
                    (
                        order_id,
                        trade_id,
                        trade_date,
                        stock_code,
                        action,
                        quantity,
                        average_cost,
                        ltp,
                        brokerage_amount,
                        total_taxes,
                        product_type,
                        exchange_code,
                    ),
                )
                stats["trades_synced"] += 1
            await db.commit()

        if isinstance(orders_resp, dict) and orders_resp.get("Success"):
            orders = orders_resp.get("Success", [])
            for o in orders:
                order_id = str(o.get("order_id") or "").strip()
                if not order_id:
                    continue
                order_datetime = str(o.get("order_datetime") or "").strip()
                stock_code = str(o.get("stock_code") or "").strip()
                action = str(o.get("action") or "").strip()
                order_type = str(o.get("order_type") or "").strip()
                try:
                    quantity = int(o.get("quantity", 0) or 0)
                except (ValueError, TypeError):
                    quantity = 0
                try:
                    price = float(o.get("price", 0) or 0)
                except (ValueError, TypeError):
                    price = 0.0
                try:
                    average_price = float(o.get("average_price", 0) or 0)
                except (ValueError, TypeError):
                    average_price = 0.0
                status = str(o.get("status") or "").strip()
                exchange_code = str(o.get("exchange_code") or "NSE").strip()

                await db.execute(
                    """
                    INSERT INTO orders_history (
                        order_id, order_datetime, stock_code, action, order_type,
                        quantity, price, average_price, status, exchange_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        order_datetime=excluded.order_datetime,
                        stock_code=excluded.stock_code,
                        action=excluded.action,
                        order_type=excluded.order_type,
                        quantity=excluded.quantity,
                        price=excluded.price,
                        average_price=excluded.average_price,
                        status=excluded.status,
                        exchange_code=excluded.exchange_code,
                        updated_at=CURRENT_TIMESTAMP
                """,
                    (
                        order_id,
                        order_datetime,
                        stock_code,
                        action,
                        order_type,
                        quantity,
                        price,
                        average_price,
                        status,
                        exchange_code,
                    ),
                )
                stats["orders_synced"] += 1
            await db.commit()

    logger.info(
        f"Breeze trades & orders sync complete: {stats['trades_synced']} trades, {stats['orders_synced']} orders processed."
    )
    return stats


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
            if not silent and app:
                await broadcast_message(app, "🚨 Sync Failed: Session missing.")
            return

        try:
            breeze = BreezeClient(token)
        except SessionExpiredError:
            logger.info("run_breeze_sync skipped: Session expired.")
            if not silent and app:
                await broadcast_message(app, "🚨 Sync Failed: Session expired.")
            return

        async with aiosqlite.connect(settings.db_path) as db:
            # 1. Fetch Holdings
            holdings = await asyncio.to_thread(breeze.get_demat_holdings)
            port_holdings = await asyncio.to_thread(
                breeze.get_portfolio_holding, exchange_code="NSE", from_date="", to_date=""
            )

            holdings_data = {}
            if isinstance(holdings, dict) and holdings.get("Success"):
                for item in holdings.get("Success", []):
                    stock = item.get("stock_code")
                    if stock:
                        holdings_data[stock] = {
                            "stock_code": stock,
                            "quantity": int(item.get("quantity", 0) or 0),
                            "average_price": float(item.get("average_price", 0) or 0),
                        }
            if isinstance(port_holdings, dict) and port_holdings.get("Success"):
                for item in port_holdings.get("Success", []):
                    stock = item.get("stock_code")
                    if stock:
                        if stock in holdings_data:
                            holdings_data[stock]["quantity"] = max(
                                holdings_data[stock]["quantity"], int(item.get("quantity", 0) or 0)
                            )
                            holdings_data[stock]["average_price"] = float(
                                item.get("average_price", 0) or holdings_data[stock]["average_price"]
                            )
                        else:
                            holdings_data[stock] = {
                                "stock_code": stock,
                                "quantity": int(item.get("quantity", 0) or 0),
                                "average_price": float(item.get("average_price", 0) or 0),
                            }

            # We DO NOT update current_price here anymore, only quantity and avg price. We rely on market_data_job for price.
            # But we must insert if it's new.
            if holdings_data:
                for stock, data in holdings_data.items():
                    # Insert or update holding ground truth
                    await db.execute(
                        """
                        INSERT INTO holdings_snapshot (stock_code, quantity, average_price, current_price)
                        VALUES (?, ?, ?, 0)
                        ON CONFLICT(stock_code) DO UPDATE SET
                            quantity=excluded.quantity,
                            average_price=excluded.average_price
                    """,
                        (stock, data["quantity"], data["average_price"]),
                    )
                await db.commit()

                # Record portfolio history (invested vs current value)
                async with db.execute(
                    "SELECT SUM(quantity * average_price), SUM(quantity * current_price) FROM holdings_snapshot"
                ) as cur:
                    totals_row = await cur.fetchone()
                if totals_row and totals_row[0] is not None and totals_row[1] is not None:
                    tot_invested, tot_current = totals_row[0], totals_row[1]
                    await db.execute(
                        """
                        INSERT INTO portfolio_value_history (total_invested, total_current_value, total_pnl)
                        VALUES (?, ?, ?)
                    """,
                        (tot_invested, tot_current, tot_current - tot_invested),
                    )
                    await db.commit()

                    # Trigger portfolio risk metrics computation
                    try:
                        await update_portfolio_risk_metrics(db)
                    except Exception as e:
                        logger.warning(f"Could not update portfolio risk metrics during breeze sync: {e}")

            # 2. Opportunistic Ticker Mapping
            new_mappings_count = 0
            for ticker in holdings_data.keys():
                async with db.execute("SELECT nse_symbol FROM ticker_mapping WHERE stock_code = ?", (ticker,)) as cur:
                    mapping = await cur.fetchone()
                if not mapping:
                    try:
                        names_data = await asyncio.to_thread(get_names_with_retry, breeze, ticker)
                        if isinstance(names_data, dict) and names_data.get("Success"):
                            first_match = names_data["Success"][0]
                            nse_sym = first_match.get("exchange_stock_code") or ticker
                            isin = first_match.get("isincode") or ""
                            if nse_sym:
                                await db.execute(
                                    """
                                    INSERT INTO ticker_mapping (stock_code, nse_symbol, isin) VALUES (?, ?, ?)
                                    ON CONFLICT (stock_code) DO NOTHING
                                """,
                                    (ticker, nse_sym, isin),
                                )
                                await db.commit()
                                new_mappings_count += 1
                                logger.info(f"Resolved new ticker mapping: {ticker} -> {nse_sym}")
                    except Exception as e:
                        logger.warning(f"Could not resolve mapping for {ticker}: {e}")

            # 3. Incremental OHLCV Backfill via Breeze
            # Only fetch what is missing to avoid rate limits
            today_dt = datetime.now()
            for ticker in list(holdings_data.keys()) + ["_NIFTY50"]:
                async with db.execute("SELECT MAX(date) FROM ohlcv_cache WHERE stock_code = ?", (ticker,)) as cur:
                    max_date_row = await cur.fetchone()
                max_date = max_date_row[0] if max_date_row else None

                if not max_date:
                    from_dt = today_dt - timedelta(days=365)
                else:
                    try:
                        from_dt = datetime.strptime(max_date[:10], "%Y-%m-%d")
                    except Exception:
                        from_dt = today_dt - timedelta(days=365)

                # Breeze requires string format YYYY-MM-DDTHH:MM:SS.000Z
                from_dt += timedelta(days=1)
                if from_dt <= today_dt:
                    from_str = from_dt.strftime("%Y-%m-%dT00:00:00.000Z")
                    to_str = today_dt.strftime("%Y-%m-%dT00:00:00.000Z")

                    try:
                        hist_data = await asyncio.to_thread(get_historical_with_retry, breeze, ticker, from_str, to_str)
                        if isinstance(hist_data, dict) and hist_data.get("Success"):
                            records = []
                            for row in hist_data.get("Success", []):
                                dt = dateutil.parser.parse(row["datetime"])
                                date_str = dt.strftime("%Y-%m-%d")
                                records.append(
                                    (
                                        ticker,
                                        date_str,
                                        float(row["open"]),
                                        float(row["high"]),
                                        float(row["low"]),
                                        float(row["close"]),
                                        int(row["volume"]),
                                    )
                                )
                            if records:
                                await db.executemany(
                                    """
                                    INSERT OR IGNORE INTO ohlcv_cache
                                    (stock_code, date, open, high, low, close, volume)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                    records,
                                )
                                await db.commit()
                        else:
                            await db.execute(
                                "INSERT INTO data_health (stock_code, source, status, message) VALUES (?, 'breeze', 'failed', ?)",
                                (ticker, str(hist_data.get("Error") or "No data returned")),
                            )
                            await db.commit()
                    except Exception as e:
                        logger.error(f"Breeze fetch failed for {ticker}: {e}")
                        await db.execute(
                            "INSERT INTO data_health (stock_code, source, status, message) VALUES (?, 'breeze', 'failed', ?)",
                            (ticker, str(e)),
                        )
                        await db.commit()

                await asyncio.sleep(1)

            # 4. Synchronize Trades and Orders History
            try:
                await sync_breeze_trades_and_orders(breeze)
            except Exception as e:
                logger.error(f"Error during trades/orders sync in run_breeze_sync: {e}")

            # Record heartbeat
            await db.execute("INSERT INTO job_heartbeats (job_name) VALUES (?)", ("run_breeze_sync",))
            await db.commit()

            if not silent and app:
                # Return summary to the user if requested (like from /refresh_session)
                total_holdings = len(holdings_data)
                async with db.execute("SELECT SUM(quantity * current_price) FROM holdings_snapshot") as cur:
                    total_val_row = await cur.fetchone()
                total_val = total_val_row[0] if (total_val_row and total_val_row[0]) else 0.0

                msg = f"✅ Session verified.\nPortfolio synced: {total_holdings} holdings, ₹{total_val:,.2f} current value, {new_mappings_count} new tickers mapped."
                await broadcast_message(app, msg)

    except Exception as e:
        logger.exception("Error in run_breeze_sync")
        import html

        if not silent and app:
            await broadcast_message(app, f"🚨 Unhandled Error in Breeze Sync:\n{html.escape(str(e))}")


async def _normalize_and_upsert_ohlcv(db, ticker, rows):
    if not rows:
        return
    records = [(ticker, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows]
    await db.executemany(
        """
        INSERT OR IGNORE INTO ohlcv_cache (stock_code, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        records,
    )
    await db.commit()


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
    from algo.action_classifier import get_stock_action
    from core.breeze_client import BreezeClient, SessionExpiredError
    from core.providers import BreezeProvider, YFinanceProvider
    from core.session_store import get_session

    provider = YFinanceProvider()
    source_name = "yfinance"

    token = await get_session()
    if token:
        try:
            breeze = BreezeClient(token)
            provider = BreezeProvider(breeze)
            source_name = "breeze"
            logger.info("Using BreezeProvider for Market Data Refresh.")
        except SessionExpiredError:
            logger.info("Breeze session expired. Using YFinanceProvider for Market Data Refresh.")
    else:
        logger.info("No Breeze session. Using YFinanceProvider for Market Data Refresh.")

    try:
        async with aiosqlite.connect(settings.db_path) as db:
            # 1. Fetch active targets: holdings + watchlist + index
            async with db.execute("SELECT stock_code FROM holdings_snapshot") as cur:
                holdings = [r[0] for r in await cur.fetchall()]
            async with db.execute("SELECT stock_code FROM watchlist") as cur:
                watchlist = [r[0] for r in await cur.fetchall()]

            all_tickers = list(set(holdings + watchlist + ["_NIFTY50"]))

            # Refresh OHLCV incremental
            for ticker in all_tickers:
                async with db.execute("SELECT MAX(date) FROM ohlcv_cache WHERE stock_code = ?", (ticker,)) as cur:
                    max_date_row = await cur.fetchone()
                max_date = max_date_row[0] if max_date_row else None

                if not max_date:
                    # Never fetched before — do a full 3-year chunked backfill
                    logger.info(f"Zero OHLCV rows for {ticker} — triggering full backfill via {source_name}...")
                    try:
                        n = await backfill_ticker_history(db, ticker, provider)
                        logger.info(f"Backfilled {n} rows for {ticker}")
                    except Exception as e:
                        logger.error(f"Backfill failed for {ticker}: {e}")
                        await db.execute(
                            "INSERT INTO data_health (stock_code, source, status, message) VALUES (?, ?, 'failed', ?)",
                            (ticker, source_name, str(e)),
                        )
                        await db.commit()
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
                                "INSERT INTO data_health (stock_code, source, status, message) VALUES (?, ?, 'failed', ?)",
                                (ticker, source_name, str(e)),
                            )
                            await db.commit()

                # Keep current_price fresh for the Portfolio auto-refresh (holdings only).
                if ticker in holdings:
                    quote = await asyncio.to_thread(provider.get_quote, ticker)
                    if quote and quote.get("ltp"):
                        await db.execute(
                            "UPDATE holdings_snapshot SET current_price = ?, timestamp = CURRENT_TIMESTAMP WHERE stock_code = ?",
                            (quote["ltp"], ticker),
                        )
                        await db.commit()

                await asyncio.sleep(1)

            # 2. Run screener
            await run_screener()

            # 3. Run Action Classifier
            async with db.execute(
                "SELECT stock_code, date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date DESC LIMIT 300"
            ) as cur:
                nifty_rows = await cur.fetchall()
            nifty_df = pd.DataFrame(
                list(reversed(nifty_rows)), columns=["stock_code", "date", "open", "high", "low", "close", "volume"]
            )
            for col in ["open", "high", "low", "close", "volume"]:
                nifty_df[col] = pd.to_numeric(nifty_df[col], errors="coerce")

            action_inserts = []
            stage_inserts = []
            for ticker in list(set(holdings + watchlist)):
                async with db.execute(
                    "SELECT stock_code, date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date DESC LIMIT 300",
                    (ticker,),
                ) as cur:
                    stock_rows = await cur.fetchall()
                if stock_rows:
                    stock_df = pd.DataFrame(
                        list(reversed(stock_rows)),
                        columns=["stock_code", "date", "open", "high", "low", "close", "volume"],
                    )
                    for col in ["open", "high", "low", "close", "volume"]:
                        stock_df[col] = pd.to_numeric(stock_df[col], errors="coerce")
                    res = get_stock_action(ticker, stock_df, nifty_df, ticker in holdings)
                    action_inserts.append((ticker, res["action"], res["rationale"]))

                    last_date = stock_df.iloc[-1]["date"]
                    date_str = last_date[:10]
                    stage_inserts.append((ticker, date_str, res["stage"], res["sma_150"], res["slope"]))

            if action_inserts:
                await db.execute("DELETE FROM stock_actions")
                await db.executemany(
                    "INSERT INTO stock_actions (stock_code, action, rationale) VALUES (?, ?, ?)",
                    action_inserts,
                )

            if stage_inserts:
                await db.executemany(
                    """
                    INSERT OR REPLACE INTO stage_history (stock_code, date, stage, sma_150, slope)
                    VALUES (?, ?, ?, ?, ?)
                """,
                    stage_inserts,
                )

            # Clean up old signals and heartbeats to conserve database storage and reduce backup egress
            await db.execute("DELETE FROM signals WHERE timestamp < datetime('now', '-30 days')")
            await db.execute("DELETE FROM job_heartbeats WHERE timestamp < datetime('now', '-7 days')")
            await db.commit()

            # Update institutional downside risk metrics
            try:
                await update_portfolio_risk_metrics(db)
            except Exception as e:
                logger.warning(f"Could not update portfolio risk metrics during market refresh: {e}")

            # Record heartbeat
            await db.execute("INSERT INTO job_heartbeats (job_name) VALUES (?)", ("run_market_data_refresh",))
            await db.commit()

            # 4. Format HTML digest and broadcast ONLY IF requested (daily digest)
            if send_digest and app:
                async with db.execute(
                    "SELECT stock_code, quantity, average_price, current_price, asset_class FROM holdings_snapshot"
                ) as cur:
                    holdings_list = [
                        {
                            "stock_code": r[0],
                            "quantity": r[1],
                            "average_price": r[2],
                            "current_price": r[3],
                            "asset_class": r[4] if len(r) > 4 else "EQUITY",
                        }
                        for r in await cur.fetchall()
                    ]

                total_val = sum(h.get("current_price", 0.0) * h.get("quantity", 0) for h in holdings_list)
                total_cost = sum(h.get("average_price", 0.0) * h.get("quantity", 0) for h in holdings_list)
                pnl = total_val - total_cost
                pnl_pct = (pnl / total_cost * 100.0) if total_cost > 0 else 0.0

                # Fetch available cash balance from Breeze client if available
                available_cash = 0.0
                try:
                    token = await get_session()
                    if token:
                        breeze_inst = BreezeClient(token)
                        funds_data = await asyncio.to_thread(breeze_inst.get_funds)
                        if funds_data.get("Success"):
                            available_cash = float(funds_data["Success"].get("unallocated_balance", 0.0) or 0.0)
                except Exception as cash_err:
                    logger.debug(f"Could not retrieve cash balance for morning brief: {cash_err}")

                portfolio_summary = {
                    "current_val": total_val,
                    "total_cost": total_cost,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "cash": available_cash,
                }

                # Lookup mappings
                action_lookup = {r[0]: {"action": r[1], "rationale": r[2]} for r in action_inserts}
                stage_lookup = {r[0]: r[2] for r in stage_inserts}

                # Audited holdings categorized by defensive action
                audited_holdings = []
                stage_2_count = 0
                stage_1_count = 0
                yield_or_macro_count = 0

                for h in holdings_list:
                    tkr = h["stock_code"]
                    qty = h.get("quantity", 0)
                    cost = h.get("average_price", 0.0)
                    price = h.get("current_price", 0.0) or cost
                    pnl_item_pct = ((price - cost) / cost * 100.0) if cost else 0.0
                    weight_pct = ((price * qty) / total_val * 100.0) if total_val else 0.0

                    act_row = action_lookup.get(tkr, {})
                    raw_action = act_row.get("action", "HOLD")
                    rationale = act_row.get("rationale", "")
                    stage_num = stage_lookup.get(tkr, 1)

                    # Stage counts for healthy baseline
                    if stage_num == 2:
                        stage_2_count += 1
                    elif stage_num == 1:
                        stage_1_count += 1
                    else:
                        yield_or_macro_count += 1

                    # Normalize action to morning brief categories: EXIT, TRIM, BUY_MORE, HOLD
                    norm_action = "HOLD"
                    directive = rationale
                    if "SELL" in raw_action:
                        norm_action = "EXIT"
                        directive = f"Stage {stage_num} breakdown / Stop breached. {rationale}"
                    elif "TRIM" in raw_action or weight_pct > 10.0 or (pnl_item_pct > 50.0 and stage_num == 3):
                        norm_action = "TRIM"
                        if weight_pct > 10.0:
                            directive = f"Overweight ({weight_pct:.1f}% of portfolio). Lock gains."
                        elif pnl_item_pct > 50.0:
                            directive = f"+{pnl_item_pct:.1f}% gain. Lock profit on Stage 3."
                        else:
                            directive = rationale
                    elif "BUY" in raw_action and stage_num == 2:
                        norm_action = "BUY_MORE"
                        directive = "Stage 2 pullback / institutional support bounce."

                    audited_holdings.append({
                        "ticker": tkr,
                        "qty": qty,
                        "price": price,
                        "action": norm_action,
                        "directive": directive,
                        "rationale": rationale,
                    })

                # Check paused SIPs
                sip_changes = []
                try:
                    async with db.execute(
                        "SELECT ticker, target_action, quant_stage FROM sip_book WHERE UPPER(TRIM(target_action)) IN ('TRIM / PAUSE SIP', 'SELL / STOP SIP') OR quant_stage IN (3, 4)"
                    ) as cur:
                        paused_sips = await cur.fetchall()
                        for ps in paused_sips:
                            sip_changes.append(f"{ps[0]} (Stage {ps[2]} distribution)")
                except Exception as sip_err:
                    logger.debug(f"SIP check skipped: {sip_err}")

                # Check liquid float sweep eligibility (>= 5000 unallocated cash)
                cash_sweep_info = None
                paused_sip_amt = await calculate_paused_sip_float_async(db)
                total_float = available_cash + paused_sip_amt
                if total_float >= 5000.0:
                    cash_sweep_info = {
                        "eligible": True,
                        "amount": total_float / 1000.0,
                        "target": "LIQUIDBEES",
                    }

                stage_breakdown = {
                    "stage_2": stage_2_count,
                    "stage_1": stage_1_count,
                    "yield_or_macro": yield_or_macro_count,
                }

                # Construct Exception-driven Morning Brief
                morning_msg = format_morning_brief(
                    portfolio_summary=portfolio_summary,
                    audited_holdings=audited_holdings,
                    sip_changes=sip_changes,
                    stage_breakdown=stage_breakdown,
                    cash_sweep_info=cash_sweep_info,
                )

                # Attach 1-tap quick action drilldowns
                inline_buttons = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📋 Action Plan", callback_data="action_plan"),
                        InlineKeyboardButton("🎯 Watchlist Signals", callback_data="signals"),
                    ]
                ])

                await broadcast_message(app, morning_msg, reply_markup=inline_buttons)

    except Exception:
        logger.exception("Error in run_market_data_refresh")


async def run_premarket_opportunities(app: Application):
    """09:00 AM IST Pre-market Deployment Job.

    Strictly Offense & Deployment:
    - Only fires if deployable cash >= ₹5,000.
    - Filters exclusively unowned watchlist candidates meeting Minervini 7/7 + volume expansion criteria.
    - Suppressed if no deployable cash or no qualifying candidates.
    """
    logger.info("Evaluating 09:00 AM pre-market opportunities...")
    try:
        available_cash = 0.0
        try:
            token = await get_session()
            if token:
                breeze_inst = BreezeClient(token)
                funds_data = await asyncio.to_thread(breeze_inst.get_funds)
                if funds_data.get("Success"):
                    available_cash = float(funds_data["Success"].get("unallocated_balance", 0.0) or 0.0)
        except Exception as cash_err:
            logger.debug(f"Could not retrieve cash balance for pre-market radar: {cash_err}")

        # Minimum deployment threshold guardrail
        if available_cash < 5000.0:
            logger.info(f"Premarket deployment suppressed: Available cash (₹{available_cash:,.0f}) < ₹5,000 threshold.")
            return

        async with aiosqlite.connect(settings.db_path) as db:
            # Partition: Only inspect unowned watchlist stocks
            async with db.execute("""
                SELECT s.stock_code, s.rsi14, s.volume_ratio_20d, s.pct_from_52w_high, s.composite_score,
                       h.current_price
                FROM signals s
                JOIN watchlist w ON s.stock_code = w.stock_code
                LEFT JOIN holdings_snapshot h ON s.stock_code = h.stock_code
                WHERE h.stock_code IS NULL
                  AND s.timestamp >= datetime('now', '-1 day')
                ORDER BY s.composite_score DESC
            """) as cur:
                candidates_rows = await cur.fetchall()

            if not candidates_rows:
                logger.info("No fresh unowned watchlist candidates found.")
                return

            qualified_candidates = []
            for r in candidates_rows:
                tkr = r[0]
                rsi = r[1] or 50.0
                vol_ratio = r[2] or 1.0
                pct_52w = r[3] or 0.0
                comp_score = r[4] or 0.0

                # Check Trend Template (7/7 Minervini) from OHLCV cache
                async with db.execute(
                    "SELECT stock_code, date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date DESC LIMIT 260",
                    (tkr,),
                ) as cur:
                    stock_rows = await cur.fetchall()

                if not stock_rows or len(stock_rows) < 50:
                    continue

                stock_df = pd.DataFrame(
                    list(reversed(stock_rows)),
                    columns=["stock_code", "date", "open", "high", "low", "close", "volume"],
                )
                for col in ["open", "high", "low", "close", "volume"]:
                    stock_df[col] = pd.to_numeric(stock_df[col], errors="coerce")

                tt_score = check_trend_template(stock_df)
                cmp = stock_df.iloc[-1]["close"]

                # Must qualify Minervini 7/7 and healthy volume/RS
                if tt_score >= 6 and vol_ratio >= 1.2:
                    entry_note = f"52W High Gap: {pct_52w:.1f}% | RSI: {rsi:.1f}"
                    qualified_candidates.append({
                        "ticker": tkr,
                        "price": cmp,
                        "score": tt_score,
                        "rs_score": round(comp_score, 0),
                        "volume_ratio": vol_ratio,
                        "entry_note": entry_note,
                    })

            if qualified_candidates:
                radar_msg = format_premarket_opportunities_message(
                    candidates=qualified_candidates[:5],
                    available_cash=available_cash,
                )
                await broadcast_message(app, radar_msg)
                logger.info(f"Broadcasted {len(qualified_candidates)} premarket opportunities.")
            else:
                logger.info("Premarket radar: No unowned candidates cleared the 7/7 Minervini setup today.")

    except Exception as e:
        logger.error(f"Error evaluating premarket opportunities: {e}")
