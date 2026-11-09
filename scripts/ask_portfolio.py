#!/usr/bin/env python3
"""
Ollama Interactive Natural Language Portfolio Query Assistant.
Allows you to ask freeform questions about your portfolio, holdings, stage shifts,
cashflow, and alerts. Pre-injects live database stats so Ollama doesn't hallucinate dummy symbols.
"""

import argparse
import os
import sys

# Ensure script directory is on sys.path for relative import
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ollama_common import (
    get_db_connection,
    resolve_db_path,
    resolve_fallback_endpoint,
    resolve_fallback_model,
    resolve_ollama_endpoint,
    resolve_ollama_model,
    stream_ollama,
)


def get_live_portfolio_context(db_path: str) -> dict:
    """Pre-compute the active portfolio universe and tables so Ollama has ground truth."""
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # Portfolio value
    cursor.execute("""
        SELECT total_invested, total_current_value, total_pnl
        FROM portfolio_value_history
        ORDER BY timestamp DESC LIMIT 1
    """)
    val = cursor.fetchone()
    tot_inv = float(val["total_invested"]) if val else 0.0
    tot_cur = float(val["total_current_value"]) if val else 0.0
    tot_pnl = float(val["total_pnl"]) if val else 0.0

    # Holdings with latest stage
    cursor.execute("""
        SELECT h.stock_code,
               CAST(h.quantity AS REAL) as qty,
               CAST(h.average_price AS REAL) as avg_price,
               CAST(h.current_price AS REAL) as cur_price,
               (CAST(h.quantity AS REAL) * CAST(h.current_price AS REAL)) as cur_val,
               (CAST(h.quantity AS REAL) * (CAST(h.current_price AS REAL) - CAST(h.average_price AS REAL))) as pnl,
               st.stage, st.slope
        FROM holdings_snapshot h
        LEFT JOIN (
            SELECT s1.stock_code, s1.stage, s1.slope
            FROM stage_history s1
            JOIN (SELECT stock_code as sc, MAX(date) as md FROM stage_history GROUP BY stock_code) s2
            ON s1.stock_code = s2.sc AND s1.date = s2.md
        ) st ON h.stock_code = st.stock_code
        ORDER BY cur_val DESC
    """)
    holdings = [dict(r) for r in cursor.fetchall()]

    # Dividends
    cursor.execute("""
        SELECT ticker, asset_type, expected_dpu, est_annual_cashflow, yield_on_cost
        FROM dividend_schedule
        WHERE est_annual_cashflow > 0
        ORDER BY CAST(est_annual_cashflow AS REAL) DESC
    """)
    dividends = [dict(r) for r in cursor.fetchall()]

    conn.close()

    # Create a compact text table of holdings that small local LLMs easily parse
    holdings_lines = []
    for rank, h in enumerate(holdings, start=1):
        holdings_lines.append(
            f"Rank #{rank}: {h['stock_code']} | Value: ₹{h['cur_val']:,.2f} | PnL: ₹{h['pnl']:,.2f} | Stage: {h['stage'] or 'Unknown'} | Slope: {h['slope']}"
        )

    return {
        "summary": f"Total Invested: ₹{tot_inv:,.2f} | Current Value: ₹{tot_cur:,.2f} | Total PnL: ₹{tot_pnl:,.2f} ({(tot_pnl / tot_inv * 100):.2f}%)",
        "ranked_holdings": "\n".join(holdings_lines),
        "top_dividend_cashflows": [
            f"{d['ticker']} ({d['asset_type']}): ₹{float(d['est_annual_cashflow'] or 0):,.2f}/yr" for d in dividends[:5]
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="Interactive Ollama Assistant for PortfolioPi")
    parser.add_argument("--db", default=resolve_db_path(), help="Path to SQLite database")
    parser.add_argument("--ollama-url", default=resolve_ollama_endpoint(), help="Primary Ollama base URL")
    parser.add_argument("--model", default=resolve_ollama_model(), help="Primary Ollama model name")
    parser.add_argument("--fallback-url", default=resolve_fallback_endpoint(), help="Fallback Ollama base URL")
    parser.add_argument("--fallback-model", default=resolve_fallback_model(), help="Fallback Ollama model name")
    parser.add_argument("--query", "-q", help="Single query mode instead of interactive loop")
    args = parser.parse_args()

    ctx = get_live_portfolio_context(args.db)

    def process_query(user_query: str):
        prompt = f"""You are the quantitative assistant for PortfolioPi.
Answer the user question using strictly the following verified portfolio records:

PORTFOLIO OVERVIEW:
{ctx["summary"]}

TOP DIVIDEND PAYERS:
{chr(10).join(ctx["top_dividend_cashflows"])}

RANKED HOLDINGS (Highest Market Value to Lowest):
{ctx["ranked_holdings"]}

QUESTION:
{user_query}

Direct concise answer:"""

        print("\n[AI Analyst]:")
        stream_ollama(
            prompt,
            base_url=args.ollama_url,
            model=args.model,
            fallback_url=args.fallback_url,
            fallback_model=args.fallback_model,
        )
        print()

    if args.query:
        process_query(args.query)
        return

    print("=" * 65)
    print(f"PortfolioPi AI Assistant Active (Ollama: {args.model})")
    print(f"Database: {args.db}")
    print("Ask any question (e.g. 'What are my top 3 holdings and their stages?', 'Which stocks are in Stage 4?')")
    print("Type 'exit' or 'quit' to close.")
    print("=" * 65 + "\n")

    while True:
        try:
            q = input("Ask PortfolioPi > ").strip()
            if not q:
                continue
            if q.lower() in ("exit", "quit", "q"):
                print("Goodbye!")
                break
            process_query(q)
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            break


if __name__ == "__main__":
    main()
