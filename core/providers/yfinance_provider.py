import logging

import yfinance as yf

from core.ticker_mapping import map_to_yfinance_ticker

from .base import MarketDataProvider

logger = logging.getLogger(__name__)

class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def get_historical_ohlcv(self, ticker, from_date, to_date):
        yf_ticker = map_to_yfinance_ticker(ticker)
        try:
            df = yf.Ticker(yf_ticker).history(start=from_date, end=to_date, interval="1d")
            if df is None or df.empty:
                return []
            df = df.reset_index()
            df = df.rename(columns={
                "Date": "date", "Open": "open", "High": "high",
                "Low": "low", "Close": "close", "Volume": "volume",
            })
            df["date"] = df["date"].dt.strftime("%Y-%m-%d")
            return df[["date", "open", "high", "low", "close", "volume"]].to_dict("records")
        except Exception as e:
            logger.error(f"yfinance fetch failed for {ticker} ({yf_ticker}): {e}")
            return []

    def get_quote(self, ticker):
        yf_ticker = map_to_yfinance_ticker(ticker)
        try:
            fi = yf.Ticker(yf_ticker).fast_info
            ltp = fi.get("last_price")
            prev_close = fi.get("previous_close")
            if ltp is None:
                return None
            change = (ltp - prev_close) if prev_close else None
            return {"ltp": float(ltp), "change": float(change) if change is not None else None}
        except Exception as e:
            logger.error(f"yfinance quote failed for {ticker} ({yf_ticker}): {e}")
            return None
