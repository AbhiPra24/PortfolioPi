"""ICICI Trade Racer Order Basket CSV generator and UPI Deep Link generator.

Provides:
- ICICI Trade Racer Basket CSV format compliant with Indian broker import conventions:
  Columns: Exchange,StockCode,Action,OrderType,Validity,Quantity,Price,DisclosedQuantity,TriggerPrice
- UPI payment deep-links / mandate summaries for rebalancing alerts so broker execution
  and funds transfer can happen with 1 click without violating read-only broker API constraints.
"""

from dataclasses import dataclass, field
import io
import csv
from typing import Any, Dict, List, Optional
import urllib.parse


@dataclass
class BasketOrder:
    """Represents a single order item in an ICICI Trade Racer basket."""
    stock_code: str
    action: str = "BUY"  # BUY or SELL
    quantity: int = 1
    price: float = 0.0
    exchange: str = "NSE"
    order_type: str = "LIMIT"  # LIMIT or MARKET
    validity: str = "DAY"  # DAY or IOC
    disclosed_quantity: int = 0
    trigger_price: float = 0.0

    def to_row(self) -> Dict[str, Any]:
        """Convert order to dictionary adhering to ICICI Trade Racer columns."""
        price_val = f"{float(self.price):.2f}" if self.order_type.upper() == "LIMIT" else "0"
        trigger_val = f"{float(self.trigger_price):.2f}" if self.trigger_price > 0 else "0"
        return {
            "Exchange": self.exchange.upper().strip(),
            "StockCode": self.stock_code.upper().strip(),
            "Action": self.action.upper().strip(),
            "OrderType": self.order_type.upper().strip(),
            "Validity": self.validity.upper().strip(),
            "Quantity": int(self.quantity),
            "Price": price_val,
            "DisclosedQuantity": int(self.disclosed_quantity),
            "TriggerPrice": trigger_val,
        }


def generate_trade_racer_csv(orders: List[BasketOrder]) -> str:
    """Generate importable ICICI Trade Racer Basket CSV string.

    Columns:
    Exchange,StockCode,Action,OrderType,Validity,Quantity,Price,DisclosedQuantity,TriggerPrice

    Example line:
    NSE,FEDERALBNK,BUY,LIMIT,DAY,50,205.50,0,0
    """
    fieldnames = [
        "Exchange",
        "StockCode",
        "Action",
        "OrderType",
        "Validity",
        "Quantity",
        "Price",
        "DisclosedQuantity",
        "TriggerPrice",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()

    for order in orders:
        if order.quantity <= 0:
            continue
        writer.writerow(order.to_row())

    return output.getvalue()


def parse_trade_racer_csv(csv_content: str) -> List[BasketOrder]:
    """Parse ICICI Trade Racer Basket CSV back into a list of BasketOrder objects."""
    reader = csv.DictReader(io.StringIO(csv_content.strip()))
    orders: List[BasketOrder] = []
    for row in reader:
        orders.append(
            BasketOrder(
                exchange=row.get("Exchange", "NSE").strip(),
                stock_code=row.get("StockCode", "").strip(),
                action=row.get("Action", "BUY").strip(),
                order_type=row.get("OrderType", "LIMIT").strip(),
                validity=row.get("Validity", "DAY").strip(),
                quantity=int(row.get("Quantity", 1)),
                price=float(row.get("Price", 0.0)),
                disclosed_quantity=int(row.get("DisclosedQuantity", 0)),
                trigger_price=float(row.get("TriggerPrice", 0.0)),
            )
        )
    return orders


@dataclass
class UpiMandateSummary:
    """UPI deep-link & mandate summary for one-tap execution / funds transfer."""
    payee_vpa: str
    payee_name: str
    amount: float
    currency: str = "INR"
    note: str = "PortfolioPi Rebalance"
    transaction_ref: Optional[str] = None
    deep_link: str = field(init=False)

    def __post_init__(self):
        self.deep_link = generate_upi_deep_link(
            payee_vpa=self.payee_vpa,
            payee_name=self.payee_name,
            amount=self.amount,
            currency=self.currency,
            note=self.note,
            transaction_ref=self.transaction_ref,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "payee_vpa": self.payee_vpa,
            "payee_name": self.payee_name,
            "amount": round(self.amount, 2),
            "currency": self.currency,
            "note": self.note,
            "transaction_ref": self.transaction_ref,
            "deep_link": self.deep_link,
        }


def generate_upi_deep_link(
    payee_vpa: str,
    payee_name: str,
    amount: float,
    currency: str = "INR",
    note: str = "PortfolioPi Rebalance",
    transaction_ref: Optional[str] = None,
) -> str:
    """Generate a standard NPCI compliant UPI deep-link (upi://pay?...).

    Parameters
    ----------
    payee_vpa : str
        Payee Virtual Payment Address (e.g., 'icicidirect.funding@icici' or user broker VPA).
    payee_name : str
        Display name of the payee.
    amount : float
        Transaction amount in INR.
    currency : str, default 'INR'
        Currency code, fixed to INR.
    note : str, default 'PortfolioPi Rebalance'
        Transaction note or description.
    transaction_ref : Optional[str]
        Unique transaction reference ID.

    Returns
    -------
    str
        NPCI formatted deep-link string: upi://pay?pa=...&pn=...&am=...&cu=INR&tn=...
    """
    params: Dict[str, str] = {
        "pa": payee_vpa.strip(),
        "pn": payee_name.strip(),
        "am": f"{float(amount):.2f}",
        "cu": currency.upper().strip(),
        "tn": note.strip(),
    }
    if transaction_ref:
        params["tr"] = transaction_ref.strip()

    # URL encode query parameters
    query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
    return f"upi://pay?{query_string}"


def create_rebalance_execution_payload(
    orders: List[BasketOrder],
    broker_vpa: str = "icicidirect@icici",
    broker_name: str = "ICICI Direct",
    basket_name: str = "Rebalance_Basket",
    reference_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate comprehensive execution payload containing:
    1. ICICI Trade Racer basket CSV string.
    2. Basket metadata (total capital required, order counts).
    3. UPI payment deep-link to fund the exact amount into broker trading account.
    """
    csv_payload = generate_trade_racer_csv(orders)

    total_buy_value = 0.0
    total_sell_value = 0.0
    for order in orders:
        val = float(order.price) * int(order.quantity)
        if order.action.upper() == "BUY":
            total_buy_value += val
        elif order.action.upper() == "SELL":
            total_sell_value += val

    net_capital_needed = max(0.0, total_buy_value - total_sell_value)

    upi_summary: Optional[Dict[str, Any]] = None
    if net_capital_needed > 0:
        mandate = UpiMandateSummary(
            payee_vpa=broker_vpa,
            payee_name=broker_name,
            amount=net_capital_needed,
            note=f"Funds for {basket_name}",
            transaction_ref=reference_id,
        )
        upi_summary = mandate.to_dict()

    return {
        "basket_name": basket_name,
        "csv_payload": csv_payload,
        "total_orders": len(orders),
        "total_buy_value": round(total_buy_value, 2),
        "total_sell_value": round(total_sell_value, 2),
        "net_capital_required": round(net_capital_needed, 2),
        "upi_mandate": upi_summary,
    }
