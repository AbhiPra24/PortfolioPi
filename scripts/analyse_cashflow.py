#!/usr/bin/env python3
"""
Ollama Dividend & Cashflow Pacing Planner.
Extracts dividend yield on cost, expected distributions, REIT/InvIT quarterly payouts,
and cross-references monthly SIP obligations to provide a complete cashflow health report.
"""

import argparse
import json
import os
import sys

from ollama_common import (
    get_db_connection,
    resolve_db_path,
    resolve_ollama_endpoint,
    resolve_ollama_model,
    stream_ollama
)


def extract_cashflow_data(db_path: str) -> dict:
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # 1. Full dividend schedule
    cursor.execute("""
        SELECT ticker, stock_code, asset_type, expected_dpu, payout_frequency,
               last_ex_date, next_expected_payout, est_annual_cashflow, yield_on_cost
        FROM dividend_schedule
        ORDER BY CAST(est_annual_cashflow AS REAL) DESC
    """)
    div_rows = cursor.fetchall()

    dividends = []
    total_est_annual = 0.0
    by_asset_type = {}

    for r in div_rows:
        annual_cf = float(r["est_annual_cashflow"] or 0)
        total_est_annual += annual_cf
        atype = r["asset_type"] or "OTHER"
        by_asset_type[atype] = by_asset_type.get(atype, 0.0) + annual_cf

        dividends.append({
            "ticker": r["ticker"],
            "asset_type": atype,
            "expected_dpu": float(r["expected_dpu"] or 0),
            "payout_frequency": r["payout_frequency"],
            "next_expected_payout": r["next_expected_payout"],
            "est_annual_cashflow": round(annual_cf, 2),
            "yield_on_cost_pct": round(float(r["yield_on_cost"] or 0) * 100, 2)
        })

    # 2. SIP commitments grouped by category
    cursor.execute("""
        SELECT ticker, asset_category, monthly_sip, execution_day, quant_stage, target_action, status
        FROM sip_book
        WHERE status = 'ACTIVE' OR status = 'active'
        ORDER BY CAST(monthly_sip AS REAL) DESC
    """)
    sip_rows = cursor.fetchall()

    sips = []
    total_monthly_sip = 0.0
    sip_by_category = {}

    for r in sip_rows:
        amt = float(r["monthly_sip"] or 0)
        total_monthly_sip += amt
        cat = r["asset_category"] or "General"
        sip_by_category[cat] = sip_by_category.get(cat, 0.0) + amt

        sips.append({
            "ticker": r["ticker"],
            "category": cat,
            "monthly_sip": round(amt, 2),
            "execution_day": r["execution_day"],
            "quant_stage": r["quant_stage"],
            "target_action": r["target_action"]
        })

    # 3. Overall Portfolio Current Value for Yield Benchmarking
    cursor.execute("""
        SELECT total_invested, total_current_value
        FROM portfolio_value_history
        ORDER BY timestamp DESC LIMIT 1
    """)
    val_row = cursor.fetchone()
    total_invested = float(val_row["total_invested"]) if val_row else 1.0
    total_cur_val = float(val_row["total_current_value"]) if val_row else 1.0

    conn.close()

    portfolio_yield_on_cost = (total_est_annual / total_invested * 100) if total_invested > 0 else 0.0
    portfolio_current_yield = (total_est_annual / total_cur_val * 100) if total_cur_val > 0 else 0.0
    dividend_reinvestment_coverage = (total_est_annual / (total_monthly_sip * 12) * 100) if total_monthly_sip > 0 else 0.0

    return {
        "cashflow_summary": {
            "total_estimated_annual_dividend": round(total_est_annual, 2),
            "monthly_dividend_run_rate": round(total_est_annual / 12, 2),
            "total_monthly_sip_outflow": round(total_monthly_sip, 2),
            "annual_sip_outflow": round(total_monthly_sip * 12, 2),
            "net_annual_capital_addition": round((total_monthly_sip * 12) - total_est_annual, 2),
            "portfolio_yield_on_cost_pct": round(portfolio_yield_on_cost, 2),
            "portfolio_current_yield_pct": round(portfolio_current_yield, 2),
            "dividend_sip_coverage_pct": round(dividend_reinvestment_coverage, 2)
        },
        "dividend_by_asset_type": {k: round(v, 2) for k, v in by_asset_type.items()},
        "sip_by_category": {k: round(v, 2) for k, v in sip_by_category.items()},
        "top_dividend_contributors": dividends[:8],
        "top_sip_allocations": sips[:8],
        "misaligned_sips": [s for s in sips if "SELL" in (s["target_action"] or "") or "STOP" in (s["target_action"] or "")]
    }


def main():
    parser = argparse.ArgumentParser(description="Audit dividend cashflow and SIP pacing with Ollama")
    parser.add_argument("--db", default=resolve_db_path(), help="Path to SQLite database")
    parser.add_argument("--ollama-url", default=resolve_ollama_endpoint(), help="Ollama base URL")
    parser.add_argument("--model", default=resolve_ollama_model(), help="Ollama model name")
    parser.add_argument("--dry-run", action="store_true", help="Print extracted metrics and exit")
    args = parser.parse_args()

    data = extract_cashflow_data(args.db)

    if args.dry_run:
        print(json.dumps(data, indent=2))
        return

    system_prompt = (
        "You are an income investing strategist and personal financial controller. "
        "You analyze dividend sustainability, distribution coverage, and systematic investment plan (SIP) cash pacing."
    )

    prompt = f"""
Review the cashflow, dividend income, and systematic investment plan (SIP) schedule below:

CASHFLOW & SIP AUDIT METRICS:
```json
{json.dumps(data, indent=2)}
```

Generate a structured strategic review covering:
1. **Dividend Engine & Yield Sustainability**:
   - Assess total annual dividend run rate vs capital invested.
   - Evaluate asset class diversification (InvITs vs REITs vs PSUs/Equities vs ETFs).
   - High-yield sustainability check on top payers.

2. **SIP Pacing vs Stage Alignment**:
   - Audit the monthly SIP outflow against the quantified target actions.
   - Specifically address the `misaligned_sips` (SIPs that are currently active despite algorithms advising STOP or SELL).

3. **Dividend Reinvestment Power (DRIP)**:
   - What percentage of the monthly SIP is organically self-funded by dividend cashflow?
   - Strategic roadmap to grow dividend coverage to 100% of monthly SIP outflow.

4. **Recommended Cashflow Adjustments**:
   - Immediate SIP redirects, pauses, or redeployments into higher-quality Stage 1/2 dividend compounders.
"""

    print(f"[*] Analyzing cashflow & dividend schedule via Ollama ({args.model} @ {args.ollama_url})...\n" + "=" * 60 + "\n")
    stream_ollama(prompt, system_prompt=system_prompt, base_url=args.ollama_url, model=args.model)
    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
