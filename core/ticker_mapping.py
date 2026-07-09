"""Maps this app's Breeze stock_code values to yfinance ticker symbols."""

DEFAULT_SUFFIX = ".NS"
OVERRIDES: dict[str, str] = {
    "_NIFTY50": "^NSEI",
    # Add per-ticker entries here if a Breeze stock_code diverges from the
    # standard NSE trading symbol (confirm case-by-case via breeze.get_names()).
}

def map_to_yfinance_ticker(stock_code: str) -> str:
    return OVERRIDES.get(stock_code, f"{stock_code}{DEFAULT_SUFFIX}")
