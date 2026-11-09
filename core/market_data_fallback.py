#!/usr/bin/env python3
"""core/market_data_fallback.py - Multi-Source Resilient Market Data Ingestion Pipeline.

Waterfall Fallback Architecture:
Tier 1: ICICI Breeze API (when active session exists - authentic broker tick)
Tier 2: yfinance (Batch vectorized mode, ~1.2s for 50 tickers - primary EOD & technical fallback)
Tier 3: nsepython (Live exchange HTTP scrape with cookie warming for intraday market hours)
Tier 4: jugaad-data (Official EOD NSE Bhavcopy master CSV download - 100% reliable offline batch)

Operational Guardrails:
1. UDiFF & Legacy NSE Bhavcopy format compatibility:
   - Supports legacy format (SYMBOL, CLOSE, TOTTRDQTY, SERIES) and UDiFF format
     (TckrSymb, ClsPric, TtlTradgVol, SctySrs).
   - Case-insensitive column matching without KeyError.
2. Holiday Calendar & Weekend Offset:
   - get_last_trading_date(ref_date) steps back over weekends and known NSE holidays.
3. Bidirectional Symbol Normalization Matrix:
   - TICKER_MAP and helpers: normalize_to_nse, normalize_to_yfinance, normalize_to_breeze.
4. Adjusted vs Unadjusted Splits / Corporate Actions:
   - Uses unadjusted closing prices ('Close' rather than 'Adj Close', auto_adjust=False)
     so prices align with broker execution records and stop triggers.
"""

from datetime import date, datetime, timedelta
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd

logger = logging.getLogger("market_data_fallback")

BHAVCOPY_DIR = Path(__file__).resolve().parent.parent / "data" / "bhavcopy"

# ---------------------------------------------------------------------------
# Bidirectional Symbol Normalization Matrix
# ---------------------------------------------------------------------------
# Map each Breeze stock_code to (NSE_SYMBOL, YFINANCE_SYMBOL)
TICKER_MAP: Dict[str, Tuple[str, str]] = {
    # Core mappings requested explicitly
    "FEDBAN": ("FEDERALBNK", "FEDERALBNK.NS"),
    "MANAFI": ("MANAPPURAM", "MANAPPURAM.NS"),
    "POWINF": ("PGINVIT", "PGINVIT.BO"),       # InvITs trade reliably on BSE via yfinance
    "INDGRI": ("INDIGRID", "INDIGRID.BO"),   # InvITs trade reliably on BSE via yfinance
    "NIPSIL": ("SILVERBEES", "SILVERBEES.NS"),
    "GOLDBEES": ("GOLDBEES", "GOLDBEES.NS"),

    # Other common portfolio tickers
    "CANBAN": ("CANBK", "CANBK.NS"),
    "BANBAR": ("BANKBARODA", "BANKBARODA.NS"),
    "COALIN": ("COALINDIA", "COALINDIA.NS"),
    "DABIND": ("DABUR", "DABUR.NS"),
    "SUZENE": ("SUZLON", "SUZLON.NS"),
    "BHAELE": ("BEL", "BEL.NS"),
    "INDOIL": ("IOC", "IOC.NS"),
    "POWGRI": ("POWERGRID", "POWERGRID.NS"),
    "HINZIN": ("HINDZINC", "HINDZINC.NS"),
    "HDFGOL": ("HDFCGOLD", "HDFCGOLD.NS"),
    "CPSETF": ("CPSEETF", "CPSEETF.NS"),
    "NIFBEE": ("NIFTYBEES", "NIFTYBEES.NS"),
    "RAIVIK": ("RVNL", "RVNL.NS"),
    "IRCINT": ("IRCON", "IRCON.NS"),
    "NATMIN": ("NMDC", "NMDC.NS"),
    "GUJPPL": ("GPPL", "GPPL.NS"),
    "MINBUS": ("MIDHANI", "MIDHANI.NS"),
    "EMBOFF": ("EMBASSY", "EMBASSY.BO"),
    "NEXSEL": ("NXST", "NXST.BO"),
    "GOLDEX": ("GOLDBEES", "GOLDBEES.NS"),
    "PSUBAN": ("PSUBNKBEES", "PSUBNKBEES.NS"),
    "TRAREA": ("TRIDENT", "TRIDENT.NS"),
    "ALLTER": ("ALLCARGO", "ALLCARGO.NS"),
    "INDR": ("IGL", "IGL.NS"),
    "HIMCHE": ("HSCL", "HSCL.NS"),
    "JAGPRA": ("JAGRAN", "JAGRAN.NS"),
    "_NIFTY50": ("NIFTY 50", "^NSEI"),
}

