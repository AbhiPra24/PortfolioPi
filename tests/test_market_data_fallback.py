"""Unit tests for operational guardrails in multi-source market data fallback pipeline."""

from datetime import date, datetime
import unittest.mock as mock
import pandas as pd
import pytest

from core.market_data_fallback import (
    KNOWN_NSE_HOLIDAYS,
    TICKER_MAP,
    MarketDataFallback,
    fetch_yfinance_history,
    fetch_yfinance_quotes,
    get_last_trading_date,
    normalize_to_breeze,
    normalize_to_nse,
    normalize_to_yfinance,
    parse_bhavcopy_row,
)


# ===========================================================================
# 1. UDiFF & Legacy NSE Bhavcopy format compatibility tests
# ===========================================================================
def test_parse_bhavcopy_udiff_format():
    """Verify parsing of modern NSE UDiFF bhavcopy structure."""
    df_udiff = pd.DataFrame([
        {
            "TradDt": "2026-11-06",
            "TckrSymb": "FEDERALBNK",
            "SctySrs": "EQ",
            "ClsPric": 340.10,
            "TtlTradgVol": 1250000,
        },
        {
            "TradDt": "2026-11-06",
            "TckrSymb": "PGINVIT",
            "SctySrs": "IV",
            "ClsPric": 105.38,
            "TtlTradgVol": 45000,
        },
    ])

    res = parse_bhavcopy_row(df_udiff, "FEDERALBNK")
    assert res is not None
    assert res["symbol"] == "FEDERALBNK"
    assert res["close"] == 340.10
    assert res["volume"] == 1250000

    # Test via Breeze stock_code lookup
    res_breeze = parse_bhavcopy_row(df_udiff, "FEDBAN")
    assert res_breeze is not None
    assert res_breeze["close"] == 340.10


def test_parse_bhavcopy_legacy_format():
    """Verify parsing of legacy NSE Bhavcopy format (SYMBOL, CLOSE, TOTTRDQTY)."""
    df_legacy = pd.DataFrame([
        {
            "SYMBOL": "MANAPPURAM",
            "SERIES": "EQ",
            "OPEN": 320.0,
            "HIGH": 328.0,
            "LOW": 319.0,
            "CLOSE": 325.40,
            "TOTTRDQTY": 850000,
            "TIMESTAMP": "05-JAN-2024",
        },
        {
            "SYMBOL": "SILVERBEES",
            "SERIES": "EQ",
            "OPEN": 75.0,
            "HIGH": 76.5,
            "LOW": 74.8,
            "CLOSE": 76.10,
            "TOTTRDQTY": 320000,
            "TIMESTAMP": "05-JAN-2024",
        },
    ])

    res = parse_bhavcopy_row(df_legacy, "MANAPPURAM")
    assert res is not None
    assert res["symbol"] == "MANAPPURAM"
    assert res["close"] == 325.40
    assert res["volume"] == 850000

    # Breeze code mapping into legacy format
    res_silver = parse_bhavcopy_row(df_legacy, "NIPSIL")
    assert res_silver is not None
    assert res_silver["symbol"] == "SILVERBEES"
    assert res_silver["close"] == 76.10


def test_parse_bhavcopy_case_insensitivity_and_missing():
    """Verify case-insensitivity, lowercase headers, and graceful missing ticker handling without KeyError."""
    df_mixed = pd.DataFrame([
        {
            "tckrsymb": "tatasteel",
            "sctysrs": "eq",
            "clspric": 150.25,
            "ttltradgvol": 99999,
        }
    ])

    res = parse_bhavcopy_row(df_mixed, "TATASTEEL")
    assert res is not None
    assert res["symbol"] == "TATASTEEL"
    assert res["close"] == 150.25

    # Graceful handling for unknown ticker without KeyError
    missing = parse_bhavcopy_row(df_mixed, "NONEXISTENT")
    assert missing is None

    # Graceful handling for empty dataframe
    assert parse_bhavcopy_row(pd.DataFrame(), "TATASTEEL") is None


