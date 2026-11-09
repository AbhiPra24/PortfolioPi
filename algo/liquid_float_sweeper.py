"""Dynamic Liquid Float Sweeper.

Monitors unallocated cash float resulting from idle ledger cash and SIP Allocator
freezes on Stage 3/4 breakdowns (target_action in ('TRIM / PAUSE SIP', 'SELL / STOP SIP')).
Calculates:
    unallocated_cash_float = unallocated_cash + sum(paused_monthly_sip)

Routing Rules:
1. When Minervini screener identifies no new Stage 2 breakout leader:
   Generates a Trade Racer CSV basket order to sweep this paused capital into overnight liquid ETF
   (default: LIQUIDBEES, quantity = floor(unallocated_cash_float / price)).
2. When Minervini screener identifies a new Stage 2 breakout leader:
   Emits a redeployment directive to shift from LIQUIDBEES into the Stage 2 leader.
3. Supports generating importable Trade Racer CSV payloads and UPI deep links.
"""

import math
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import aiosqlite

from algo.order_payloads import BasketOrder, create_rebalance_execution_payload, generate_trade_racer_csv


class RedeploymentAction(str, Enum):
    SWEEP_TO_LIQUIDBEES = "SWEEP_TO_LIQUIDBEES"
    REDEPLOY_TO_STAGE2_LEADER = "REDEPLOY_TO_STAGE2_LEADER"
    NO_ACTION = "NO_ACTION"


@dataclass
class LiquidFloatReport:
    """Evaluation summary of unallocated cash float and dynamic sweep/redeployment action."""

    unallocated_cash: float
    paused_sip_amount: float
    unallocated_cash_float: float
    action: RedeploymentAction
    target_ticker: str
    target_price: float
    quantity: int
    allocation_amount: float
    remaining_cash: float
    rationale: str
    orders: List[BasketOrder] = field(default_factory=list)
    trade_racer_csv: str = ""
    execution_payload: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unallocated_cash": round(self.unallocated_cash, 2),
            "paused_sip_amount": round(self.paused_sip_amount, 2),
            "unallocated_cash_float": round(self.unallocated_cash_float, 2),
            "action": self.action.value,
            "target_ticker": self.target_ticker,
            "target_price": round(self.target_price, 2),
            "quantity": self.quantity,
            "allocation_amount": round(self.allocation_amount, 2),
            "remaining_cash": round(self.remaining_cash, 2),
            "rationale": self.rationale,
            "orders": [o.to_row() for o in self.orders],
            "trade_racer_csv": self.trade_racer_csv,
            "execution_payload": self.execution_payload,
        }