# Reverse lookup lookup tables for fast resolution
_NSE_TO_BREEZE: Dict[str, str] = {v[0].upper(): k for k, v in TICKER_MAP.items()}
_YF_TO_BREEZE: Dict[str, str] = {v[1].upper(): k for k, v in TICKER_MAP.items()}
_NSE_TO_YF: Dict[str, str] = {v[0].upper(): v[1] for k, v in TICKER_MAP.items()}


def normalize_to_nse(symbol: str) -> str:
    """Normalize any symbol format (Breeze code, yfinance ticker, or NSE symbol) to official NSE symbol.

    Examples:
        'FEDBAN' -> 'FEDERALBNK'
        'SILVERBEES.NS' -> 'SILVERBEES'
        'PGINVIT.BO' -> 'PGINVIT'
        'INFY' -> 'INFY'
    """
    if not symbol:
        return ""
    clean = symbol.strip().upper()
    # Check if known yfinance ticker
    if clean in _YF_TO_BREEZE:
        breeze_code = _YF_TO_BREEZE[clean]
        return TICKER_MAP[breeze_code][0]
    # Check if known Breeze stock_code
    if clean in TICKER_MAP:
        return TICKER_MAP[clean][0]
    # Strip exchange suffixes if any
    base = clean.replace(".NS", "").replace(".BO", "").strip()
    if base in TICKER_MAP:
        return TICKER_MAP[base][0]
    return base


def normalize_to_yfinance(symbol: str) -> str:
    """Normalize any symbol format (Breeze code, NSE symbol, or yfinance ticker) to yfinance symbol.

    Examples:
        'FEDBAN' -> 'FEDERALBNK.NS'
        'PGINVIT' -> 'PGINVIT.BO'
        'FEDERALBNK' -> 'FEDERALBNK.NS'
        'INFY' -> 'INFY.NS'
    """
    if not symbol:
        return ""
    clean = symbol.strip().upper()
    # Already has yfinance exchange suffix
    if clean.endswith(".NS") or clean.endswith(".BO") or clean.startswith("^"):
        return clean
    # Check if Breeze code
    if clean in TICKER_MAP:
        return TICKER_MAP[clean][1]
    # Check if NSE symbol in reverse map
    if clean in _NSE_TO_YF:
        return _NSE_TO_YF[clean]
    # Default Indian equity convention on Yahoo Finance
    return f"{clean}.NS"


def normalize_to_breeze(symbol: str) -> str:
    """Normalize any symbol format (NSE symbol, yfinance ticker, or Breeze code) to Breeze stock_code.

    Examples:
        'FEDERALBNK' -> 'FEDBAN'
        'SILVERBEES.NS' -> 'NIPSIL'
        'FEDBAN' -> 'FEDBAN'
    """
    if not symbol:
        return ""
    clean = symbol.strip().upper()
    if clean in TICKER_MAP:
        return clean
    if clean in _YF_TO_BREEZE:
        return _YF_TO_BREEZE[clean]
    if clean in _NSE_TO_BREEZE:
        return _NSE_TO_BREEZE[clean]
    # Strip exchange suffix and test again
    base = clean.replace(".NS", "").replace(".BO", "").strip()
    if base in TICKER_MAP:
        return base
    if base in _NSE_TO_BREEZE:
        return _NSE_TO_BREEZE[base]
    return base


