#!/usr/bin/env python3
"""Automated Portfolio P&L & Trade Performance Analytics.

Analyzes:
1. Trade executions vs current portfolio holdings and signals.
2. Realized turnover, total transaction drag (brokerage + taxes as % of turnover),
   average trade size, win rate / exit cost analysis based on executed sells and buys.
3. Breeze API recent trade executions and/or local database historical trades.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import aiosqlite
from app_config import settings

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False


def calculate_trade_metrics(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Pure analytics function to compute turnover, drag, trade sizes, and win/loss stats.

    Handles Breeze trade payloads or DB record dictionaries.
    Standard Breeze trade keys:
        - action / order_action: 'Buy' / 'Sell'
        - quantity / qty / match_qty: int
        - average_cost / trade_price / price / ltp: float
        - brokerage_amount / brokerage: float
        - total_taxes / taxes: float
        - stock_code: str
        - trade_date: str
    """
    total_turnover = 0.0
    total_brokerage = 0.0
    total_taxes = 0.0
    buy_turnover = 0.0
    sell_turnover = 0.0
    buy_count = 0
    sell_count = 0
    trade_sizes: List[float] = []

    # Map trades per stock for matched pair exit / PnL estimation
    stock_buys: Dict[str, List[Dict[str, Any]]] = {}
    stock_sells: Dict[str, List[Dict[str, Any]]] = {}

    for t in trades:
        action = str(t.get("action") or t.get("order_action") or "").strip().title()
        qty = int(t.get("quantity") or t.get("qty") or t.get("match_qty") or 0)
        
        # Turn-over cost calculation:
        # In Breeze, average_cost is often total trade value (qty * price) or average fill cost
        raw_cost = float(t.get("average_cost") or 0.0)
        price = float(t.get("price") or t.get("trade_price") or t.get("ltp") or 0.0)
        
        if raw_cost > 0 and raw_cost >= price * max(qty, 1) * 0.9:
            trade_value = raw_cost
        elif price > 0 and qty > 0:
            trade_value = price * qty
        else:
            trade_value = raw_cost

        brok = float(t.get("brokerage_amount") or t.get("brokerage") or 0.0)
        taxes = float(t.get("total_taxes") or t.get("taxes") or 0.0)
        stock_code = str(t.get("stock_code") or "").upper().strip()

        total_turnover += trade_value
        total_brokerage += brok
        total_taxes += taxes
        if trade_value > 0:
            trade_sizes.append(trade_value)

        trade_item = {
            "stock_code": stock_code,
            "action": action,
            "quantity": qty,
            "price": price if price > 0 else (trade_value / qty if qty > 0 else 0.0),
            "trade_value": trade_value,
            "brokerage": brok,
            "taxes": taxes,
            "trade_date": t.get("trade_date") or t.get("execution_date") or "",
        }

        if action == "Buy":
            buy_count += 1
            buy_turnover += trade_value
            stock_buys.setdefault(stock_code, []).append(trade_item)
        elif action == "Sell":
            sell_count += 1
            sell_turnover += trade_value
            stock_sells.setdefault(stock_code, []).append(trade_item)

    total_frictions = total_brokerage + total_taxes
    drag_pct = (total_frictions / total_turnover * 100.0) if total_turnover > 0 else 0.0
    avg_trade_size = (sum(trade_sizes) / len(trade_sizes)) if trade_sizes else 0.0

    # Win rate and Realized P&L calculation on closed round-trips
    # Match Sells against Buys using FIFO for stocks present in both
    winning_trades = 0
    losing_trades = 0
    breakeven_trades = 0
    realized_gross_pnl = 0.0

    for stock, sells in stock_sells.items():
        buys = list(stock_buys.get(stock, []))
        if not buys:
            continue
        
        buy_idx = 0
        cur_buy = dict(buys[buy_idx]) if buy_idx < len(buys) else None

        for s in sells:
            sell_rem_qty = s["quantity"]
            sell_price = s["price"]

            while sell_rem_qty > 0 and cur_buy is not None:
                matched_qty = min(sell_rem_qty, cur_buy["quantity"])
                pnl = (sell_price - cur_buy["price"]) * matched_qty
                realized_gross_pnl += pnl

                if pnl > 0.01:
                    winning_trades += 1
                elif pnl < -0.01:
                    losing_trades += 1
                else:
                    breakeven_trades += 1

                sell_rem_qty -= matched_qty
                cur_buy["quantity"] -= matched_qty

                if cur_buy["quantity"] <= 0:
                    buy_idx += 1
                    cur_buy = dict(buys[buy_idx]) if buy_idx < len(buys) else None

    closed_trades_count = winning_trades + losing_trades + breakeven_trades
    win_rate_pct = (winning_trades / closed_trades_count * 100.0) if closed_trades_count > 0 else 0.0
    net_realized_pnl = realized_gross_pnl - total_frictions

    return {
        "total_trades": len(trades),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "total_turnover": round(total_turnover, 2),
        "buy_turnover": round(buy_turnover, 2),
        "sell_turnover": round(sell_turnover, 2),
        "total_brokerage": round(total_brokerage, 2),
        "total_taxes": round(total_taxes, 2),
        "total_frictions": round(total_frictions, 2),
        "drag_pct": round(drag_pct, 3),
        "avg_trade_size": round(avg_trade_size, 2),
        "closed_trades_count": closed_trades_count,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate_pct": round(win_rate_pct, 1),
        "realized_gross_pnl": round(realized_gross_pnl, 2),
        "net_realized_pnl": round(net_realized_pnl, 2),
    }


