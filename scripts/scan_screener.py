#!/usr/bin/env python3
"""
Screener.in Market Scanner & Live Price Indexer.
Scrapes top curated screens from Screener.in (Golden Crossover, High Volume,
GARP, FII Buying, Capex Expansion, Coffee Can, High Dividend).
Enriches screened stocks with live tick data (via Yahoo Finance API / Breeze mapping)
and optionally feeds candidates to Ollama for qualitative/stage screening.
Zero third-party dependencies required (pure standard library).
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request
import urllib.error
from typing import Dict, List, Optional

# Ensure scripts directory is on sys.path for relative import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ollama_common import (
    resolve_ollama_endpoint,
    resolve_ollama_model,
    stream_ollama
)

SCREENER_PRESETS = {
    "golden-crossover": {
        "title": "Golden Crossover (50 DMA > 200 DMA)",
        "url": "https://www.screener.in/screens/336509/golden-crossover/",
        "description": "50-day moving average crossing above the 200-day moving average from below (Stage 2 transition)."
    },
    "price-volume": {
        "title": "Price & Volume Breakout (>5x Volume)",
        "url": "https://www.screener.in/screens/440753/price-volume-action/",
        "description": "Weekly volume surge >5x with positive price momentum."
    },
    "garp": {
        "title": "Growth at a Reasonable Price (GARP)",
        "url": "https://www.screener.in/screens/178/growth-stocks/",
        "description": "High quarterly earnings momentum combined with reasonable valuation and return metrics."
    },
    "fii-buying": {
        "title": "FII Institutional Inflows",
        "url": "https://www.screener.in/screens/343087/fii-buying/",
        "description": "Companies witnessing continuous institutional stake accumulation."
    },
    "capacity-expansion": {
        "title": "Major Capex & Capacity Expansion",
        "url": "https://www.screener.in/screens/97687/capacity-expansion/",
        "description": "Companies where fixed assets or CWIP have grown >50% in the last year."
    },
    "coffee-can": {
        "title": "Coffee Can Long-Term Compounders",
        "url": "https://www.screener.in/screens/57601/coffee-can-portfolio/",
        "description": "Consistent 10Y Revenue Growth >10% and ROCE >15%."
    },
    "high-dividend": {
        "title": "Highest Sustainable Dividend Yield",
        "url": "https://www.screener.in/screens/3/highest-dividend-yield-shares/",
        "description": "High, consistent dividend-paying companies for cashflow generation."
    }
}


def fetch_live_quote(ticker: str) -> Optional[dict]:
    """Fetch live market price and intraday stats from Yahoo Finance v8 JSON API."""
    clean_ticker = ticker.strip().upper()
    if clean_ticker.isdigit():
        symbol = f"{clean_ticker}.BO"
    else:
        symbol = f"{clean_ticker}.NS"

    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1d"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            cur_price = meta.get("regularMarketPrice")
            prev_close = meta.get("chartPreviousClose")
            day_change = ((cur_price - prev_close) / prev_close * 100) if cur_price and prev_close else 0.0
            return {
                "symbol": symbol,
                "current_price": round(cur_price, 2) if cur_price else None,
                "prev_close": round(prev_close, 2) if prev_close else None,
                "day_change_pct": round(day_change, 2),
                "day_high": meta.get("regularMarketDayHigh"),
                "day_low": meta.get("regularMarketDayLow")
            }
    except Exception:
        return None


def scrape_screen(screen_url: str, limit: int = 15) -> List[dict]:
    """Scrape and parse stock screen table from Screener.in."""
    req = urllib.request.Request(
        screen_url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            content = resp.read().decode("utf-8")
    except Exception as e:
        sys.stderr.write(f"[Error] Failed to fetch {screen_url}: {e}\n")
        return []

    # Parse rows
    row_matches = re.findall(r'<tr[^>]*data-row-company-id=.*?>(.*?)</tr>', content, re.DOTALL)
    results = []

    for r in row_matches[:limit]:
        # Extract ticker link
        sym_match = re.search(r'<a href=\"/company/([A-Z0-9]+)/[^\"]*\"[^>]*>(.*?)</a>', r, re.DOTALL)
        if not sym_match:
            continue
        ticker = sym_match.group(1).strip()
        name = html.unescape(re.sub('<[^<]+?>', '', sym_match.group(2)).strip())

        # Extract table cells
        cells = [html.unescape(re.sub('<[^<]+?>', '', td).strip()) for td in re.findall(r'<td[^>]*>(.*?)</td>', r, re.DOTALL)]

        cmp = cells[2] if len(cells) > 2 else "N/A"
        pe = cells[3] if len(cells) > 3 else "N/A"
        market_cap = cells[4] if len(cells) > 4 else "N/A"

        results.append({
            "ticker": ticker,
            "name": name,
            "screener_cmp": cmp,
            "pe": pe,
            "market_cap_cr": market_cap
        })

    return results


def main():
    parser = argparse.ArgumentParser(description="Screener.in Market Scanner & Live Price Indexer")
    parser.add_argument(
        "--screen",
        choices=list(SCREENER_PRESETS.keys()) + ["all"],
        default="golden-crossover",
        help="Screener algorithm preset"
    )
    parser.add_argument("--limit", type=int, default=10, help="Max stocks to fetch per screen")
    parser.add_argument("--live", action="store_true", help="Fetch live Yahoo Finance quotes for each stock")
    parser.add_argument("--ai", action="store_true", help="Pass candidates to Ollama for technical stage evaluation")
    parser.add_argument("--ollama-url", default=resolve_ollama_endpoint(), help="Ollama base URL")
    parser.add_argument("--model", default=resolve_ollama_model(), help="Ollama model name")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Output raw JSON")
    args = parser.parse_args()

    active_screens = list(SCREENER_PRESETS.keys()) if args.screen == "all" else [args.screen]

    aggregated_results = {}

    for s_key in active_screens:
        config = SCREENER_PRESETS[s_key]
        print(f"\n[*] Scanning: {config['title']} ({config['url']})...")
        stocks = scrape_screen(config["url"], limit=args.limit)

        if args.live:
            print("    [+] Indexing live prices...")
            for s in stocks:
                q = fetch_live_quote(s["ticker"])
                if q and q["current_price"]:
                    s["live_price"] = q["current_price"]
                    s["day_change_pct"] = q["day_change_pct"]
                else:
                    s["live_price"] = s["screener_cmp"]
                    s["day_change_pct"] = 0.0

        aggregated_results[s_key] = {
            "title": config["title"],
            "description": config["description"],
            "count": len(stocks),
            "stocks": stocks
        }

        if not args.as_json:
            print(f"{'Ticker':<12} | {'Company Name':<28} | {'Price (₹)':<10} | {'P/E':<8} | {'M.Cap (Cr)':<10}")
            print("-" * 75)
            for s in stocks:
                price_str = str(s.get("live_price", s["screener_cmp"]))
                print(f"{s['ticker']:<12} | {s['name'][:27]:<28} | {price_str:<10} | {s['pe']:<8} | {s['market_cap_cr']:<10}")

    if args.as_json:
        print(json.dumps(aggregated_results, indent=2))
        return

    if args.ai:
        print(f"\n[*] Synthesizing findings with Ollama ({args.model})...\n" + "=" * 65 + "\n")
        prompt = f"""
You are an institutional equity strategist and Stage Analysis expert.
Analyze the following newly screened Indian stock market candidates extracted from Screener.in:

CANDIDATES DATA:
```json
{json.dumps(aggregated_results, indent=2)}
```

Provide a high-conviction screening memo:
1. **Top 3 Standout Accumulation Candidates**:
   - Highlight the strongest candidates based on valuation (P/E), sector tailwinds, and breakout potential.
2. **Value Traps / High Risk Flags**:
   - Warn against any illiquid or high-P/E names from the screen.
3. **Suggested Watchlist Additions for PortfolioPi**:
   - Exactly which 2-3 tickers should be added to our local tracking database for 150-day moving average monitoring.
"""
        stream_ollama(prompt, base_url=args.ollama_url, model=args.model)
        print("\n" + "=" * 65)


if __name__ == "__main__":
    main()