def calculate_paused_sip_float(db_connection: sqlite3.Connection) -> float:
    """Synchronous helper: Query sip_book for paused/frozen monthly SIP amounts."""
    cur = db_connection.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(monthly_sip), 0.0) as paused_total
        FROM sip_book
        WHERE UPPER(TRIM(target_action)) IN ('TRIM / PAUSE SIP', 'SELL / STOP SIP', 'HOLD / PAUSE SIP', 'PAUSE')
           OR UPPER(TRIM(status)) IN ('PAUSED', 'FROZEN', 'STOPPED')
           OR quant_stage IN (3, 4)
    """)
    row = cur.fetchone()
    paused_total = float(row[0] if row else 0.0)
    return max(0.0, paused_total)


async def calculate_paused_sip_float_async(db_connection: aiosqlite.Connection) -> float:
    """Asynchronous helper: Query sip_book for paused/frozen monthly SIP amounts."""
    async with db_connection.execute("""
        SELECT COALESCE(SUM(monthly_sip), 0.0) as paused_total
        FROM sip_book
        WHERE UPPER(TRIM(target_action)) IN ('TRIM / PAUSE SIP', 'SELL / STOP SIP', 'HOLD / PAUSE SIP', 'PAUSE')
           OR UPPER(TRIM(status)) IN ('PAUSED', 'FROZEN', 'STOPPED')
           OR quant_stage IN (3, 4)
    """) as cur:
        row = await cur.fetchone()
        paused_total = float(row[0] if row else 0.0)
        return max(0.0, paused_total)


def compute_unallocated_cash_float(unallocated_cash: float, paused_sip_amount: float) -> float:
    """Calculate total unallocated cash float."""
    return max(0.0, float(unallocated_cash) + float(paused_sip_amount))


def evaluate_liquid_float_sweep(
    unallocated_cash: float = 0.0,
    paused_sip_amount: float = 0.0,
    stage2_leaders: Optional[List[Dict[str, Any]]] = None,
    liquid_etf_ticker: str = "LIQUIDBEES",
    liquid_etf_price: float = 1000.0,
    min_sweep_threshold: float = 1000.0,
    broker_vpa: str = "icicidirect@icici",
    liquidbees_holdings_qty: int = 0,
) -> LiquidFloatReport:
    """
    Evaluates dynamic liquid float sweeping and redeployment.

    Rules:
    - If unallocated_cash_float < min_sweep_threshold: No action.
    - If Minervini screener identifies a new Stage 2 breakout leader (e.g. stage2_leaders contains valid setups):
      Emit REDEPLOY_TO_STAGE2_LEADER directive.
      If liquidbees_holdings_qty > 0, generate SELL order for LIQUIDBEES and BUY order for Stage 2 leader.
      Otherwise, allocate the float into the Stage 2 leader.
    - If no new Stage 2 leader:
      Sweep float into LIQUIDBEES: quantity = floor(unallocated_cash_float / liquid_etf_price).
    """
    total_float = compute_unallocated_cash_float(unallocated_cash, paused_sip_amount)
    etf_ticker = liquid_etf_ticker.strip().upper()
    etf_price = max(1.0, float(liquid_etf_price))

    if total_float < min_sweep_threshold:
        return LiquidFloatReport(
            unallocated_cash=unallocated_cash,
            paused_sip_amount=paused_sip_amount,
            unallocated_cash_float=total_float,
            action=RedeploymentAction.NO_ACTION,
            target_ticker="",
            target_price=0.0,
            quantity=0,
            allocation_amount=0.0,
            remaining_cash=total_float,
            rationale=f"Cash float ₹{total_float:,.2f} is below minimum sweep threshold ₹{min_sweep_threshold:,.2f}.",
            orders=[],
            trade_racer_csv="",
            execution_payload=None,
        )

    # Check for Stage 2 breakout leaders
    stage2_leader = None
    if stage2_leaders:
        valid_leaders = [
            c
            for c in stage2_leaders
            if (c.get("stage") == 2 or c.get("quant_stage") == 2) and float(c.get("price", 0.0)) > 0
        ]
        if valid_leaders:
            # Sort by rs_score descending or composite_score descending
            valid_leaders.sort(
                key=lambda x: (float(x.get("rs_score", 0.0)), float(x.get("score", x.get("composite_score", 0.0)))),
                reverse=True,
            )
            stage2_leader = valid_leaders[0]

    # Scenario 1: Stage 2 Breakout Leader Identified -> REDEPLOY
    if stage2_leader is not None:
        leader_ticker = (
            str(stage2_leader.get("ticker", stage2_leader.get("symbol", stage2_leader.get("stock_code", ""))))
            .strip()
            .upper()
        )
        leader_price = float(stage2_leader.get("price", 0.0))

        orders: List[BasketOrder] = []
        redeployment_capital = total_float

        # If holding LIQUIDBEES that should be harvested, sell them to fund Stage 2 leader
        if liquidbees_holdings_qty > 0:
            harvest_value = liquidbees_holdings_qty * etf_price
            redeployment_capital += harvest_value
            orders.append(
                BasketOrder(
                    stock_code=etf_ticker,
                    action="SELL",
                    quantity=liquidbees_holdings_qty,
                    price=etf_price,
                    exchange="NSE",
                    order_type="LIMIT",
                )
            )

        leader_qty = int(math.floor(redeployment_capital / leader_price)) if leader_price > 0 else 0
        allocated_amount = leader_qty * leader_price
        remaining_cash = redeployment_capital - allocated_amount

        if leader_qty > 0:
            buy_order = BasketOrder(
                stock_code=leader_ticker,
                action="BUY",
                quantity=leader_qty,
                price=leader_price,
                exchange="NSE",
                order_type="LIMIT",
            )
            orders.append(buy_order)

        csv_str = generate_trade_racer_csv(orders)
        exec_payload = (
            create_rebalance_execution_payload(
                orders=orders,
                broker_vpa=broker_vpa,
                basket_name=f"Redeploy_{leader_ticker}",
            )
            if orders
            else None
        )

        rationale = (
            f"Stage 2 breakout leader {leader_ticker} identified (RS: {stage2_leader.get('rs_score', 'N/A')}). "
            f"Redeploying float ₹{total_float:,.2f}"
            + (
                f" + harvesting {liquidbees_holdings_qty} {etf_ticker} (₹{harvest_value:,.2f})"
                if liquidbees_holdings_qty > 0
                else ""
            )
            + f" into {leader_qty} shares of {leader_ticker}."
        )

        return LiquidFloatReport(
            unallocated_cash=unallocated_cash,
            paused_sip_amount=paused_sip_amount,
            unallocated_cash_float=total_float,
            action=RedeploymentAction.REDEPLOY_TO_STAGE2_LEADER,
            target_ticker=leader_ticker,
            target_price=leader_price,
            quantity=leader_qty,
            allocation_amount=allocated_amount,
            remaining_cash=remaining_cash,
            rationale=rationale,
            orders=orders,
            trade_racer_csv=csv_str,
            execution_payload=exec_payload,
        )

    # Scenario 2: No Stage 2 Leader Identified -> SWEEP TO LIQUIDBEES
    qty_etf = int(math.floor(total_float / etf_price))
    allocated_amount = qty_etf * etf_price
    remaining_cash = total_float - allocated_amount

    orders = []
    if qty_etf > 0:
        sweep_order = BasketOrder(
            stock_code=etf_ticker,
            action="BUY",
            quantity=qty_etf,
            price=etf_price,
            exchange="NSE",
            order_type="LIMIT",
        )
        orders.append(sweep_order)

    csv_str = generate_trade_racer_csv(orders)
    exec_payload = (
        create_rebalance_execution_payload(
            orders=orders,
            broker_vpa=broker_vpa,
            basket_name=f"Sweep_{etf_ticker}",
        )
        if orders
        else None
    )

    rationale = (
        f"No Stage 2 breakout leader identified. Freezing capital from breakdown SIPs (₹{paused_sip_amount:,.2f}) "
        f"and sweeping total float ₹{total_float:,.2f} into {qty_etf} shares of {etf_ticker} to prevent cash drag."
    )

    return LiquidFloatReport(
        unallocated_cash=unallocated_cash,
        paused_sip_amount=paused_sip_amount,
        unallocated_cash_float=total_float,
        action=RedeploymentAction.SWEEP_TO_LIQUIDBEES,
        target_ticker=etf_ticker,
        target_price=etf_price,
        quantity=qty_etf,
        allocation_amount=allocated_amount,
        remaining_cash=remaining_cash,
        rationale=rationale,
        orders=orders,
        trade_racer_csv=csv_str,
        execution_payload=exec_payload,
    )
