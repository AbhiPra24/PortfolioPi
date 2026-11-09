"""FIFO Tax-Lot Matching Engine and Asset Classification & Tax Hurdle Rules.

Provides:
1. `compute_fifo_tax_lots(trades: list[dict], current_prices: dict[str, float] = None) -> list[dict]`:
   Pure Python FIFO lot-matching for buy/sell transactions.
2. `compute_tax_conversion_hurdle(unrealized_gain: float, current_market_value: float, ytd_realized_ltcg: float = 0.0) -> float`:
   Calculates Tax-Conversion Hurdle Score.
3. `evaluate_tax_hurdle_rule(...) -> dict`:
   Two-Tier Tax Hurdle Rule Matrix implementation.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from algo.tax_pairing import (
    HarvestPairingItem,
    HarvestPairingResult,
    Section112AHarvestItem,
    Section112AHarvestResult,
    pair_tax_loss_harvest,
    plan_section_112a_harvest_reset,
)

__all__ = [
    "HarvestPairingItem",
    "HarvestPairingResult",
    "Section112AHarvestItem",
    "Section112AHarvestResult",
    "pair_tax_loss_harvest",
    "plan_section_112a_harvest_reset",
    "compute_fifo_tax_lots",
    "compute_tax_conversion_hurdle",
    "evaluate_tax_hurdle_rule",
    "LTCG_RATE",
    "STCG_RATE",
    "LTCG_ANNUAL_EXEMPTION_LIMIT",
]

LTCG_RATE = 0.125  # 12.5% LTCG on equity
STCG_RATE = 0.20  # 20.0% STCG on equity
LTCG_ANNUAL_EXEMPTION_LIMIT = 125000.0  # ₹1.25 Lakh annual exemption under Section 112A


def _parse_date(d_val: Any) -> Optional[date]:
    """Parse various date formats into date object."""
    if not d_val:
        return None
    if isinstance(d_val, datetime):
        return d_val.date()
    if isinstance(d_val, date):
        return d_val

    s = str(d_val).strip()
    if not s:
        return None

    # Common formats: '2025-03-01', '2025-03-01T10:00:00.000Z', '01-MAR-2025', '01/03/2025'
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.split("T")[0] if "T" in s and fmt == "%Y-%m-%d" else s, fmt).date()
        except Exception:
            continue
    # Fallback to prefix YYYY-MM-DD
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.strptime(s[:10], "%Y-%m-%d").date()
        except Exception:
            pass
    return None


def compute_fifo_tax_lots(
    trades: List[Dict[str, Any]],
    current_prices: Optional[Dict[str, float]] = None,
    as_of_date: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Compute FIFO tax lots by matching Buy and Sell transactions.

    Each remaining unclosed Buy lot contains:
    - lot_id: str/int
    - stock_code: str
    - buy_date: str (YYYY-MM-DD)
    - quantity: int
    - buy_price: float
    - holding_days: int
    - tax_category: 'STCG' (if holding_days <= 365) else 'LTCG'
    - days_to_ltcg: max(0, 365 - holding_days)
    - unrealized_gain_per_share: float
    - price_cushion_required: float (cushion % needed to break even after tax difference)
    - current_price: float
    - current_value: float
    - total_cost: float
    - unrealized_gain: float
    """
    if current_prices is None:
        current_prices = {}
    if as_of_date is None:
        as_of_date = date.today()

    # Sort trades chronologically
    sorted_trades = []
    for idx, t in enumerate(trades):
        t_date = _parse_date(t.get("trade_date") or t.get("execution_date") or t.get("order_datetime"))
        sorted_trades.append((t_date or date.min, idx, t))
    sorted_trades.sort(key=lambda x: (x[0], x[1]))

    # Group trades by stock_code
    stock_lots: Dict[str, List[Dict[str, Any]]] = {}

    for _, _, t in sorted_trades:
        stock_code = str(t.get("stock_code") or "").strip().upper()
        if not stock_code:
            continue

        action = str(t.get("action") or t.get("order_action") or "").strip().title()
        qty = int(t.get("quantity") or t.get("qty") or t.get("match_qty") or 0)
        if qty <= 0:
            continue

        raw_cost = float(t.get("average_cost") or 0.0)
        price = float(t.get("price") or t.get("trade_price") or t.get("ltp") or 0.0)
        if price <= 0:
            price = (raw_cost / qty) if (raw_cost > 0 and qty > 0) else 0.0

        trade_date = (
            _parse_date(t.get("trade_date") or t.get("execution_date") or t.get("order_datetime")) or as_of_date
        )
        trade_id = str(t.get("trade_id") or t.get("order_id") or f"lot_{len(stock_lots.get(stock_code, [])) + 1}")

        if action == "Buy":
            stock_lots.setdefault(stock_code, []).append(
                {
                    "lot_id": trade_id,
                    "stock_code": stock_code,
                    "buy_date": trade_date,
                    "quantity": qty,
                    "buy_price": price,
                }
            )
        elif action == "Sell":
            # Match FIFO against existing buy lots
            sell_rem = qty
            open_lots = stock_lots.get(stock_code, [])
            for lot in open_lots:
                if lot["quantity"] <= 0:
                    continue
                matched = min(sell_rem, lot["quantity"])
                lot["quantity"] -= matched
                sell_rem -= matched
                if sell_rem <= 0:
                    break

    # Build active unclosed lots list
    result_lots: List[Dict[str, Any]] = []
    lot_counter = 1

    for stock_code, lots in stock_lots.items():
        curr_price = float(current_prices.get(stock_code, 0.0))

        for lot in lots:
            rem_qty = lot["quantity"]
            if rem_qty <= 0:
                continue

            b_date = lot["buy_date"]
            holding_days = max(0, (as_of_date - b_date).days)
            tax_category = "STCG" if holding_days <= 365 else "LTCG"
            days_to_ltcg = max(0, 365 - holding_days)

            buy_price = float(lot["buy_price"])
            cmp = curr_price if curr_price > 0 else buy_price
            gain_per_share = cmp - buy_price
            total_cost = rem_qty * buy_price
            current_val = rem_qty * cmp
            unrealized_gain = rem_qty * gain_per_share

            # Price cushion required:
            # If currently in STCG (rate 20%) but can convert to LTCG (rate 12.5%),
            # the tax differential is (20% - 12.5%) = 7.5% of gains.
            # To justify holding and bearing price risk rather than realizing now:
            # price_cushion_required = (STCG_RATE - LTCG_RATE) * gain_pct
            gain_pct = (gain_per_share / buy_price * 100.0) if buy_price > 0 else 0.0
            if tax_category == "STCG" and gain_pct > 0:
                # Minimum percentage decline in price the position can absorb before
                # the tax savings of waiting for LTCG are eliminated.
                price_cushion_required = round((STCG_RATE - LTCG_RATE) * (cmp - buy_price) / cmp * 100.0, 2)
            else:
                price_cushion_required = 0.0

            unique_lot_id = lot["lot_id"] if lot["lot_id"] != "None" else f"{stock_code}_LOT_{lot_counter}"
            lot_counter += 1

            result_lots.append(
                {
                    "lot_id": unique_lot_id,
                    "stock_code": stock_code,
                    "buy_date": b_date.strftime("%Y-%m-%d"),
                    "quantity": rem_qty,
                    "buy_price": round(buy_price, 2),
                    "current_price": round(cmp, 2),
                    "holding_days": holding_days,
                    "tax_category": tax_category,
                    "days_to_ltcg": days_to_ltcg,
                    "unrealized_gain_per_share": round(gain_per_share, 2),
                    "price_cushion_required": price_cushion_required,
                    "total_cost": round(total_cost, 2),
                    "current_value": round(current_val, 2),
                    "unrealized_gain": round(unrealized_gain, 2),
                }
            )

    return result_lots