def format_trades_summary_header(metrics: Dict[str, Any]) -> str:
    """Format a compact HTML summary block for Telegram and reports."""
    turnover = metrics.get("total_turnover", 0.0)
    drag_pct = metrics.get("drag_pct", 0.0)
    brok = metrics.get("total_brokerage", 0.0)
    taxes = metrics.get("total_taxes", 0.0)
    frictions = metrics.get("total_frictions", 0.0)
    avg_size = metrics.get("avg_trade_size", 0.0)
    win_rate = metrics.get("win_rate_pct", 0.0)
    closed = metrics.get("closed_trades_count", 0)

    summary_lines = [
        "<b>📊 Trade Performance &amp; Turnover Analytics:</b>",
        f"• <b>Total Turnover:</b> ₹{turnover:,.2f} ({metrics.get('total_trades', 0)} executions)",
        f"• <b>Transaction Drag:</b> {drag_pct:.2f}% (₹{frictions:,.2f})",
        f"  ├─ Brokerage: ₹{brok:,.2f}",
        f"  └─ Taxes: ₹{taxes:,.2f}",
        f"• <b>Avg Trade Size:</b> ₹{avg_size:,.2f}",
    ]

    if closed > 0:
        pnl = metrics.get("net_realized_pnl", 0.0)
        pnl_sign = "+" if pnl >= 0 else ""
        summary_lines.append(
            f"• <b>Roundtrips Closed:</b> {closed} | <b>Win Rate:</b> {win_rate:.1f}% | <b>Net P&amp;L:</b> {pnl_sign}₹{pnl:,.2f}"
        )

    return "\n".join(summary_lines)


async def get_trades_from_db(db_path: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch trades history stored in the SQLite database."""
    trades: List[Dict[str, Any]] = []
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        # Check if trades_history exists
        cursor = await db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades_history'")
        table_exists = await cursor.fetchone()
        if not table_exists:
            return trades

        async with db.execute(
            """
            SELECT order_id, trade_id, trade_date, stock_code, action, quantity,
                   average_cost, ltp, brokerage_amount, total_taxes, product_type, exchange_code
            FROM trades_history
            ORDER BY trade_date DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ) as cur:
            async for row in cur:
                trades.append({
                    "stock_code": row["stock_code"],
                    "action": row["action"],
                    "quantity": row["quantity"],
                    "average_cost": row["average_cost"],
                    "ltp": row["ltp"],
                    "brokerage_amount": row["brokerage_amount"] or 0.0,
                    "total_taxes": row["total_taxes"] or 0.0,
                    "trade_date": row["trade_date"],
                    "trade_id": row["trade_id"],
                    "order_id": row["order_id"],
                })
    return trades


async def fetch_portfolio_context(db_path: str) -> Dict[str, Any]:
    """Fetch current portfolio holdings snapshot and latest signals to cross-reference with trades."""
    holdings: Dict[str, Dict[str, Any]] = {}
    signals: Dict[str, Dict[str, Any]] = {}

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        try:
            async with db.execute("SELECT stock_code, quantity, average_price, current_price FROM holdings_snapshot") as cur:
                async for row in cur:
                    holdings[row["stock_code"].upper()] = {
                        "quantity": row["quantity"],
                        "average_price": row["average_price"],
                        "current_price": row["current_price"],
                    }
        except Exception:
            pass

        try:
            async with db.execute(
                """
                SELECT s.stock_code, s.composite_score, s.rsi14, a.action
                FROM signals s
                LEFT JOIN stock_actions a ON s.stock_code = a.stock_code
                WHERE s.timestamp = (SELECT MAX(timestamp) FROM signals s2 WHERE s2.stock_code = s.stock_code)
                """
            ) as cur:
                async for row in cur:
                    signals[row["stock_code"].upper()] = {
                        "composite_score": row["composite_score"],
                        "rsi14": row["rsi14"],
                        "action": row["action"],
                    }
        except Exception:
            pass

    return {"holdings": holdings, "signals": signals}


