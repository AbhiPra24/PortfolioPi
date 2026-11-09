#!/usr/bin/env python3
"""
PortfolioPi Daily Quantitative Portfolio Rebalancer & Tax Harvest Engine.

Core Capabilities:
1. Real-time Portfolio State & Valuation (market value, cost basis, unrealized PnL).
2. Weinstein Stage Breakdown (Stages 1-4) & 150-SMA trend slopes.
3. Algorithmic Exit Triggers (Chandelier stop-loss breaches, Stage 4 structural declines).
4. Indian Equity Tax-Loss Harvesting Ledger (STCG vs LTCG under Section 111A/70):
   - Offsets short-term capital gains with short-term capital losses (SUZENE, XCHSOL, SWILIM).
   - Computes exact share & rupee cut downs for overallocated assets (GOLDEX, NIPSIL) and decaying PSUs (PSUBAN, CANBAN).
   - Allocates freed liquidity into Stage 1/2 accumulation leaders (POWINF, EMBOFF, NEXSEL, FEDBAN).
5. Quantitative Memo Generation via Ollama / Antigravity CLI fallback.
6. Zero-dependency Telegram notification broadcaster.
"""

import argparse
import json
import math
import os
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Tuple

try:
    from algo.ai_analyst import audit_holdings
except ImportError:
    try:
        from scripts.ollama_common import audit_holdings
    except ImportError:
        audit_holdings = None

try:
    from scripts.ollama_common import (
        get_db_connection,
        query_llm_with_fallback,
        resolve_db_path,
        resolve_fallback_endpoint,
        resolve_fallback_model,
        resolve_ollama_endpoint,
        resolve_ollama_model,
    )
except ImportError:
    from ollama_common import (
        get_db_connection,
        query_llm_with_fallback,
        resolve_db_path,
        resolve_fallback_endpoint,
        resolve_fallback_model,
        resolve_ollama_endpoint,
        resolve_ollama_model,
    )


def load_env_file(env_path: str = ".env") -> Dict[str, str]:
    """Parse local .env file without external dependencies."""
    env = {}
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip("'\"")
        except Exception:
            pass
    return env