def compute_tax_conversion_hurdle(
    unrealized_gain: float,
    current_market_value: float,
    ytd_realized_ltcg: float = 0.0,
) -> float:
    """Calculate Tax-Conversion Hurdle Score.

    Represents the net tax benefit (or drag) as a percentage of market value
    if held to LTCG conversion vs selling now under STCG.
    Higher score indicates higher incentive to defer selling until LTCG.
    """
    if current_market_value <= 0 or unrealized_gain <= 0:
        return 0.0

    stcg_tax = unrealized_gain * STCG_RATE

    # Check remaining exemption
    rem_exemption = max(0.0, LTCG_ANNUAL_EXEMPTION_LIMIT - ytd_realized_ltcg)
    taxable_ltcg = max(0.0, unrealized_gain - rem_exemption)
    ltcg_tax = taxable_ltcg * LTCG_RATE

    tax_saved = max(0.0, stcg_tax - ltcg_tax)
    # Hurdle score is tax saved as percentage of current market value
    hurdle_score = (tax_saved / current_market_value) * 100.0
    return round(hurdle_score, 2)


def evaluate_tax_hurdle_rule(
    lot: Dict[str, Any],
    base_action: str,
    stage: int,
    hard_stop_breached: bool = False,
    realized_stcl_available: bool = False,
    remaining_ltcg_exemption: float = LTCG_ANNUAL_EXEMPTION_LIMIT,
    as_of_date: Optional[date] = None,
    candidate_lots: Optional[List[Dict[str, Any]]] = None,
    current_prices: Optional[Dict[str, float]] = None,
    stage_map: Optional[Dict[str, int]] = None,
    upi_payee_vpa: Optional[str] = None,
    upi_payee_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Two-Tier Tax Hurdle Rule Matrix implementation.

    Rules:
    1. Hard stop breach / Stage 4: `SELL_100_IMMEDIATELY`
       (capital preservation overrides tax drag), check for loss-harvest pairs.
    2. Discretionary TRIM with STCG and days_to_ltcg <= 30 and price cushion >= 2.5%:
       `DEFER_TRIM_TAX_HOLD`
    3. Discretionary TRIM with STCG and days_to_ltcg > 30 and realized STCL/loser available:
       `PAIRED_TAX_NEUTRAL_TRIM`
    4. Discretionary TRIM with LTCG and remaining Section 112A exemption > 0:
       `PRIORITY_TAX_FREE_TRIM`
    5. Fiscal year-end March 15-31 audit:
       `GAIN_HARVEST_RESET` if unused exemption >= 10,000.
    """
    if as_of_date is None:
        as_of_date = date.today()

    tax_cat = lot.get("tax_category", "STCG")
    days_to_ltcg = int(lot.get("days_to_ltcg", 0))
    price_cushion = float(lot.get("price_cushion_required", 0.0))
    unrealized_gain = float(lot.get("unrealized_gain", 0.0))

    # Rule 5: Fiscal year-end March 15-31 audit check
    # Indian tax year ends March 31.
    is_fy_end_window = as_of_date.month == 3 and 15 <= as_of_date.day <= 31
    if is_fy_end_window and tax_cat == "LTCG" and unrealized_gain > 0 and remaining_ltcg_exemption >= 10000.0:
        return {
            "tax_action": "GAIN_HARVEST_RESET",
            "decision": "HARVEST",
            "rationale": (
                f"Fiscal year-end window ({as_of_date.strftime('%d-%b')}): Unused Section 112A exemption "
                f"₹{remaining_ltcg_exemption:,.0f} >= ₹10,000. Harvest gains to reset cost basis tax-free."
            ),
            "tax_category": tax_cat,
        }

    # Rule 1: Hard stop breach or Stage 4
    if hard_stop_breached or stage == 4 or "SELL" in base_action.upper():
        pairing_result = None
        if tax_cat == "STCG" and unrealized_gain > 0 and candidate_lots:
            pairing_result = pair_tax_loss_harvest(
                gain_lot=lot,
                candidate_lots=candidate_lots,
                current_prices=current_prices,
                stage_map=stage_map,
                upi_payee_vpa=upi_payee_vpa,
                upi_payee_name=upi_payee_name,
            )

        loss_pair_note = ""
        if pairing_result and pairing_result.harvested_losses:
            loss_pair_note = (
                f" Paired with {len(pairing_result.harvested_losses)} Stage 4 loss lot(s) "
                f"saving ₹{pairing_result.net_tax_saved:,.2f} STCG tax."
            )
        elif unrealized_gain < 0:
            loss_pair_note = " Check for loss-harvesting offsets."

        res: Dict[str, Any] = {
            "tax_action": "SELL_100_IMMEDIATELY",
            "decision": "EXIT",
            "rationale": f"Capital preservation overrides tax drag (Stage {stage} / Stop breach).{loss_pair_note}",
            "tax_category": tax_cat,
        }
        if pairing_result is not None:
            res["harvest_pairing"] = pairing_result
        return res

    # Check for Discretionary TRIM actions
    is_trim = "TRIM" in base_action.upper() or base_action.upper() == "PAUSE SIP"

    if is_trim:
        # Rule 2: STCG, days_to_ltcg <= 30 and price cushion >= 2.5%
        if tax_cat == "STCG" and days_to_ltcg <= 30 and price_cushion >= 2.5:
            return {
                "tax_action": "DEFER_TRIM_TAX_HOLD",
                "decision": "HOLD",
                "rationale": (
                    f"STCG converting to LTCG in {days_to_ltcg} days with sufficient cushion "
                    f"({price_cushion:.1f}% >= 2.5%). Defer trim to avoid 20% tax penalty."
                ),
                "tax_category": tax_cat,
            }

        # Rule 3: STCG, days_to_ltcg > 30 and realized STCL/loser available
        if tax_cat == "STCG" and days_to_ltcg > 30 and realized_stcl_available:
            return {
                "tax_action": "PAIRED_TAX_NEUTRAL_TRIM",
                "decision": "TRIM",
                "rationale": (
                    f"STCG ({days_to_ltcg} days to LTCG) paired against available short-term capital loss "
                    "for tax-neutral execution."
                ),
                "tax_category": tax_cat,
            }

        # Rule 4: LTCG and remaining Section 112A exemption > 0
        if tax_cat == "LTCG" and remaining_ltcg_exemption > 0:
            return {
                "tax_action": "PRIORITY_TAX_FREE_TRIM",
                "decision": "TRIM",
                "rationale": (
                    f"LTCG qualifying under remaining Section 112A exemption (₹{remaining_ltcg_exemption:,.0f} available). "
                    "Priority trim with zero tax liability."
                ),
                "tax_category": tax_cat,
            }

    # Default: maintain base action
    return {
        "tax_action": base_action,
        "decision": base_action,
        "rationale": f"Standard execution under {tax_cat}.",
        "tax_category": tax_cat,
    }