# ---------------------------------------------------------------------------
# Holiday Calendar & Weekend Offset
# ---------------------------------------------------------------------------
# Official NSE Trading Holidays (Equity Segment)
KNOWN_NSE_HOLIDAYS = {
    # 2024
    date(2024, 1, 22),   # Special Holiday
    date(2024, 1, 26),   # Republic Day
    date(2024, 3, 8),    # Mahashivratri
    date(2024, 3, 25),   # Holi
    date(2024, 3, 29),   # Good Friday
    date(2024, 4, 11),   # Id-Ul-Fitr
    date(2024, 4, 17),   # Shri Ram Navmi
    date(2024, 5, 1),    # Maharashtra Day
    date(2024, 5, 20),   # General Parliamentary Elections
    date(2024, 6, 17),   # Bakri Id
    date(2024, 7, 17),   # Moharram
    date(2024, 8, 15),   # Independence Day
    date(2024, 10, 2),   # Mahatma Gandhi Jayanti
    date(2024, 11, 1),   # Diwali Laxmi Pujan (Day holiday, evening Muhurat)
    date(2024, 11, 15),  # Gurunanak Jayanti
    date(2024, 11, 20),  # Maharashtra Assembly Elections
    date(2024, 12, 25),  # Christmas

    # 2025
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr
    date(2025, 4, 10),   # Shri Mahavir Jayanti
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 21),  # Diwali Laxmi Pujan
    date(2025, 10, 22),  # Diwali-Balipratipada
    date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev
    date(2025, 12, 25),  # Christmas

    # 2026
    date(2026, 1, 15),   # Municipal Corporation Election
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Shri Ram Navami
    date(2026, 3, 31),   # Shri Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali-Balipratipada
    date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    date(2026, 12, 25),  # Christmas
}


def get_last_trading_date(ref_date: Optional[Union[date, datetime]] = None) -> date:
    """Return the most recent completed or active trading date prior to or on ref_date.

    Steps backward over weekends (Saturday=5, Sunday=6) and known NSE holidays.
    If ref_date is None, defaults to date.today().
    """
    if ref_date is None:
        cur = date.today()
    elif isinstance(ref_date, datetime):
        cur = ref_date.date()
    else:
        cur = ref_date

    while True:
        # Saturday=5, Sunday=6
        if cur.weekday() >= 5 or cur in KNOWN_NSE_HOLIDAYS:
            cur -= timedelta(days=1)
        else:
            return cur


# ---------------------------------------------------------------------------
# Bhavcopy Format Parser (UDiFF + Legacy)
# ---------------------------------------------------------------------------
def parse_bhavcopy_row(df: pd.DataFrame, ticker: str) -> Optional[Dict[str, Any]]:
    """Extract symbol data from Bhavcopy DataFrame supporting both UDiFF and Legacy NSE formats.

    Case-insensitive and schema-resilient without raising KeyError.
    """
    if df is None or df.empty:
        return None

    # Normalise column lookup map: lowercase stripped -> actual column name
    col_lookup = {str(c).strip().lower(): c for c in df.columns}

    # Find symbol column
    sym_col = None
    for cand in ["tckrsymb", "symbol", "stock", "security"]:
        if cand in col_lookup:
            sym_col = col_lookup[cand]
            break

    if not sym_col:
        return None

    # Series / SctySrs column (optional filter for equities/InvITs/ETFs)
    series_col = None
    for cand in ["sctysrs", "series"]:
        if cand in col_lookup:
            series_col = col_lookup[cand]
            break

    # Resolve candidate search tickers (NSE symbol and original ticker)
    nse_sym = normalize_to_nse(ticker)
    search_syms = {ticker.upper(), nse_sym.upper(), ticker.replace(".NS", "").replace(".BO", "").upper()}

    # Case-insensitive symbol match
    sym_series = df[sym_col].astype(str).str.strip().str.upper()
    mask = sym_series.isin(search_syms)

    if series_col:
        valid_series = {"EQ", "GB", "IV", "RR", "BE", "BZ", "SM"}
        series_vals = df[series_col].astype(str).str.strip().str.upper()
        # Filter series only if match exists in equity/InvIT series; otherwise keep any match
        filtered_mask = mask & series_vals.isin(valid_series)
        matches = df[filtered_mask] if filtered_mask.any() else df[mask]
    else:
        matches = df[mask]

    if matches.empty:
        return None

    row = matches.iloc[0]

    # Close price resolution
    cls_col = None
    for cand in ["clspric", "close", "lastprice", "last", "settleprice"]:
        if cand in col_lookup:
            cls_col = col_lookup[cand]
            break

    # Total trading volume resolution
    vol_col = None
    for cand in ["ttltradgvol", "tottrdqty", "volume", "totalvolume"]:
        if cand in col_lookup:
            vol_col = col_lookup[cand]
            break

    close_val = float(row[cls_col]) if cls_col and pd.notna(row[cls_col]) else 0.0
    volume_val = float(row[vol_col]) if vol_col and pd.notna(row[vol_col]) else 0.0

    return {
        "symbol": str(row[sym_col]).strip().upper(),
        "close": close_val,
        "volume": volume_val,
    }


