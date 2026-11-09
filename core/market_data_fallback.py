#!/usr/bin/env python3
"""core/market_data_fallback.py - Multi-Source Resilient Market Data Ingestion Pipeline.

Waterfall Fallback Architecture:
Tier 1: ICICI Breeze API (when active session exists - authentic broker tick)
Tier 2: yfinance (Batch vectorized mode, ~1.2s for 50 tickers - primary EOD & technical fallback)
Tier 3: nsepython (Live exchange HTTP scrape with cookie warming for intraday market hours)
Tier 4: jugaad-data (Official EOD NSE Bhavcopy master CSV download - 100% reliable offline batch)
"""

from datetime import date, datetime, timedelta
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
import pandas as pd

logger = logging.getLogger("market_data_fallback")

BHAVCOPY_DIR = Path(__file__).resolve().parent.parent / "data" / "bhavcopy"


class MarketDataFallback:
    def __init__(self, breeze_client=None):
        self.breeze = breeze_client
        BHAVCOPY_DIR.mkdir(parents=True, exist_ok=True)

    def set_breeze_client(self, client):
        self.breeze = client

    def get_price(self, ticker: str) -> Tuple[float, str]:
        """Fetch single ticker price using the fallback waterfall."""
        clean_ticker = ticker.replace(".NS", "").replace(".BO", "").strip().upper()

        # 1. Breeze API (Live Broker Feed)
        if self.breeze is not None:
            try:
                res = self.breeze.get_quotes(stock_code=clean_ticker, exchange_code="NSE", product_type="cash")
                if res and res.get("Success"):
                    ltp = float(res["Success"][0].get("ltp", 0.0))
                    if ltp > 0:
                        return ltp, "BREEZE_LIVE"
            except Exception as e:
                logger.debug(f"Breeze quote failed for {clean_ticker}: {e}")

        # 2. yfinance (Single Ticker Quick Query)
        try:
            import yfinance as yf
            ticker_obj = yf.Ticker(f"{clean_ticker}.NS")
            hist = ticker_obj.history(period="1d")
            if not hist.empty and "Close" in hist:
                val = float(hist["Close"].iloc[-1])
                if val > 0:
                    return val, "YFINANCE"
        except Exception as e:
            logger.debug(f"yfinance failed for {clean_ticker}: {e}")

        # 3. nsepython (Live Exchange Scrape)
        try:
            from nsepython import nse_quote_ltp
            ltp = nse_quote_ltp(clean_ticker)
            if ltp and float(ltp) > 0:
                return float(ltp), "NSEPYTHON"
        except Exception as e:
            logger.debug(f"nsepython failed for {clean_ticker}: {e}")

        # 4. jugaad-data (Latest Cached Bhavcopy)
        try:
            val = self._get_price_from_latest_bhavcopy(clean_ticker)
            if val and val > 0:
                return val, "BHAVCOPY"
        except Exception as e:
            logger.debug(f"bhavcopy lookup failed for {clean_ticker}: {e}")

        return 0.0, "FAILED"

    def get_prices_batch(self, tickers: List[str]) -> Dict[str, Tuple[float, str]]:
        """Batch-optimized price retrieval using vectorized yfinance with fallback."""
        results: Dict[str, Tuple[float, str]] = {}
        clean_map = {t: t.replace(".NS", "").replace(".BO", "").strip().upper() for t in tickers}
        pending = list(clean_map.values())

        # If Breeze is active, try Breeze first
        if self.breeze is not None:
            for t in list(pending):
                try:
                    res = self.breeze.get_quotes(stock_code=t, exchange_code="NSE", product_type="cash")
                    if res and res.get("Success"):
                        ltp = float(res["Success"][0].get("ltp", 0.0))
                        if ltp > 0:
                            results[t] = (ltp, "BREEZE_LIVE")
                            pending.remove(t)
                except Exception:
                    pass

        if not pending:
            return results

        # Vectorized yfinance for remaining
        try:
            import yfinance as yf
            yf_symbols = [f"{t}.NS" for t in pending]
            df = yf.download(
                tickers=" ".join(yf_symbols),
                period="5d",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            for t in list(pending):
                sym = f"{t}.NS"
                if sym in df and not df[sym].empty:
                    close_series = df[sym]["Close"].dropna()
                    if not close_series.empty:
                        results[t] = (float(close_series.iloc[-1]), "YFINANCE_BATCH")
                        pending.remove(t)
                elif len(pending) == 1 and not df.empty and "Close" in df:
                    # Single item download shape
                    close_series = df["Close"].dropna()
                    if not close_series.empty:
                        results[t] = (float(close_series.iloc[-1]), "YFINANCE_BATCH")
                        pending.remove(t)
        except Exception as e:
            logger.warning(f"Vectorized yfinance batch failed: {e}")

        # Fallback individually for any residual tickers
        for t in pending:
            val, src = self.get_price(t)
            results[t] = (val, src)

        return results

    def sync_latest_bhavcopy(self, days_back: int = 5) -> Optional[Path]:
        """Downloads the most recent available official NSE Bhavcopy master CSV."""
        from jugaad_data.nse import bhavcopy_save
        today = date.today()
        for i in range(days_back):
            d = today - timedelta(days=i)
            # Skip weekends
            if d.weekday() >= 5:
                continue
            try:
                out_path = bhavcopy_save(d, str(BHAVCOPY_DIR))
                logger.info(f"Downloaded official NSE Bhavcopy for {d}: {out_path}")
                return Path(out_path)
            except Exception:
                continue
        return None

    def _get_price_from_latest_bhavcopy(self, ticker: str) -> Optional[float]:
        """Extract price for a symbol from the most recent Bhavcopy CSV on disk."""
        files = sorted(BHAVCOPY_DIR.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            p = self.sync_latest_bhavcopy()
            if p:
                files = [p]
        if not files:
            return None

        for csv_path in files[:2]:
            try:
                df = pd.read_csv(csv_path)
                match = df[(df["TckrSymb"] == ticker) & (df["SctySrs"].isin(["EQ", "GB", "IV", "RR"]))]
                if not match.empty:
                    return float(match.iloc[0]["ClsPric"])
            except Exception:
                continue
        return None


if __name__ == "__main__":
    import time
    md = MarketDataFallback()
    test_symbols = ["BHEL", "FEDERALBNK", "GOLDBEES", "INFY", "TATASTEEL"]
    
    print("\n--- 1. Testing Single Price Waterfall ---")
    p, src = md.get_price("BHEL")
    print(f"BHEL: ₹{p:.2f} via {src}")

    print("\n--- 2. Testing Vectorized Batch Mode ---")
    t0 = time.perf_counter()
    batch_res = md.get_prices_batch(test_symbols)
    dt = time.perf_counter() - t0
    for sym, (val, src) in batch_res.items():
        print(f"  {sym:<10}: ₹{val:8.2f} [{src}]")
    print(f"Batch ({len(test_symbols)} tickers) completed in {dt:.2f}s")
