"""Unit tests for multi-source market data fallback pipeline."""

import pytest
from core.market_data_fallback import MarketDataFallback


def test_market_data_fallback_single():
    md = MarketDataFallback(breeze_client=None)
    # Test lookup on well-known liquid Indian symbols
    price, source = md.get_price("BHEL")
    assert price > 0, f"Expected positive price for BHEL, got {price}"
    assert source in ("YFINANCE", "NSEPYTHON", "BHAVCOPY")


def test_market_data_fallback_vectorized_batch():
    md = MarketDataFallback(breeze_client=None)
    symbols = ["BHEL", "INFY", "TATASTEEL"]
    batch_res = md.get_prices_batch(symbols)
    assert len(batch_res) == len(symbols)
    for s in symbols:
        price, src = batch_res[s]
        assert price > 0, f"Expected positive price for {s}"
        assert "YFINANCE" in src or "NSEPYTHON" in src or "BHAVCOPY" in src
