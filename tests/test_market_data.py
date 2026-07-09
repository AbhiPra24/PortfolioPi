import sys
from unittest.mock import MagicMock
# Mock breeze_connect before any other imports to avoid permission issues with its log directory
sys.modules['breeze_connect'] = MagicMock()

import unittest
from unittest.mock import patch
import pandas as pd
from datetime import datetime

from core.ticker_mapping import map_to_yfinance_ticker
from core.providers.yfinance_provider import YFinanceProvider

class TestMarketData(unittest.TestCase):
    def test_map_to_yfinance_ticker(self):
        self.assertEqual(map_to_yfinance_ticker("_NIFTY50"), "^NSEI")
        self.assertEqual(map_to_yfinance_ticker("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(map_to_yfinance_ticker("TCS"), "TCS.NS")

    @patch("yfinance.Ticker")
    def test_yfinance_provider_get_historical_ohlcv(self, mock_ticker_class):
        # Setup mock DataFrame returned by yfinance history()
        mock_data = {
            "Open": [100.0, 105.0],
            "High": [106.0, 110.0],
            "Low": [99.0, 104.0],
            "Close": [105.0, 108.0],
            "Volume": [1000, 1500]
        }
        dates = pd.to_datetime(["2026-07-01", "2026-07-02"])
        dates.name = "Date"
        mock_df = pd.DataFrame(mock_data, index=dates)
        
        # Configure the mock instance
        mock_ticker_instance = MagicMock()
        mock_ticker_instance.history.return_value = mock_df
        mock_ticker_class.return_value = mock_ticker_instance
        
        provider = YFinanceProvider()
        from_date = datetime(2026, 7, 1)
        to_date = datetime(2026, 7, 2)
        
        result = provider.get_historical_ohlcv("RELIANCE", from_date, to_date)
        
        # Verify the history method call on mock
        mock_ticker_instance.history.assert_called_once_with(
            start=from_date, end=to_date, interval="1d"
        )
        
        # Assert format mapping
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["date"], "2026-07-01")
        self.assertEqual(result[0]["open"], 100.0)
        self.assertEqual(result[0]["close"], 105.0)
        self.assertEqual(result[0]["volume"], 1000)
        
        self.assertEqual(result[1]["date"], "2026-07-02")
        self.assertEqual(result[1]["open"], 105.0)
        self.assertEqual(result[1]["close"], 108.0)
        self.assertEqual(result[1]["volume"], 1500)