# ===========================================================================
# 2. Holiday Calendar & Weekend Offset tests
# ===========================================================================
def test_get_last_trading_date_weekday():
    """Verify last trading date on normal midweek trading day."""
    # Wednesday 2026-11-04 is a regular trading day
    ref = date(2026, 11, 4)
    assert get_last_trading_date(ref) == ref


def test_get_last_trading_date_weekend():
    """Verify stepping backward over Saturday and Sunday to Friday."""
    # Sunday 2026-11-08 -> Friday 2026-11-06
    sunday = date(2026, 11, 8)
    saturday = date(2026, 11, 7)
    friday = date(2026, 11, 6)

    assert get_last_trading_date(sunday) == friday
    assert get_last_trading_date(saturday) == friday


def test_get_last_trading_date_holiday():
    """Verify stepping backward over known NSE holidays (e.g. Gandhi Jayanti Oct 2)."""
    # Oct 2, 2026 was Friday (Gandhi Jayanti) -> Should step back to Thursday Oct 1, 2026
    gandhi_jayanti = date(2026, 10, 2)
    assert gandhi_jayanti in KNOWN_NSE_HOLIDAYS
    assert get_last_trading_date(gandhi_jayanti) == date(2026, 10, 1)

    # Monday holiday: Republic Day 2026-01-26 (Monday)
    # Should step back past Sunday (25th) and Saturday (24th) to Friday Jan 23, 2026
    republic_day = date(2026, 1, 26)
    assert republic_day in KNOWN_NSE_HOLIDAYS
    assert get_last_trading_date(republic_day) == date(2026, 1, 23)


# ===========================================================================
# 3. Bidirectional Symbol Normalization Matrix tests
# ===========================================================================
def test_ticker_map_completeness():
    """Verify explicit required tickers are present in TICKER_MAP."""
    required = ["FEDBAN", "MANAFI", "POWINF", "INDGRI", "NIPSIL", "GOLDBEES"]
    for sym in required:
        assert sym in TICKER_MAP
        nse, yf = TICKER_MAP[sym]
        assert nse and yf


def test_normalize_to_nse():
    """Verify normalization to official NSE symbol."""
    assert normalize_to_nse("FEDBAN") == "FEDERALBNK"
    assert normalize_to_nse("MANAFI") == "MANAPPURAM"
    assert normalize_to_nse("POWINF") == "PGINVIT"
    assert normalize_to_nse("INDGRI") == "INDIGRID"
    assert normalize_to_nse("NIPSIL") == "SILVERBEES"
    assert normalize_to_nse("GOLDBEES") == "GOLDBEES"

    # From yfinance tickers
    assert normalize_to_nse("FEDERALBNK.NS") == "FEDERALBNK"
    assert normalize_to_nse("SILVERBEES.NS") == "SILVERBEES"
    assert normalize_to_nse("PGINVIT.BO") == "PGINVIT"

    # Standard symbols without special override
    assert normalize_to_nse("INFY") == "INFY"
    assert normalize_to_nse("INFY.NS") == "INFY"


def test_normalize_to_yfinance():
    """Verify normalization to Yahoo Finance ticker format."""
    assert normalize_to_yfinance("FEDBAN") == "FEDERALBNK.NS"
    assert normalize_to_yfinance("MANAFI") == "MANAPPURAM.NS"
    assert normalize_to_yfinance("POWINF") == "PGINVIT.BO"
    assert normalize_to_yfinance("INDGRI") == "INDIGRID.BO"
    assert normalize_to_yfinance("NIPSIL") == "SILVERBEES.NS"
    assert normalize_to_yfinance("GOLDBEES") == "GOLDBEES.NS"

    # From NSE symbols
    assert normalize_to_yfinance("FEDERALBNK") == "FEDERALBNK.NS"
    assert normalize_to_yfinance("SILVERBEES") == "SILVERBEES.NS"
    assert normalize_to_yfinance("PGINVIT") == "PGINVIT.BO"

    # Standard symbol
    assert normalize_to_yfinance("INFY") == "INFY.NS"
    assert normalize_to_yfinance("INFY.NS") == "INFY.NS"


