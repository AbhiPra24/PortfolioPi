from abc import ABC, abstractmethod
from datetime import datetime


class MarketDataProvider(ABC):
    name: str  # "yfinance" | "breeze"

    @abstractmethod
    def get_historical_ohlcv(self, ticker: str, from_date: datetime, to_date: datetime) -> list[dict]:
        """Ascending-by-date list of {date, open, high, low, close, volume} (lowercase keys,
        date as 'YYYY-MM-DD' string) — normalized to ohlcv_cache's schema. Return [] on
        failure/empty result; do not raise for a single bad ticker."""

    @abstractmethod
    def get_quote(self, ticker: str) -> dict | None:
        """Returns {'ltp': float, 'change': float | None}, or None if unavailable."""
