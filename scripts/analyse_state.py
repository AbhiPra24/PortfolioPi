#!/usr/bin/env python3
"""
Ollama Ad-Hoc Portfolio State Analyst
Extracts current portfolio state, stage classifications, PnL, cashflows, and risk metrics,
then prompts Ollama to produce a senior quantitative audit and advisory brief.
Zero third-party dependencies required (pure standard library: sqlite3, urllib, json).
"""

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

try:
    from scripts.ollama_common import (
        resolve_fallback_endpoint,
        resolve_fallback_model,
        resolve_ollama_endpoint,
        resolve_ollama_model,
        stream_ollama,
    )
except ImportError:
    from ollama_common import (
        resolve_fallback_endpoint,
        resolve_fallback_model,
        resolve_ollama_endpoint,
        resolve_ollama_model,
        stream_ollama,
    )


def get_default_db_path() -> str:
    candidates = [
        os.getenv("SQLITE_DB_PATH"),
        os.getenv("DB_PATH"),
        "data/portfoliopi.db",
        "backups/portfoliopi_local.db",
    ]
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return "backups/portfoliopi_local.db"


def fetch_portfolio_state(db_path: str) -> dict:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"Database not found at: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    state = {}

    # 1. Total Portfolio History (latest snapshot)
    cursor.execute("""
        SELECT timestamp, total_invested, total_current_value, total_pnl
        FROM portfolio_value_history
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    val_row = cursor.fetchone()
    if val_row:
        total_inv = float(val_row["total_invested"])
        total_cur = float(val_row["total_current_value"])
        total_pnl = float(val_row["total_pnl"])
        pnl_pct = (total_pnl / total_inv * 100) if total_inv > 0 else 0.0
        state["portfolio_summary"] = {
            "timestamp": val_row["timestamp"],
            "total_invested": round(total_inv, 2),
            "total_current_value": round(total_cur, 2),
            "total_pnl": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        }
    else:
        state["portfolio_summary"] = {}

    # 2. Holdings with latest Stage Analysis
    cursor.execute("""
        SELECT h.stock_code,
               CAST(h.quantity AS REAL) as qty,
               CAST(h.average_price AS REAL) as avg_price,
               CAST(h.current_price AS REAL) as cur_price,
               (CAST(h.quantity AS REAL) * CAST(h.current_price AS REAL)) as cur_val,
               (CAST(h.quantity AS REAL) * (CAST(h.current_price AS REAL) - CAST(h.average_price AS REAL))) as pnl,
               s.stage,
               s.slope,
               s.sma_150
        FROM holdings_snapshot h
        LEFT JOIN (
            SELECT s1.stock_code, s1.stage, s1.slope, s1.sma_150
            FROM stage_history s1
            JOIN (
                SELECT stock_code, MAX(date) as max_date
                FROM stage_history
                GROUP BY stock_code
            ) s2 ON s1.stock_code = s2.stock_code AND s1.date = s2.max_date
        ) s ON h.stock_code = s.stock_code
        ORDER BY cur_val DESC
    """)

    holdings_rows = cursor.fetchall()
    total_val = sum(r["cur_val"] for r in holdings_rows) if holdings_rows else 1.0

    stage_breakdown = {"1": 0, "2": 0, "3": 0, "4": 0, "Unknown": 0}
    stage_value = {"1": 0.0, "2": 0.0, "3": 0.0, "4": 0.0, "Unknown": 0.0}

    holdings = []
    for r in holdings_rows:
        stg = str(r["stage"]) if r["stage"] else "Unknown"
        if stg not in stage_breakdown:
            stg = "Unknown"
        stage_breakdown[stg] += 1
        stage_value[stg] += r["cur_val"] or 0.0

        pnl_pct = ((r["cur_price"] - r["avg_price"]) / r["avg_price"] * 100) if r["avg_price"] > 0 else 0.0
        alloc_pct = (r["cur_val"] / total_val * 100) if total_val > 0 else 0.0

        holdings.append(
            {
                "ticker": r["stock_code"],
                "qty": r["qty"],
                "avg_price": round(r["avg_price"], 2),
                "cur_price": round(r["cur_price"], 2),
                "cur_val": round(r["cur_val"], 2),
                "pnl": round(r["pnl"], 2),
                "pnl_pct": round(pnl_pct, 2),
                "allocation_pct": round(alloc_pct, 2),
                "stage": stg,
                "slope": round(float(r["slope"]), 4) if r["slope"] is not None else None,
            }
        )

    state["holdings_count"] = len(holdings)
    state["stage_distribution_count"] = stage_breakdown
    state["stage_distribution_value_pct"] = {k: round(v / total_val * 100, 2) for k, v in stage_value.items()}
    state["top_5_allocations"] = holdings[:5]
    state["top_5_gainers"] = sorted(holdings, key=lambda x: x["pnl"], reverse=True)[:5]
    state["top_5_laggards"] = sorted(holdings, key=lambda x: x["pnl"])[:5]
    state["stage_4_decay_holdings"] = [h for h in holdings if h["stage"] == "4"]

    # 3. Dividend Cashflows
    cursor.execute("""
        SELECT ticker, asset_type, expected_dpu, payout_frequency, est_annual_cashflow, yield_on_cost
        FROM dividend_schedule
        WHERE est_annual_cashflow > 0
        ORDER BY est_annual_cashflow DESC
    """)
    div_rows = cursor.fetchall()
    total_est_div = sum(float(r["est_annual_cashflow"] or 0) for r in div_rows)
    state["dividend_summary"] = {
        "tracked_dividend_assets": len(div_rows),
        "total_annual_est_dividend": round(total_est_div, 2),
        "top_payers": [
            {
                "ticker": r["ticker"],
                "type": r["asset_type"],
                "annual_est": round(float(r["est_annual_cashflow"] or 0), 2),
                "yield_on_cost_pct": round(float(r["yield_on_cost"] or 0) * 100, 2),
            }
            for r in div_rows[:5]
        ],
    }

    # 4. SIP Book Health
    cursor.execute("""
        SELECT ticker, asset_category, monthly_sip, quant_stage, target_action, status
        FROM sip_book
        WHERE status = 'ACTIVE' OR status = 'active'
    """)
    sip_rows = cursor.fetchall()
    state["sip_summary"] = {
        "active_sips": len(sip_rows),
        "total_monthly_commitment": round(sum(float(r["monthly_sip"] or 0) for r in sip_rows), 2),
        "sips": [
            {
                "ticker": r["ticker"],
                "category": r["asset_category"],
                "monthly_sip": round(float(r["monthly_sip"] or 0), 2),
                "quant_stage": r["quant_stage"],
                "target_action": r["target_action"],
            }
            for r in sip_rows
        ],
    }

    conn.close()
    return state


def query_ollama(prompt: str, ollama_url: str = "http://localhost:11434", model: str = "qwen2.5:7b") -> None:
    endpoint = f"{ollama_url.rstrip('/')}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": True, "options": {"temperature": 0.2, "top_p": 0.9}}

    req = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req) as resp:
            for line in resp:
                if line:
                    chunk = json.loads(line.decode("utf-8"))
                    text = chunk.get("response", "")
                    sys.stdout.write(text)
                    sys.stdout.flush()
            print()
    except urllib.error.URLError as e:
        sys.stderr.write(f"\n[Error] Unable to reach Ollama at {ollama_url}: {e}\n")
        sys.stderr.write("Make sure Ollama is running (`ollama serve`).\n")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Analyze current PortfolioPi state via Ollama")
    parser.add_argument("--db", default=get_default_db_path(), help="Path to SQLite database")
    parser.add_argument("--ollama-url", default=resolve_ollama_endpoint(), help="Primary Ollama base URL")
    parser.add_argument("--model", default=resolve_ollama_model(), help="Primary Ollama model name")
    parser.add_argument("--fallback-url", default=resolve_fallback_endpoint(), help="Fallback Ollama base URL")
    parser.add_argument("--fallback-model", default=resolve_fallback_model(), help="Fallback Ollama model name")
    parser.add_argument("--dry-run", action="store_true", help="Dump extracted metrics JSON without calling Ollama")
    args = parser.parse_args()

    print(f"[*] Reading portfolio state from: {args.db}")
    state = fetch_portfolio_state(args.db)

    if args.dry_run:
        print(json.dumps(state, indent=2))
        return

    prompt = f"""
You are a senior quantitative portfolio manager and Stan Weinstein Stage Analysis expert.
Analyze the following current state metrics extracted from PortfolioPi's local SQLite database.

PORTFOLIO AUDIT DATA:
```json
{json.dumps(state, indent=2)}
```

Provide an executive, actionable analysis with the following structured sections:
1. **Executive Portfolio Health Check**:
   - Total capital invested, current market value, overall PnL, and ROI.
   - High-level verdict on portfolio momentum and risk posture.

2. **Stage Analysis & Capital Allocation**:
   - Evaluate the capital split across stages (Stage 1 Accumulation, Stage 2 Markup, Stage 3 Distribution, Stage 4 Capitulation/Markdown).
   - Highlighting critical warnings for any assets currently in **Stage 4** or **Stage 3** (especially those with negative slope).

3. **Key Position Diagnostics**:
   - Top compounders/winners: Are they over-allocated or nearing distribution?
   - Top laggards / underwater positions: Specific trim or hold recommendations based on stage and slope.
   - Concentration risk in the top 5 assets.

4. **Cash Flow & SIP Discipline**:
   - Passive income yield from dividend schedule.
   - Review of monthly SIP commitments vs current stage alignment.

5. **Action Plan (Next 1-2 Trading Weeks)**:
   - Bulleted list of prioritized, defensive or offensive portfolio actions (Take Profits, Stop Losses, SIP Pause/Boost).
"""

    print(
        f"[*] Prompting Ollama (Primary: {args.model} @ {args.ollama_url} | Fallback: {args.fallback_model} @ {args.fallback_url})...\n"
        + "=" * 60
        + "\n"
    )
    stream_ollama(
        prompt,
        base_url=args.ollama_url,
        model=args.model,
        fallback_url=args.fallback_url,
        fallback_model=args.fallback_model,
    )
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