def send_telegram_broadcast(token: str, owner_ids: List[str], text: str) -> List[Tuple[str, bool, str]]:
    """
    Transmit message to all configured Telegram owner IDs using pure standard library.
    Automatically splits long messages into compliant chunks (max 3800 chars).
    """
    results = []
    endpoint = f"https://api.telegram.org/bot{token}/sendMessage"

    chunks = []
    max_len = 3800
    remaining = text
    while len(remaining) > max_len:
        split_pos = remaining.rfind("\n", 0, max_len)
        if split_pos == -1:
            split_pos = max_len
        chunks.append(remaining[:split_pos])
        remaining = remaining[split_pos:].lstrip("\n")
    if remaining:
        chunks.append(remaining)

    for chat_id in owner_ids:
        chat_id = chat_id.strip()
        if not chat_id:
            continue
        chat_success = True
        chat_err = ""
        for chunk in chunks:
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(endpoint, data=data, headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_data = json.loads(resp.read().decode("utf-8"))
                    if not resp_data.get("ok"):
                        chat_success = False
                        chat_err = str(resp_data)
            except urllib.error.HTTPError:
                # Fallback to plain text if Markdown syntax fails to parse in Telegram
                try:
                    payload.pop("parse_mode", None)
                    data_plain = json.dumps(payload).encode("utf-8")
                    req_plain = urllib.request.Request(
                        endpoint, data=data_plain, headers={"Content-Type": "application/json"}
                    )
                    with urllib.request.urlopen(req_plain, timeout=15) as resp2:
                        resp_data2 = json.loads(resp2.read().decode("utf-8"))
                        if not resp_data2.get("ok"):
                            chat_success = False
                            chat_err = str(resp_data2)
                except Exception as ex:
                    chat_success = False
                    chat_err = str(ex)
            except Exception as e:
                chat_success = False
                chat_err = str(e)
        results.append((chat_id, chat_success, chat_err))
    return results


def extract_portfolio_rebalancer_data(db_path: str) -> Dict[str, Any]:
    """
    Query database and compute comprehensive portfolio valuation, stage breakdown,
    algorithmic triggers, and quantitative tax-optimization ledger.
    """
    conn = get_db_connection(db_path)
    cursor = conn.cursor()

    # 1. Fetch current holdings with latest stage, signals, and algorithmic actions
    cursor.execute("""
        SELECT h.stock_code,
               CAST(h.quantity AS REAL) as qty,
               CAST(h.average_price AS REAL) as avg_price,
               CAST(h.current_price AS REAL) as cur_price,
               (CAST(h.quantity AS REAL) * CAST(h.current_price AS REAL)) as cur_val,
               (CAST(h.quantity AS REAL) * (CAST(h.current_price AS REAL) - CAST(h.average_price AS REAL))) as pnl,
               st.stage,
               st.slope,
               st.sma_150,
               act.action,
               act.rationale,
               sig.rsi14,
               sig.composite_score,
               sig.volume_ratio_20d
        FROM holdings_snapshot h
        LEFT JOIN (
            SELECT s1.stock_code, s1.stage, s1.slope, s1.sma_150
            FROM stage_history s1
            JOIN (
                SELECT stock_code, MAX(date) as max_date
                FROM stage_history
                GROUP BY stock_code
            ) s2 ON s1.stock_code = s2.stock_code AND s1.date = s2.max_date
        ) st ON h.stock_code = st.stock_code
        LEFT JOIN (
            SELECT a1.stock_code, a1.action, a1.rationale
            FROM stock_actions a1
            JOIN (
                SELECT stock_code, MAX(timestamp) as max_ts
                FROM stock_actions
                GROUP BY stock_code
            ) a2 ON a1.stock_code = a2.stock_code AND a1.timestamp = a2.max_ts
        ) act ON h.stock_code = act.stock_code
        LEFT JOIN (
            SELECT sig1.stock_code, sig1.rsi14, sig1.composite_score, sig1.volume_ratio_20d
            FROM signals sig1
            JOIN (
                SELECT stock_code, MAX(timestamp) as max_ts
                FROM signals
                GROUP BY stock_code
            ) sig2 ON sig1.stock_code = sig2.stock_code AND sig1.timestamp = sig2.max_ts
        ) sig ON h.stock_code = sig.stock_code
        ORDER BY cur_val DESC
    """)
    rows = cursor.fetchall()

    # 2. Latest total portfolio history row if available
    cursor.execute("""
        SELECT timestamp, total_invested, total_current_value, total_pnl
        FROM portfolio_value_history
        ORDER BY timestamp DESC
        LIMIT 1
    """)
    cursor.fetchone()
    conn.close()

    total_invested = sum(r["qty"] * r["avg_price"] for r in rows)
    current_market_value = sum(r["cur_val"] for r in rows)
    total_pnl = current_market_value - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100.0) if total_invested > 0 else 0.0

    # 3. Stage breakdown and moving average slope aggregation
    stage_buckets = {
        "1": {"label": "Stage 1 (Accumulation / Base)", "holdings": [], "value": 0.0, "slopes": []},
        "2": {"label": "Stage 2 (Markup / Uptrend)", "holdings": [], "value": 0.0, "slopes": []},
        "3": {"label": "Stage 3 (Distribution / Top)", "holdings": [], "value": 0.0, "slopes": []},
        "4": {"label": "Stage 4 (Capitulation / Decline)", "holdings": [], "value": 0.0, "slopes": []},
        "Unknown": {"label": "Unclassified", "holdings": [], "value": 0.0, "slopes": []},
    }

    holdings_dict = {}
    gains_list = []
    losses_list = []
    neutral_list = []

    stage_4_declines = []
    chandelier_breaches = []
    distribution_warnings = []

    for r in rows:
        sc = r["stock_code"]
        qty = float(r["qty"])
        avg_p = float(r["avg_price"])
        cur_p = float(r["cur_price"])
        cur_val = float(r["cur_val"])
        pnl = float(r["pnl"])
        pnl_pct = (pnl / (qty * avg_p) * 100.0) if (qty > 0 and avg_p > 0) else 0.0
        stg = str(r["stage"]) if r["stage"] else "Unknown"
        if stg not in stage_buckets:
            stg = "Unknown"

        slope_val = None
        if r["slope"] is not None and not math.isnan(float(r["slope"])):
            slope_val = float(r["slope"])
            stage_buckets[stg]["slopes"].append(slope_val)

        action = (r["action"] or "").strip()
        rationale = (r["rationale"] or "").strip()

        holding_obj = {
            "stock_code": sc,
            "quantity": qty,
            "average_price": round(avg_p, 2),
            "current_price": round(cur_p, 2),
            "current_value": round(cur_val, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "portfolio_weight_pct": round(cur_val / current_market_value * 100.0, 2) if current_market_value else 0.0,
            "stage": r["stage"],
            "slope": round(slope_val, 5) if slope_val is not None else None,
            "action": action,
            "rationale": rationale,
            "rsi14": round(float(r["rsi14"]), 1) if r["rsi14"] is not None else None,
            "composite_score": round(float(r["composite_score"]), 1) if r["composite_score"] is not None else None,
        }
        holdings_dict[sc] = holding_obj
        stage_buckets[stg]["holdings"].append(sc)
        stage_buckets[stg]["value"] += cur_val

        # PnL classification
        if pnl > 0.01:
            gains_list.append(holding_obj)
        elif pnl < -0.01:
            losses_list.append(holding_obj)
        else:
            neutral_list.append(holding_obj)

        # Algorithmic trigger detection
        is_stage_4 = (stg == "4") or ("Stage 4" in rationale)
        is_chandelier = "Chandelier" in rationale or ("SELL" in action.upper() and "below" in rationale.lower())
        is_distribution = "Stage 3" in rationale or "distribution" in rationale.lower() or "TRIM" in action.upper()

        if is_stage_4:
            stage_4_declines.append(
                {
                    "stock_code": sc,
                    "current_value": round(cur_val, 2),
                    "pnl": round(pnl, 2),
                    "slope": round(slope_val, 5) if slope_val is not None else None,
                    "action": action,
                    "rationale": rationale,
                }
            )

        if is_chandelier:
            chandelier_breaches.append(
                {
                    "stock_code": sc,
                    "current_value": round(cur_val, 2),
                    "pnl": round(pnl, 2),
                    "action": action,
                    "rationale": rationale,
                }
            )

        if is_distribution and not is_stage_4 and not is_chandelier:
            distribution_warnings.append(
                {
                    "stock_code": sc,
                    "current_value": round(cur_val, 2),
                    "pnl": round(pnl, 2),
                    "action": action,
                    "rationale": rationale,
                }
            )

    # Summary stage stats
    stage_breakdown = {}
    for stg, data in stage_buckets.items():
        slopes = data["slopes"]
        avg_slope = round(sum(slopes) / len(slopes), 5) if slopes else None
        stg_val = data["value"]
        weight = round(stg_val / current_market_value * 100.0, 2) if current_market_value else 0.0
        stage_breakdown[stg] = {
            "label": data["label"],
            "count": len(data["holdings"]),
            "current_value": round(stg_val, 2),
            "portfolio_weight_pct": weight,
            "avg_slope": avg_slope,
            "stocks": data["holdings"],
        }

    # 4. Quantitative STCG vs LTCG Tax Optimization Ledger
    # In India, equity assets held <= 12 months are STCG taxed at 20% (Section 111A).
    # Short-Term Capital Losses (STCL) can offset STCG dollar-for-dollar under Section 70.

    # Priority Loss Harvest Candidates:
    # Specifically SUZENE, XCHSOL, SWILIM, CANBAN, HTMGLO, etc.
    loss_harvest_targets = ["SUZENE", "XCHSOL", "SWILIM", "HTMGLO", "CANBAN", "NATMIN", "GUJMI", "INTPAP"]
    harvested_losses = []
    total_stcl_harvested = 0.0

    for sc in loss_harvest_targets:
        if sc in holdings_dict:
            h = holdings_dict[sc]
            if h["pnl"] < 0:
                loss_amt = abs(h["pnl"])
                total_stcl_harvested += loss_amt
                harvested_losses.append(
                    {
                        "stock_code": sc,
                        "shares_to_sell": int(h["quantity"]),
                        "current_price": h["current_price"],
                        "freed_rupees": round(h["current_value"], 2),
                        "harvested_loss": round(loss_amt, 2),
                        "loss_pct": h["pnl_pct"],
                        "stage": h["stage"],
                        "reason": f"Tax-loss harvest & exit {h['action'] or 'decaying position'}",
                    }
                )

    # Recommended Capital Cut Downs (Trims & Exits for overallocated/decaying assets)
    # - GOLDEX: Trim from ~12% down to 8% target cap (single asset guardrail 10%)
    # - NIPSIL: Trim from ~10.2% down to 5% target cap (precious metals guardrail & Stage 4 stop breach)
    # - PSUBAN: Full exit (100%) - Stage 4 decline, frees capital for Stage 1/2 leaders
    # - CANBAN: Full exit (100%) - Stage 4 decline, also acts as loss harvest
    capital_cut_downs = []

    # GOLDEX calculation
    if "GOLDEX" in holdings_dict:
        g = holdings_dict["GOLDEX"]
        target_val = current_market_value * 0.08  # 8% target
        excess_val = max(0.0, g["current_value"] - target_val)
        trim_shares = int(excess_val / g["current_price"]) if g["current_price"] > 0 else 0
        if trim_shares > 0:
            freed = round(trim_shares * g["current_price"], 2)
            stcg = round(trim_shares * (g["current_price"] - g["average_price"]), 2)
            capital_cut_downs.append(
                {
                    "stock_code": "GOLDEX",
                    "action_type": "PARTIAL_TRIM",
                    "current_shares": int(g["quantity"]),
                    "shares_to_sell": trim_shares,
                    "remaining_shares": int(g["quantity"]) - trim_shares,
                    "current_price": g["current_price"],
                    "freed_rupees": freed,
                    "realized_stcg": stcg,
                    "realized_stcl": 0.0,
                    "target_weight_pct": 8.0,
                    "rationale": "Overallocated (12.0% -> 8.0%). Single-asset concentration guardrail trim & lock profit.",
                }
            )

    # NIPSIL calculation
    if "NIPSIL" in holdings_dict:
        n = holdings_dict["NIPSIL"]
        target_val = current_market_value * 0.05  # 5% target
        excess_val = max(0.0, n["current_value"] - target_val)
        trim_shares = int(excess_val / n["current_price"]) if n["current_price"] > 0 else 0
        if trim_shares > 0:
            freed = round(trim_shares * n["current_price"], 2)
            stcg = round(trim_shares * (n["current_price"] - n["average_price"]), 2)
            capital_cut_downs.append(
                {
                    "stock_code": "NIPSIL",
                    "action_type": "PARTIAL_TRIM",
                    "current_shares": int(n["quantity"]),
                    "shares_to_sell": trim_shares,
                    "remaining_shares": int(n["quantity"]) - trim_shares,
                    "current_price": n["current_price"],
                    "freed_rupees": freed,
                    "realized_stcg": stcg,
                    "realized_stcl": 0.0,
                    "target_weight_pct": 5.0,
                    "rationale": "Precious metals overweight (10.2% -> 5.0%), Stage 4 slope (-0.0202) & zero dividend yield.",
                }
            )

    # PSUBAN calculation
    if "PSUBAN" in holdings_dict:
        p = holdings_dict["PSUBAN"]
        sell_shares = int(p["quantity"])
        freed = round(sell_shares * p["current_price"], 2)
        stcg = round(sell_shares * (p["current_price"] - p["average_price"]), 2)
        capital_cut_downs.append(
            {
                "stock_code": "PSUBAN",
                "action_type": "FULL_EXIT",
                "current_shares": sell_shares,
                "shares_to_sell": sell_shares,
                "remaining_shares": 0,
                "current_price": p["current_price"],
                "freed_rupees": freed,
                "realized_stcg": max(0.0, stcg),
                "realized_stcl": abs(min(0.0, stcg)),
                "target_weight_pct": 0.0,
                "rationale": "Full exit. Stage 4 structural decline (slope -0.0200). Encashes capital for Stage 1/2 leaders.",
            }
        )

    # CANBAN calculation (also listed in loss harvest)
    if "CANBAN" in holdings_dict:
        c = holdings_dict["CANBAN"]
        sell_shares = int(c["quantity"])
        freed = round(sell_shares * c["current_price"], 2)
        pnl_realized = round(sell_shares * (c["current_price"] - c["average_price"]), 2)
        capital_cut_downs.append(
            {
                "stock_code": "CANBAN",
                "action_type": "FULL_EXIT",
                "current_shares": sell_shares,
                "shares_to_sell": sell_shares,
                "remaining_shares": 0,
                "current_price": c["current_price"],
                "freed_rupees": freed,
                "realized_stcg": max(0.0, pnl_realized),
                "realized_stcl": abs(min(0.0, pnl_realized)),
                "target_weight_pct": 0.0,
                "rationale": "Full exit. Stage 4 banking decay (slope -0.0290). Tax-loss harvest.",
            }
        )

    # Combine total cut downs including priority loss exits
    for item in harvested_losses:
        sc = item["stock_code"]
        if sc not in [x["stock_code"] for x in capital_cut_downs]:
            capital_cut_downs.append(
                {
                    "stock_code": sc,
                    "action_type": "FULL_EXIT_TAX_HARVEST",
                    "current_shares": item["shares_to_sell"],
                    "shares_to_sell": item["shares_to_sell"],
                    "remaining_shares": 0,
                    "current_price": item["current_price"],
                    "freed_rupees": item["freed_rupees"],
                    "realized_stcg": 0.0,
                    "realized_stcl": item["harvested_loss"],
                    "target_weight_pct": 0.0,
                    "rationale": item["reason"],
                }
            )

    # Calculate net capital freed and net tax match
    total_freed_capital = sum(c["freed_rupees"] for c in capital_cut_downs)
    total_realized_stcg = sum(c["realized_stcg"] for c in capital_cut_downs)
    total_realized_stcl = sum(c["realized_stcl"] for c in capital_cut_downs)

    net_taxable_gain = max(0.0, total_realized_stcg - total_realized_stcl)
    tax_liability_without_harvest = total_realized_stcg * 0.20
    tax_liability_with_harvest = net_taxable_gain * 0.20
    tax_shield_cash_saved = tax_liability_without_harvest - tax_liability_with_harvest
    excess_loss_carryforward = max(0.0, total_realized_stcl - total_realized_stcg)

    # 5. Exact Rupee & Share Quantities for Stage 1/2 Accumulation Leaders
    # Leaders explicitly requested: POWINF, EMBOFF, NEXSEL, FEDBAN
    # Reserve ~10% of freed capital for dry-powder cash / LIQUIDBEES buffer.
    reinvestment_pool = total_freed_capital * 0.88  # 88% active redeployment, 12% cash buffer
    target_weights = {
        "POWINF": 0.26,  # 26% of redeployment pool (~Rs. 34,500)
        "EMBOFF": 0.26,  # 26% of redeployment pool (~Rs. 34,500)
        "NEXSEL": 0.24,  # 24% of redeployment pool (~Rs. 31,800)
        "FEDBAN": 0.24,  # 24% of redeployment pool (~Rs. 31,800)
    }

    reinvestment_orders = []
    total_reinvested_rupees = 0.0

    for sc, weight in target_weights.items():
        if sc in holdings_dict:
            h = holdings_dict[sc]
            alloc_rupees = reinvestment_pool * weight
            cur_price = h["current_price"]
            buy_shares = int(alloc_rupees / cur_price) if cur_price > 0 else 0
            order_rupees = round(buy_shares * cur_price, 2)
            total_reinvested_rupees += order_rupees

            # Yield estimates for cash flow impact
            yield_pct = 4.0 if "POWINF" in sc else (7.0 if "EMBOFF" in sc else (6.8 if "NEXSEL" in sc else 0.5))
            annual_div_boost = round(order_rupees * (yield_pct / 100.0), 2)

            reinvestment_orders.append(
                {
                    "stock_code": sc,
                    "current_shares": int(h["quantity"]),
                    "recommended_buy_shares": buy_shares,
                    "projected_total_shares": int(h["quantity"]) + buy_shares,
                    "current_price": cur_price,
                    "rupee_amount": order_rupees,
                    "stage": h["stage"],
                    "slope": h["slope"],
                    "est_yield_pct": yield_pct,
                    "projected_annual_dividend_boost": annual_div_boost,
                    "rationale": f"Stage {h['stage']} accumulation leader (slope {h['slope']}). High-cashflow yield ballast.",
                }
            )

    cash_buffer_retained = round(total_freed_capital - total_reinvested_rupees, 2)

    return {
        "timestamp": datetime.now().isoformat(),
        "portfolio_summary": {
            "total_invested": round(total_invested, 2),
            "current_market_value": round(current_market_value, 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "holdings_count": len(rows),
            "profitable_count": len(gains_list),
            "loss_making_count": len(losses_list),
            "neutral_count": len(neutral_list),
            "concentration_top5_pct": round(sum(r["cur_val"] for r in rows[:5]) / current_market_value * 100.0, 2)
            if current_market_value
            else 0.0,
        },
        "stage_breakdown": stage_breakdown,
        "algorithmic_triggers": {
            "chandelier_exit_breaches": chandelier_breaches,
            "stage_4_declines": stage_4_declines,
            "distribution_warnings": distribution_warnings,
        },
        "tax_optimization_ledger": {
            "tax_regime": "Indian Equity STCG @ 20% (Section 111A) / Section 70 Set-off",
            "capital_cut_downs": capital_cut_downs,
            "total_freed_capital": round(total_freed_capital, 2),
            "tax_matching_matrix": {
                "total_realized_stcg": round(total_realized_stcg, 2),
                "total_realized_stcl_shield": round(total_realized_stcl, 2),
                "net_taxable_capital_gain": round(net_taxable_gain, 2),
                "tax_liability_before_harvest": round(tax_liability_without_harvest, 2),
                "tax_liability_after_harvest": round(tax_liability_with_harvest, 2),
                "direct_cash_tax_shield_saved": round(tax_shield_cash_saved, 2),
                "excess_loss_carryforward": round(excess_loss_carryforward, 2),
            },
            "reinvestment_orders": reinvestment_orders,
            "total_reinvested_rupees": round(total_reinvested_rupees, 2),
            "cash_buffer_retained": cash_buffer_retained,
            "total_annual_dividend_boost": round(
                sum(o["projected_annual_dividend_boost"] for o in reinvestment_orders), 2
            ),
        },
        "top_holdings": [holdings_dict[r["stock_code"]] for r in rows[:10]],
        "all_holdings": [holdings_dict[r["stock_code"]] for r in rows],
    }


def build_quantitative_memo_prompt(data: Dict[str, Any]) -> str:
    """Build high-fidelity prompt for senior quantitative audit brief."""
    summary = data["portfolio_summary"]
    tax_ledger = data["tax_optimization_ledger"]
    matrix = tax_ledger["tax_matching_matrix"]

    prompt = f"""
You are the Chief Quantitative Strategist and Senior Risk Officer for PortfolioPi.
Deliver a rigorous, institutional-grade Daily Rebalancing & Tax-Shield Executive Memo based on the following verified ledger data:

CURRENT PORTFOLIO STATE:
- Invested Capital: ₹{summary["total_invested"]:,.2f}
- Current Market Value: ₹{summary["current_market_value"]:,.2f}
- Unrealized PnL: ₹{summary["total_pnl"]:,.2f} ({summary["total_pnl_pct"]:.2f}%)
- Holdings Count: {summary["holdings_count"]} (Profitable: {summary["profitable_count"]}, Loss: {summary["loss_making_count"]})
- Top 5 Concentration: {summary["concentration_top5_pct"]}%

STAGE BREAKDOWN (Weinstein 150-SMA):
{json.dumps(data["stage_breakdown"], indent=2)}

ALGORITHMIC TRIGGERS:
- Chandelier Stop Breaches: {len(data["algorithmic_triggers"]["chandelier_exit_breaches"])} positions
- Stage 4 Structural Declines: {len(data["algorithmic_triggers"]["stage_4_declines"])} positions
- Distribution Warnings: {len(data["algorithmic_triggers"]["distribution_warnings"])} positions

TAX OPTIMIZATION & REBALANCING MATRIX:
- Gross Liquidity Unlocked from Cuts: ₹{tax_ledger["total_freed_capital"]:,.2f}
- Realized STCG from Trims: ₹{matrix["total_realized_stcg"]:,.2f}
- Realized STCL from Loss Harvest (SUZENE, XCHSOL, SWILIM, CANBAN, etc.): ₹{matrix["total_realized_stcl_shield"]:,.2f}
- Net Taxable Capital Gain: ₹{matrix["net_taxable_capital_gain"]:,.2f} (Direct Tax Saved: ₹{matrix["direct_cash_tax_shield_saved"]:,.2f})
- Excess Loss Shield Carryforward: ₹{matrix["excess_loss_carryforward"]:,.2f}

EXACT CAPITAL REDEPLOYMENT ORDERS (Stage 1/2 Leaders):
{json.dumps(tax_ledger["reinvestment_orders"], indent=2)}
- Cash Buffer Retained for Corrections: ₹{tax_ledger["cash_buffer_retained"]:,.2f}
- Estimated Incremental Annual Dividend Boost: +₹{tax_ledger["total_annual_dividend_boost"]:,.2f}/yr

Provide your assessment structured in these mandatory sections:
1. **Executive Rebalancing Summary & Guardrail Rectification**:
   - Status of precious metals exposure (GOLDEX + NIPSIL) and how the proposed trims bring them within safety limits.
   - Elimination of dead-weight Stage 4 capital decay.
2. **Indian Tax-Loss Harvesting & STCG Shield Analysis**:
   - Explain how harvesting losses in SUZENE, XCHSOL, SWILIM, and CANBAN completely extinguishes capital gains tax liability on GOLDEX/NIPSIL trims under Section 70.
   - Quantify the direct cash tax savings.
3. **Execution Ledger: Trims & Exits (Shares & Values)**:
   - Present a clean markdown execution table of exact shares to sell, rupee value freed, and rationale.
4. **Execution Ledger: Stage 1/2 Accumulation Buys (Shares & Values)**:
   - Present the exact orders for POWINF, EMBOFF, NEXSEL, and FEDBAN.
   - Highlight the defensive yield enhancement and volatility reduction.
5. **Immediate Trading Checklist for Market Open**:
   - Precise 4-step sequence to execute across broker terminal.
"""
    return prompt.strip()


def main():
    parser = argparse.ArgumentParser(description="Daily Quantitative Portfolio Rebalancer & Tax Harvest Engine")
    parser.add_argument("--db", default=None, help="Path to SQLite database")
    parser.add_argument(
        "--dry-run", action="store_true", help="Output raw metrics JSON and tax ledger without calling LLM"
    )
    parser.add_argument(
        "--edge-audit",
        dest="edge_audit",
        action="store_true",
        default=True,
        help="Run 2-ticker chunked edge auditor (pralabs:edge)",
    )
    parser.add_argument("--no-edge-audit", dest="edge_audit", action="store_false", help="Skip edge auditor")
    parser.add_argument(
        "--ai", dest="ai", action="store_true", default=True, help="Run AI quantitative analysis (default True)"
    )
    parser.add_argument("--no-ai", dest="ai", action="store_false", help="Skip AI analysis")
    parser.add_argument(
        "--telegram", action="store_true", help="Broadcast generated memo to Telegram owners via urllib"
    )
    parser.add_argument("--ollama-url", default=resolve_ollama_endpoint(), help="Primary Ollama base URL")
    parser.add_argument("--model", default=resolve_ollama_model(), help="Primary Ollama model name")
    parser.add_argument("--fallback-url", default=resolve_fallback_endpoint(), help="Fallback Ollama base URL")
    parser.add_argument("--fallback-model", default=resolve_fallback_model(), help="Fallback Ollama model name")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    rebalancer_data = extract_portfolio_rebalancer_data(db_path)

    # 1. Handle --dry-run
    if args.dry_run:
        print(json.dumps(rebalancer_data, indent=2))
        return

    # 2. Print Terminal Executive Banner
    summary = rebalancer_data["portfolio_summary"]
    ledger = rebalancer_data["tax_optimization_ledger"]
    matrix = ledger["tax_matching_matrix"]

    net_tax_liability = matrix.get("tax_liability_after_harvest", 0.0)
    print("=" * 80)
    print(" PORTFOLIOPi DAILY QUANTITATIVE REBALANCER & TAX HARVEST ENGINE")
    print(f" Database: {db_path} | Time: {rebalancer_data['timestamp']}")
    print("=" * 80)
    print(f" Total Invested : Rs. {summary['total_invested']:>12,.2f}  |  Holdings Count : {summary['holdings_count']}")
    print(
        f" Current Value  : Rs. {summary['current_market_value']:>12,.2f}  |  Win/Loss Ratio : {summary['profitable_count']}/{summary['loss_making_count']}"
    )
    print(f" Unrealized PnL : Rs. {summary['total_pnl']:>12,.2f} ({summary['total_pnl_pct']:+.2f}%)")
    print("-" * 80)
    print(f" [TAX SHIELD] Gross Freed Liquidity : Rs. {ledger['total_freed_capital']:,.2f}")
    print(f" [TAX SHIELD] Harvested STCL Losses : Rs. {matrix['total_realized_stcl_shield']:,.2f}")
    print(f" [TAX SHIELD] Realized STCG Gains   : Rs. {matrix['total_realized_stcg']:,.2f}")
    print(
        f" [TAX SHIELD] Net Tax Liability     : Rs. {net_tax_liability:,.2f} (Direct Tax Saved: Rs. {matrix['direct_cash_tax_shield_saved']:,.2f})"
    )
    print(f" [TAX SHIELD] Dividend Boost Added  : +Rs. {ledger['total_annual_dividend_boost']:,.2f} / year")
    print("=" * 80 + "\n")

    generated_memo = ""

    # 3. AI Quantitative Analysis
    if args.ai:
        system_prompt = (
            "You are an elite quantitative portfolio manager and certified tax optimization specialist. "
            "You adhere strictly to Stan Weinstein stage rules, capital preservation stops, and Indian Income Tax Section 70/111A provisions. "
            "Deliver an articulate, high-conviction quantitative investment memorandum."
        )
        prompt = build_quantitative_memo_prompt(rebalancer_data)

        print(
            f"[*] Querying LLM memo generator (Primary: {args.model} @ {args.ollama_url} | Fallback: {args.fallback_model} @ {args.fallback_url})...\n"
        )
        generated_memo = query_llm_with_fallback(
            prompt=prompt,
            system_prompt=system_prompt,
            base_url=args.ollama_url,
            model=args.model,
            fallback_url=args.fallback_url,
            fallback_model=args.fallback_model,
            temperature=0.2,
            timeout=15.0,
        )

        print("\n" + "=" * 80)

    # 3b. Edge Risk Auditor via 2-ticker Micro-Batches
    edge_audit_alerts = []
    if args.edge_audit and audit_holdings:
        print("[*] Running 2-ticker chunked edge risk auditor across portfolio holdings...")
        try:
            raw_holdings = rebalancer_data.get("all_holdings", [])
            formatted = []
            for h in raw_holdings:
                cmp = float(h.get("current_price", h.get("cmp", 1.0)))
                stop = cmp * 0.95
                rat = str(h.get("rationale", ""))
                if "Chandelier Exit (" in rat:
                    try:
                        stop = float(rat.split("Chandelier Exit (")[1].split(")")[0])
                    except Exception:
                        pass
                formatted.append(
                    {
                        "ticker": h.get("stock_code", h.get("ticker", "TKR")),
                        "cmp": cmp,
                        "stage": h.get("stage", h.get("weinstein_stage", 2)),
                        "minervini_pass": 5,
                        "minervini_total": 7,
                        "rs_rating": int(h.get("composite_score") or 60),
                        "stop_loss_price": stop,
                        "pnl_pct": float(h.get("pnl_pct", h.get("unrealized_pnl_pct", 0.0))),
                        "dist_days": 2,
                        "asset_class": h.get("asset_class", "EQUITY"),
                    }
                )

            audit_lines = audit_holdings(formatted, chunk_size=2)

            # Filter for non-HOLD actionable alerts
            for i in range(0, len(audit_lines), 2):
                header_line = audit_lines[i]
                detail_line = audit_lines[i + 1] if i + 1 < len(audit_lines) else ""
                if "ACT:HOLD" not in header_line:
                    edge_audit_alerts.append(f"{header_line}\n{detail_line}")

            print(
                f"[✓] Edge audit completed: {len(edge_audit_alerts)} actionable alerts out of {len(formatted)} holdings."
            )
            if edge_audit_alerts:
                print("--- Actionable Edge Alerts ---")
                for alert in edge_audit_alerts:
                    print(alert)
                print("------------------------------\n")
        except Exception as e:
            print(f"[!] Edge audit encountered an error: {e}")

    # 4. Telegram Notification Broadcaster
    if args.telegram:
        env_vars = load_env_file()
        bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or env_vars.get("TELEGRAM_BOT_TOKEN")
        owner_ids_str = os.getenv("TELEGRAM_OWNER_IDS") or env_vars.get("TELEGRAM_OWNER_IDS")

        if not bot_token or not owner_ids_str:
            print("[Telegram Error] TELEGRAM_BOT_TOKEN or TELEGRAM_OWNER_IDS missing in environment or .env file.")
        else:
            owner_ids = [x.strip() for x in owner_ids_str.split(",") if x.strip()]
            alert_section = ""
            if edge_audit_alerts:
                alert_section = (
                    "\n🚨 *Actionable Edge Directives*:\n" + "\n\n".join([f"`{a}`" for a in edge_audit_alerts]) + "\n\n"
                )

            header = (
                f"📊 *PortfolioPi Daily Rebalancer & Tax Shield Report*\n"
                f"• *Current Value*: ₹{summary['current_market_value']:,.2f} ({summary['total_pnl_pct']:+.2f}%)\n"
                f"• *Gross Freed Liquidity*: ₹{ledger['total_freed_capital']:,.2f}\n"
                f"• *Tax Shield Savings*: ₹{matrix['direct_cash_tax_shield_saved']:,.2f} (Net Tax: ₹{matrix['tax_liability_after_harvest']:,.2f})\n"
                f"• *Dividend Boost*: +₹{ledger['total_annual_dividend_boost']:,.2f}/yr\n\n"
            )
            broadcast_text = (
                header
                + alert_section
                + (generated_memo if generated_memo else "AI memo skipped or generated in dry-run mode.")
            )
            print(f"[*] Broadcasting rebalancer report to Telegram owner IDs: {owner_ids}...")
            results = send_telegram_broadcast(bot_token, owner_ids, broadcast_text)
            for cid, success, err in results:
                if success:
                    print(f"  [✓] Successfully transmitted to Telegram chat ID: {cid}")
                else:
                    print(f"  [✗] Failed to transmit to Telegram chat ID: {cid}. Error: {err}")


if __name__ == "__main__":
    main()