# ---------------------------------------------------------------------------
# yfinance Unadjusted Helper Functions
# ---------------------------------------------------------------------------
def fetch_yfinance_history(
    symbol: str,
    period: str = "1mo",
    interval: str = "1d",
    start: Optional[Union[str, date, datetime]] = None,
    end: Optional[Union[str, date, datetime]] = None,
) -> pd.DataFrame:
    """Fetch unadjusted OHLCV price history from yfinance.

    Enforces auto_adjust=False and prioritizes unadjusted 'Close' over 'Adj Close'
    so prices remain strictly aligned with broker execution records and stop triggers.
    """
    import yfinance as yf

    yf_ticker = normalize_to_yfinance(symbol)
    ticker_obj = yf.Ticker(yf_ticker)

    kwargs: Dict[str, Any] = {
        "interval": interval,
        "auto_adjust": False,
    }
    if start is not None:
        kwargs["start"] = start
        if end is not None:
            kwargs["end"] = end
    else:
        kwargs["period"] = period

    df = ticker_obj.history(**kwargs)
    if df is None or df.empty:
        return pd.DataFrame()

    # Ensure unadjusted Close exists
    if "Close" not in df.columns and "Adj Close" in df.columns:
        df["Close"] = df["Adj Close"]

    return df


def fetch_yfinance_quotes(symbols: List[str]) -> Dict[str, float]:
    """Fetch latest unadjusted closing prices for a list of symbols via yfinance.

    Enforces auto_adjust=False across single and vectorized downloads.
    """
    if not symbols:
        return {}

    import yfinance as yf

    results: Dict[str, float] = {}
    yf_symbol_map = {s: normalize_to_yfinance(s) for s in symbols}
    unique_yf_syms = list(set(yf_symbol_map.values()))

    try:
        df = yf.download(
            tickers=" ".join(unique_yf_syms),
            period="5d",
            interval="1d",
            auto_adjust=False,
            group_by="ticker",
            threads=True,
            progress=False,
        )
        if df is not None and not df.empty:
            for s, yf_sym in yf_symbol_map.items():
                close_series = None
                if yf_sym in df and "Close" in df[yf_sym]:
                    close_series = df[yf_sym]["Close"].dropna()
                elif (yf_sym, "Close") in df:
                    close_series = df[(yf_sym, "Close")].dropna()
                elif len(unique_yf_syms) == 1 and "Close" in df:
                    close_series = df["Close"].dropna()

                if close_series is not None and not close_series.empty:
                    val = float(close_series.iloc[-1])
                    if val > 0:
                        results[s] = val
    except Exception as e:
        logger.debug(f"fetch_yfinance_quotes batch failed: {e}")

    # Fallback individually for any remaining tickers
    for s, yf_sym in yf_symbol_map.items():
        if s not in results:
            try:
                hist = fetch_yfinance_history(yf_sym, period="5d")
                if not hist.empty and "Close" in hist:
                    close_series = hist["Close"].dropna()
                    if not close_series.empty:
                        results[s] = float(close_series.iloc[-1])
            except Exception:
                pass

    return results


