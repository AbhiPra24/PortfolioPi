import logging
from tenacity import retry, stop_after_attempt, wait_exponential

from core.breeze_client import BreezeClient
from .base import MarketDataProvider

logger = logging.getLogger(__name__)

class BreezeProvider(MarketDataProvider):
    name = "breeze"

    def __init__(self, breeze: BreezeClient):
        self.breeze = breeze

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    def _fetch(self, stock_code, from_date, to_date):
        return self.breeze.get_historical_data_v2(
            interval="1day", from_date=from_date, to_date=to_date,
            stock_code="NIFTY" if stock_code == "_NIFTY50" else stock_code,
            exchange_code="NSE", product_type="cash",
        )

    def get_historical_ohlcv(self, ticker, from_date, to_date):
        from_str = from_date.strftime("%Y-%m-%dT00:00:00.000Z")
        to_str = to_date.strftime("%Y-%m-%dT00:00:00.000Z")
        try:
            hist_data = self._fetch(ticker, from_str, to_str)
            if isinstance(hist_data, dict) and hist_data.get("Success"):
                return [
                    {
                        "date": row.get("datetime", "")[:10],
                        "open": float(row.get("open", 0)),
                        "high": float(row.get("high", 0)),
                        "low": float(row.get("low", 0)),
                        "close": float(row.get("close", 0)),
                        "volume": int(row.get("volume", 0)),
                    }
                    for row in hist_data.get("Success", [])
                ]
            return []
        except Exception as e:
            logger.error(f"Breeze fetch failed for {ticker}: {e}")
            return []

    def get_quote(self, ticker):
        try:
            res = self.breeze.get_quotes(stock_code=ticker, exchange_code="NSE", product_type="cash")
            if res.get("Success") and len(res["Success"]) > 0:
                data = res["Success"][0]
                return {"ltp": float(data.get("ltp", 0)), "change": data.get("change")}
            return None
        except Exception as e:
            logger.error(f"Breeze quote failed for {ticker}: {e}")
            return None