async def run_trade_performance_analysis(days: int = 30, use_breeze: bool = True):
    print("=" * 70)
    print(" 📈 PORTFOLIOPI TRADE PERFORMANCE & DRAG ANALYTICS")
    print("=" * 70)

    trades: List[Dict[str, Any]] = []
    source = "Database"

    if use_breeze:
        try:
            from core.breeze_client import BreezeClient, SessionExpiredError
            from core.session_store import get_session

            token = await get_session()
            if token:
                breeze = BreezeClient(token)
                now = datetime.now(timezone.utc)
                from_date = (now - timedelta(days=days)).strftime("%Y-%m-%dT06:00:00.000Z")
                to_date = now.strftime("%Y-%m-%dT06:00:00.000Z")
                resp = breeze.get_trade_list(from_date=from_date, to_date=to_date, exchange_code="NSE")
                if resp.get("Success"):
                    trades = resp["Success"]
                    source = f"Breeze API (Last {days} days)"
        except Exception as e:
            print(f"⚠️ Could not pull live trades from Breeze: {e}. Falling back to SQLite...")

    if not trades:
        trades = await get_trades_from_db(settings.db_path, limit=100)
        source = "SQLite `trades_history`"

    if not trades:
        print(f"\n❌ No executed trades found from Breeze or database (window: {days} days).")
        return

    print(f"\nSource: {source} | Total Executions: {len(trades)}")

    # 1. Compute summary metrics
    metrics = calculate_trade_metrics(trades)
    print("\n" + "-" * 70)
    print(f"Total Turnover:      ₹{metrics['total_turnover']:,.2f}")
    print(f"Total Brokerage:     ₹{metrics['total_brokerage']:,.2f}")
    print(f"Total Taxes:         ₹{metrics['total_taxes']:,.2f}")
    print(f"Total Frictions:     ₹{metrics['total_frictions']:,.2f}")
    print(f"Transaction Drag:    {metrics['drag_pct']:.3f}%")
    print(f"Average Trade Size:  ₹{metrics['avg_trade_size']:,.2f}")
    print(f"Buys: {metrics['buy_count']} (₹{metrics['buy_turnover']:,.2f}) | Sells: {metrics['sell_count']} (₹{metrics['sell_turnover']:,.2f})")
    if metrics["closed_trades_count"] > 0:
        print(f"Roundtrips Closed:   {metrics['closed_trades_count']} | Win Rate: {metrics['win_rate_pct']:.1f}%")
        print(f"Realized Gross P&L:  ₹{metrics['realized_gross_pnl']:,.2f}")
        print(f"Net Realized P&L:    ₹{metrics['net_realized_pnl']:,.2f}")
    print("-" * 70)

    # 2. Correlate with current holdings and signals
    context = await fetch_portfolio_context(settings.db_path)
    holdings = context["holdings"]
    signals = context["signals"]

    rows = []
    for t in trades[:20]:
        code = str(t.get("stock_code") or "").upper().strip()
        act = str(t.get("action") or t.get("order_action") or "").title()
        qty = int(t.get("quantity") or t.get("qty") or 0)
        cost = float(t.get("average_cost") or 0.0)
        price = float(t.get("price") or t.get("ltp") or 0.0)
        val = cost if cost > 0 else (price * qty)
        brok = float(t.get("brokerage_amount") or t.get("brokerage") or 0.0)
        t_date = str(t.get("trade_date") or "")[:10]

        h = holdings.get(code, {})
        curr_qty = h.get("quantity", "-")
        sig = signals.get(code, {})
        algo_action = sig.get("action", "-")

        rows.append([
            t_date,
            code,
            act,
            qty,
            f"₹{val:,.1f}",
            f"₹{brok:.2f}",
            curr_qty,
            algo_action,
        ])

    print("\n📋 EXECUTED TRADES VS PORTFOLIO & SIGNAL STATE:")
    headers = ["Date", "Symbol", "Side", "Qty", "Turnover", "Brok", "Held Qty", "Algo Action"]
    if HAS_TABULATE:
        print(tabulate(rows, headers=headers, tablefmt="fancy_grid"))
    else:
        for r in rows:
            print(f"  {r[0]} | {r[1]:<8} | {r[2]:<4} | Qty: {r[3]:<4} | Turn: {r[4]:<10} | Held: {r[6]} | Action: {r[7]}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze trade performance and portfolio drag.")
    parser.add_argument("--days", type=int, default=30, help="Lookback window in days for Breeze API (default: 30)")
    parser.add_argument("--no-breeze", action="store_true", help="Skip Breeze API and use local database trades")
    args = parser.parse_args()

    asyncio.run(run_trade_performance_analysis(days=args.days, use_breeze=not args.no_breeze))