# ---------------------------------------------------------------------------
# Main MarketDataFallback Class
# ---------------------------------------------------------------------------
class MarketDataFallback:
    def __init__(self, breeze_client=None):
        self.breeze = breeze_client
        BHAVCOPY_DIR.mkdir(parents=True, exist_ok=True)

    def set_breeze_client(self, client):
        self.breeze = client

    def get_price(self, ticker: str) -> Tuple[float, str]:
        """Fetch single ticker price using the fallback waterfall."""
        breeze_code = normalize_to_breeze(ticker)
        nse_symbol = normalize_to_nse(ticker)
        yf_symbol = normalize_to_yfinance(ticker)

        # 1. Breeze API (Live Broker Feed)
        if self.breeze is not None:
            try:
                res = self.breeze.get_quotes(stock_code=breeze_code, exchange_code="NSE", product_type="cash")
                if res and res.get("Success"):
                    ltp = float(res["Success"][0].get("ltp", 0.0))
                    if ltp > 0:
                        return ltp, "BREEZE_LIVE"
            except Exception as e:
                logger.debug(f"Breeze quote failed for {breeze_code}: {e}")

        # 2. yfinance (Single Ticker Quick Query with unadjusted prices)
        try:
            hist = fetch_yfinance_history(yf_symbol, period="5d")
            if not hist.empty and "Close" in hist:
                close_series = hist["Close"].dropna()
                if not close_series.empty:
                    val = float(close_series.iloc[-1])
                    if val > 0:
                        return val, "YFINANCE"
        except Exception as e:
            logger.debug(f"yfinance failed for {yf_symbol}: {e}")

        # 3. nsepython (Live Exchange Scrape)
        try:
            from nsepython import nse_quote_ltp
            ltp = nse_quote_ltp(nse_symbol)
            if ltp and float(ltp) > 0:
                return float(ltp), "NSEPYTHON"
        except Exception as e:
            logger.debug(f"nsepython failed for {nse_symbol}: {e}")

        # 4. jugaad-data (Latest Cached Bhavcopy)
        try:
            val = self._get_price_from_latest_bhavcopy(nse_symbol)
            if val and val > 0:
                return val, "BHAVCOPY"
        except Exception as e:
            logger.debug(f"bhavcopy lookup failed for {nse_symbol}: {e}")

        return 0.0, "FAILED"

    def get_prices_batch(self, tickers: List[str]) -> Dict[str, Tuple[float, str]]:
        """Batch-optimized price retrieval using vectorized unadjusted yfinance with fallback."""
        results: Dict[str, Tuple[float, str]] = {}
        pending = list(tickers)

        # 1. If Breeze is active, try Breeze first
        if self.breeze is not None:
            for t in list(pending):
                breeze_code = normalize_to_breeze(t)
                try:
                    res = self.breeze.get_quotes(stock_code=breeze_code, exchange_code="NSE", product_type="cash")
                    if res and res.get("Success"):
                        ltp = float(res["Success"][0].get("ltp", 0.0))
                        if ltp > 0:
                            results[t] = (ltp, "BREEZE_LIVE")
                            pending.remove(t)
                except Exception:
                    pass

        if not pending:
            return results

        # 2. Vectorized unadjusted yfinance for remaining
        try:
            quotes = fetch_yfinance_quotes(pending)
            for t, val in quotes.items():
                if val > 0:
                    results[t] = (val, "YFINANCE_BATCH")
                    if t in pending:
                        pending.remove(t)
        except Exception as e:
            logger.warning(f"Vectorized yfinance batch failed: {e}")

        # 3. Fallback individually for any residual tickers
        for t in pending:
            val, src = self.get_price(t)
            results[t] = (val, src)

        return results

    def sync_latest_bhavcopy(self, days_back: int = 7) -> Optional[Path]:
        """Downloads the most recent available official NSE Bhavcopy master CSV.

        Uses get_last_trading_date to skip weekends and official holidays.
        """
        from jugaad_data.nse import bhavcopy_save
        cur = get_last_trading_date()
        for _ in range(days_back):
            cur = get_last_trading_date(cur)
            try:
                out_path = bhavcopy_save(cur, str(BHAVCOPY_DIR))
                logger.info(f"Downloaded official NSE Bhavcopy for {cur}: {out_path}")
                return Path(out_path)
            except Exception:
                cur -= timedelta(days=1)
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

        for csv_path in files[:3]:
            try:
                df = pd.read_csv(csv_path)
                parsed = parse_bhavcopy_row(df, ticker)
                if parsed and parsed.get("close", 0.0) > 0:
                    return parsed["close"]
            except Exception:
                continue
        return None


if __name__ == "__main__":
    import time
    md = MarketDataFallback()
    test_symbols = ["FEDBAN", "MANAFI", "POWINF", "INDGRI", "NIPSIL", "GOLDBEES", "INFY"]

    print("\n--- 1. Testing Normalization ---")
    for s in test_symbols:
        print(f"{s:<10} -> NSE: {normalize_to_nse(s):<12} | YF: {normalize_to_yfinance(s):<15} | Breeze: {normalize_to_breeze(s)}")

    print("\n--- 2. Testing Last Trading Date ---")
    print(f"Last trading date: {get_last_trading_date()}")

    print("\n--- 3. Testing Single Price Waterfall ---")
    p, src = md.get_price("FEDBAN")
    print(f"FEDBAN: ₹{p:.2f} via {src}")

    print("\n--- 4. Testing Vectorized Batch Mode ---")
    t0 = time.perf_counter()
    batch_res = md.get_prices_batch(test_symbols)
    dt = time.perf_counter() - t0
    for sym, (val, src) in batch_res.items():
        print(f"  {sym:<10}: ₹{val:8.2f} [{src}]")
    print(f"Batch ({len(test_symbols)} tickers) completed in {dt:.2f}s")
