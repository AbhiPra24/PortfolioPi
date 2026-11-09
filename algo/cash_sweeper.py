"""Automated Cash Drag Sweeper.

Tracks idle cash, accrued dividend payouts, and paused SIP budget.
When total idle cash exceeds a threshold (default ₹5,000), generates automated
allocation directives:
  - Option A: Sweeps into top Stage 2 breakout compounder.
  - Option B: Sweeps into overnight low-risk liquid ETF (e.g. LIQUIDBEES / LIQUIDETF) to avoid cash drag.
Also generates execution basket orders and UPI funding mandates.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
import pandas as pd

from algo.order_payloads import BasketOrder, create_rebalance_execution_payload


class SweepTargetType(str, Enum):
    STAGE2_COMPOUNDER = "STAGE2_COMPOUNDER"
    LIQUID_ETF = "LIQUID_ETF"


@dataclass
class CashSweepDirective:
    """Actionable directive produced when cash exceeds drag threshold."""
    target_type: SweepTargetType
    symbol: str
    action: str
    target_price: float
    allocation_amount: float
    quantity: int
    unallocated_cash_balance: float
    rationale: str
    order: BasketOrder

    def to_dict(self) -> Dict[str, Any]:
        return {
            "target_type": self.target_type.value,
            "symbol": self.symbol,
            "action": self.action,
            "target_price": round(self.target_price, 2),
            "allocation_amount": round(self.allocation_amount, 2),
            "quantity": self.quantity,
            "unallocated_cash_balance": round(self.unallocated_cash_balance, 2),
            "rationale": self.rationale,
            "order": self.order.to_row(),
        }


@dataclass
class CashDragReport:
    """Evaluation summary of portfolio cash balance and sweep opportunities."""
    unallocated_cash: float
    accrued_dividends: float
    paused_sip_budget: float
    total_idle_cash: float
    threshold: float
    sweep_triggered: bool
    option_a_directive: Optional[CashSweepDirective] = None
    option_b_directive: Optional[CashSweepDirective] = None
    execution_payload_option_a: Optional[Dict[str, Any]] = None
    execution_payload_option_b: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unallocated_cash": round(self.unallocated_cash, 2),
            "accrued_dividends": round(self.accrued_dividends, 2),
            "paused_sip_budget": round(self.paused_sip_budget, 2),
            "total_idle_cash": round(self.total_idle_cash, 2),
            "threshold": round(self.threshold, 2),
            "sweep_triggered": self.sweep_triggered,
            "option_a": self.option_a_directive.to_dict() if self.option_a_directive else None,
            "option_b": self.option_b_directive.to_dict() if self.option_b_directive else None,
            "execution_payload_option_a": self.execution_payload_option_a,
            "execution_payload_option_b": self.execution_payload_option_b,
        }


def evaluate_cash_drag(
    unallocated_cash: float = 0.0,
    accrued_dividends: float = 0.0,
    paused_sip_budget: float = 0.0,
    threshold: float = 5000.0,
    stage2_candidates: Optional[List[Dict[str, Any]]] = None,
    liquid_etf_symbol: str = "LIQUIDBEES",
    liquid_etf_price: float = 1000.0,
    broker_vpa: str = "icicidirect@icici",
) -> CashDragReport:
    """Evaluate cash drag across cash sources and generate sweep directives.

    Parameters
    ----------
    unallocated_cash : float
        Liquid unallocated ledger balance in trading account.
    accrued_dividends : float
        Dividends received in bank or demat awaiting deployment.
    paused_sip_budget : float
        Capital saved from paused/trimmed SIPs awaiting allocation.
    threshold : float, default 5000.0
        Minimum cash threshold to trigger automated sweep (₹5,000).
    stage2_candidates : Optional[List[Dict[str, Any]]]
        List of Stage 2 candidate stocks ranked by strength.
        Expected format per dict: {'symbol': 'FEDERALBNK', 'price': 205.50, 'rs_score': 85.0, ...}
    liquid_etf_symbol : str, default 'LIQUIDBEES'
        Default cash park ETF (e.g. 'LIQUIDBEES', 'LIQUIDETF').
    liquid_etf_price : float, default 1000.0
        Current market price for the liquid ETF (LIQUIDBEES NAV is fixed at ~1000.0).
    broker_vpa : str
        UPI VPA for funding if needed.

    Returns
    -------
    CashDragReport
    """
    total_idle_cash = max(0.0, float(unallocated_cash) + float(accrued_dividends) + float(paused_sip_budget))
    sweep_triggered = total_idle_cash >= threshold

    if not sweep_triggered:
        return CashDragReport(
            unallocated_cash=unallocated_cash,
            accrued_dividends=accrued_dividends,
            paused_sip_budget=paused_sip_budget,
            total_idle_cash=total_idle_cash,
            threshold=threshold,
            sweep_triggered=False,
            option_a_directive=None,
            option_b_directive=None,
        )

    # Option A: Sweep into top Stage 2 breakout compounder
    directive_a: Optional[CashSweepDirective] = None
    payload_a: Optional[Dict[str, Any]] = None

    if stage2_candidates and len(stage2_candidates) > 0:
        # Sort by rs_score or rank if available
        sorted_candidates = sorted(
            stage2_candidates,
            key=lambda x: (x.get("stage", 2) == 2, x.get("rs_score", 0.0), -x.get("price", 1e9)),
            reverse=True,
        )
        top_cand = sorted_candidates[0]
        cand_sym = str(top_cand.get("symbol") or top_cand.get("stock_code") or "").upper().strip()
        cand_price = float(top_cand.get("price", 0.0))

        if cand_sym and cand_price > 0:
            qty_a = int(total_idle_cash // cand_price)
            if qty_a > 0:
                allocated_a = qty_a * cand_price
                order_a = BasketOrder(
                    stock_code=cand_sym,
                    action="BUY",
                    quantity=qty_a,
                    price=cand_price,
                    exchange="NSE",
                    order_type="LIMIT",
                )
                directive_a = CashSweepDirective(
                    target_type=SweepTargetType.STAGE2_COMPOUNDER,
                    symbol=cand_sym,
                    action="BUY",
                    target_price=cand_price,
                    allocation_amount=allocated_a,
                    quantity=qty_a,
                    unallocated_cash_balance=total_idle_cash - allocated_a,
                    rationale=(
                        f"Idle cash ₹{total_idle_cash:,.2f} exceeds threshold ₹{threshold:,.2f}. "
                        f"Deploying into top Stage 2 compounder {cand_sym} (RS: {top_cand.get('rs_score', 0):.1f})."
                    ),
                    order=order_a,
                )
                payload_a = create_rebalance_execution_payload(
                    orders=[order_a],
                    broker_vpa=broker_vpa,
                    basket_name=f"Sweep_Stage2_{cand_sym}",
                )

    # Option B: Sweep into overnight low-risk liquid ETF (LIQUIDBEES / LIQUIDETF)
    etf_sym = liquid_etf_symbol.upper().strip()
    etf_price = max(1.0, float(liquid_etf_price))
    qty_b = int(total_idle_cash // etf_price)
    directive_b: Optional[CashSweepDirective] = None
    payload_b: Optional[Dict[str, Any]] = None

    if qty_b > 0:
        allocated_b = qty_b * etf_price
        order_b = BasketOrder(
            stock_code=etf_sym,
            action="BUY",
            quantity=qty_b,
            price=etf_price,
            exchange="NSE",
            order_type="LIMIT",
        )
        directive_b = CashSweepDirective(
            target_type=SweepTargetType.LIQUID_ETF,
            symbol=etf_sym,
            action="BUY",
            target_price=etf_price,
            allocation_amount=allocated_b,
            quantity=qty_b,
            unallocated_cash_balance=total_idle_cash - allocated_b,
            rationale=(
                f"Idle cash ₹{total_idle_cash:,.2f} exceeds threshold ₹{threshold:,.2f}. "
                f"Sweeping into overnight risk-free ETF {etf_sym} to avoid cash drag."
            ),
            order=order_b,
        )
        payload_b = create_rebalance_execution_payload(
            orders=[order_b],
            broker_vpa=broker_vpa,
            basket_name=f"Sweep_Liquid_{etf_sym}",
        )

    return CashDragReport(
        unallocated_cash=unallocated_cash,
        accrued_dividends=accrued_dividends,
        paused_sip_budget=paused_sip_budget,
        total_idle_cash=total_idle_cash,
        threshold=threshold,
        sweep_triggered=True,
        option_a_directive=directive_a,
        option_b_directive=directive_b,
        execution_payload_option_a=payload_a,
        execution_payload_option_b=payload_b,
    )