def test_normalize_to_breeze():
    """Verify normalization to Breeze broker stock_code."""
    assert normalize_to_breeze("FEDERALBNK") == "FEDBAN"
    assert normalize_to_breeze("FEDERALBNK.NS") == "FEDBAN"
    assert normalize_to_breeze("MANAPPURAM") == "MANAFI"
    assert normalize_to_breeze("PGINVIT") == "POWINF"
    assert normalize_to_breeze("PGINVIT.BO") == "POWINF"
    assert normalize_to_breeze("INDIGRID") == "INDGRI"
    assert normalize_to_breeze("SILVERBEES") == "NIPSIL"
    assert normalize_to_breeze("SILVERBEES.NS") == "NIPSIL"
    assert normalize_to_breeze("FEDBAN") == "FEDBAN"


# ===========================================================================
# 4. Adjusted vs Unadjusted Splits / Corporate Actions tests
# ===========================================================================
def test_fetch_yfinance_history_unadjusted_close():
    """Verify that fetch_yfinance_history requests auto_adjust=False and returns 'Close' column."""
    with mock.patch("yfinance.Ticker") as mock_ticker:
        mock_instance = mock.MagicMock()
        mock_ticker.return_value = mock_instance

        # Return mock DataFrame with distinct Close vs Adj Close
        mock_df = pd.DataFrame(
            {
                "Open": [100.0],
                "High": [105.0],
                "Low": [98.0],
                "Close": [102.0],       # Unadjusted execution price
                "Adj Close": [51.0],     # Dividend/split adjusted price
                "Volume": [1000],
            },
            index=pd.to_datetime(["2026-11-06"]),
        )
        mock_instance.history.return_value = mock_df

        df = fetch_yfinance_history("FEDBAN", period="5d")

        # Verify auto_adjust=False was passed to yfinance
        mock_instance.history.assert_called_once()
        _, kwargs = mock_instance.history.call_args
        assert kwargs.get("auto_adjust") is False

        # Verify unadjusted Close price is preserved
        assert "Close" in df.columns
        assert float(df["Close"].iloc[-1]) == 102.0


def test_fetch_yfinance_quotes_unadjusted():
    """Verify fetch_yfinance_quotes uses auto_adjust=False in vectorized download."""
    with mock.patch("yfinance.download") as mock_download:
        cols = pd.MultiIndex.from_tuples([
            ("FEDERALBNK.NS", "Close"),
            ("FEDERALBNK.NS", "Adj Close"),
        ])
        mock_df = pd.DataFrame([[340.10, 170.05]], columns=cols)
        mock_download.return_value = mock_df

        quotes = fetch_yfinance_quotes(["FEDBAN"])

        mock_download.assert_called_once()
        _, kwargs = mock_download.call_args
        assert kwargs.get("auto_adjust") is False
        assert quotes.get("FEDBAN") == 340.10


# ===========================================================================
# 5. Integration Waterfall tests
# ===========================================================================
def test_market_data_fallback_single():
    """Integration test verifying price retrieval on core tickers."""
    md = MarketDataFallback(breeze_client=None)
    price, source = md.get_price("FEDBAN")
    assert price > 0, f"Expected positive price for FEDBAN, got {price}"
    assert source in ("YFINANCE", "NSEPYTHON", "BHAVCOPY")


def test_market_data_fallback_vectorized_batch():
    """Integration test verifying batch price retrieval with matrix normalization."""
    md = MarketDataFallback(breeze_client=None)
    symbols = ["FEDBAN", "MANAFI", "NIPSIL", "GOLDBEES"]
    batch_res = md.get_prices_batch(symbols)
    assert len(batch_res) == len(symbols)
    for s in symbols:
        price, src = batch_res[s]
        assert price > 0, f"Expected positive price for {s}"
        assert "YFINANCE" in src or "NSEPYTHON" in src or "BHAVCOPY" in src
