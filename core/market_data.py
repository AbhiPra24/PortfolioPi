"""Alternative market data source wrapper using yfinance."""

import logging
import asyncio
import yfinance as yf
from datetime import datetime
import pandas as pd

logger = logging.getLogger(__name__)

async def get_market_data(nse_symbol: str):
    """
    Fetches the current price and latest OHLCV from yfinance for a given NSE symbol.
    Returns a dict with 'current_price' and 'ohlcv' list of tuples on success, None on failure.
    Note: yfinance is an unofficial API and can break or rate limit. 
    If this fails, it logs and returns None, which the caller should skip.
    """
    if nse_symbol == "NIFTY 50":
        yf_ticker = "^NSEI"
    else:
        yf_ticker = f"{nse_symbol}.NS"
        
    try:
        def _fetch():
            ticker = yf.Ticker(yf_ticker)
            # Use period="5d" to ensure we get some data even across weekends
            hist = ticker.history(period="5d")
            if hist.empty:
                return None
                
            # Current price is the close of the most recent candle
            latest_row = hist.iloc[-1]
            current_price = float(latest_row['Close'])
            
            # Format history for ohlcv_cache
            ohlcv_records = []
            for date, row in hist.iterrows():
                # yfinance returns timezone-aware timestamps, convert to naive string format matching Breeze
                date_str = date.strftime("%Y-%m-%d %H:%M:%S")
                ohlcv_records.append({
                    "date": date_str,
                    "open": float(row['Open']),
                    "high": float(row['High']),
                    "low": float(row['Low']),
                    "close": float(row['Close']),
                    "volume": int(row['Volume'])
                })
                
            return {
                "current_price": current_price,
                "ohlcv": ohlcv_records
            }
            
        return await asyncio.to_thread(_fetch)
        
    except Exception as e:
        logger.warning(f"Failed to fetch market data from yfinance for {yf_ticker}: {e}")
        return None
