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
from typing import Any, Dict, List, Optional, Set
import urllib.parse


# Well-known illiquid instrument tickers / patterns on Indian exchanges
# (REITs, InvITs, Debt ETFs, G-Sec ETFs, SDL ETFs, CPSE Debt, etc.)
DEFAULT_ILLIQUID_ASSETS: Set[str] = {
    # REITs
    "EMBASSY",
    "EMBOFF",
    "MINDSPACE",
    "BIRET",
    "NEXUS",
    "NEXSEL",
    # InvITs
    "PGINVIT",
    "POWINF",
    "INDIGRID",
    "INDGRI",
    "IRB",
    "IRBINVIT",
    # Debt / G-Sec / SDL / Bharat Bond ETFs
    "EBBETF0430",
    "EBBETF0431",
    "EBBETF0432",
    "EBBETF0433",
    "GSEC5IETF",
    "GSEC10IETF",
    "SDL26ICICI",
    "SDL27ICICI",
    "SETF10GILT",
    "LIQUIDBEES",
    "LIQUIDETF",
    "LIQUIDCASE",
}

# Default slip protection buffers
DEFAULT_BUFFER_PCT: float = 0.003          # 0.3% standard for liquid equities
DEFAULT_ILLIQUID_BUFFER_PCT: float = 0.008 # 0.8% for illiquid assets (REITs, InvITs, Debt ETFs)


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
    is_illiquid: bool = False

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


def is_instrument_illiquid(stock_code: str) -> bool:
    """Determine whether a stock symbol represents an illiquid instrument (REIT, InvIT, Debt ETF)."""
    code = stock_code.upper().strip()
    if code in DEFAULT_ILLIQUID_ASSETS:
        return True
    # Substring heuristic checks for REITs, InvITs, and Debt / Gilt / SDL ETF series
    illiquid_markers = ("INVIT", "REIT", "GILT", "GSEC", "SDL", "EBBETF", "DEBT")
    return any(marker in code for marker in illiquid_markers)


def compute_buffered_price(
    cmp: float,
    action: str,
    buffer_pct: float,
) -> float:
    """Calculate slip-protected limit price based on CMP, action, and buffer percentage.

    - BUY: limit price capped at CMP * (1.0 + buffer_pct)
    - SELL: limit price floored at CMP * (1.0 - buffer_pct)
    """
    act = action.upper().strip()
    if act == "BUY":
        return round(float(cmp) * (1.0 + abs(buffer_pct)), 2)
    elif act == "SELL":
        return round(float(cmp) * (1.0 - abs(buffer_pct)), 2)
    return round(float(cmp), 2)


def create_slip_protected_basket_order(
    stock_code: str,
    action: str,
    quantity: int,
    cmp: float,
    buffer_pct: float = DEFAULT_BUFFER_PCT,
    is_illiquid: Optional[bool] = None,
    exchange: str = "NSE",
    validity: str = "DAY",
    disclosed_quantity: int = 0,
    trigger_price: float = 0.0,
) -> BasketOrder:
    """Create a single BasketOrder protected with dynamic limit slip guardrail.

    If is_illiquid is None, automatically detected via `is_instrument_illiquid(stock_code)`.
    For illiquid assets, unconstrained MARKET orders are disallowed and OrderType is enforced to LIMIT,
    and if default buffer_pct (0.003) was passed without explicit override, the illiquid default (0.008) is applied.
    """
    clean_code = stock_code.upper().strip()
    illiquid = is_illiquid if is_illiquid is not None else is_instrument_illiquid(clean_code)

    # Use illiquid default buffer (0.8%) if caller used standard liquid default (0.3%)
    effective_buffer = buffer_pct
    if illiquid and buffer_pct == DEFAULT_BUFFER_PCT:
        effective_buffer = DEFAULT_ILLIQUID_BUFFER_PCT

    limit_price = compute_buffered_price(cmp, action, effective_buffer)

    return BasketOrder(
        stock_code=clean_code,
        action=action.upper().strip(),
        quantity=int(quantity),
        price=limit_price,
        exchange=exchange.upper().strip(),
        order_type="LIMIT",  # Always LIMIT with slip protection guardrails
        validity=validity.upper().strip(),
        disclosed_quantity=int(disclosed_quantity),
        trigger_price=float(trigger_price),
        is_illiquid=illiquid,
    )


def apply_slip_protection(
    orders: List[BasketOrder],
    buffer_pct: float = DEFAULT_BUFFER_PCT,
    asset_buffers: Optional[Dict[str, float]] = None,
) -> List[BasketOrder]:
    """Enhance BasketOrder list with dynamic limit slip-protection guardrails.

    - For BUY orders: limit price = round(CMP * (1.0 + buffer), 2) (default buffer: +0.3% / 0.003).
    - For SELL orders: limit price = round(CMP * (1.0 - buffer), 2) (default buffer: -0.3% / 0.003).
    - For illiquid instruments (InvITs, REITs, debt ETFs, smallcaps):
      custom buffer overrides via `asset_buffers` dict or fallback to DEFAULT_ILLIQUID_BUFFER_PCT (0.8%).
    - Avoid unconstrained MARKET orders on illiquid instruments by enforcing OrderType='LIMIT'.
    """
    asset_buffers = {k.upper().strip(): v for k, v in (asset_buffers or {}).items()}
    protected_orders: List[BasketOrder] = []

    for o in orders:
        clean_code = o.stock_code.upper().strip()
        illiquid = getattr(o, "is_illiquid", False) or is_instrument_illiquid(clean_code)

        # Determine effective buffer for this instrument
        if clean_code in asset_buffers:
            eff_buffer = asset_buffers[clean_code]
        elif illiquid:
            eff_buffer = DEFAULT_ILLIQUID_BUFFER_PCT
        else:
            eff_buffer = buffer_pct

        cmp = float(o.price)
        # Calculate limit price with buffer guardrail
        limit_price = compute_buffered_price(cmp, o.action, eff_buffer)

        # Enforce LIMIT order type for illiquid instruments or if market order had CMP specified
        order_type = o.order_type.upper().strip()
        if illiquid or order_type == "MARKET":
            order_type = "LIMIT"

        protected_order = BasketOrder(
            stock_code=clean_code,
            action=o.action.upper().strip(),
            quantity=int(o.quantity),
            price=limit_price,
            exchange=o.exchange.upper().strip(),
            order_type=order_type,
            validity=o.validity.upper().strip(),
            disclosed_quantity=int(o.disclosed_quantity),
            trigger_price=float(o.trigger_price),
            is_illiquid=illiquid,
        )
        protected_orders.append(protected_order)

    return protected_orders


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
    slip_protection: bool = False,
    buffer_pct: float = DEFAULT_BUFFER_PCT,
    asset_buffers: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Generate comprehensive execution payload containing:
    1. ICICI Trade Racer basket CSV string.
    2. Basket metadata (total capital required, order counts).
    3. UPI payment deep-link to fund the exact amount into broker trading account.

    Parameters
    ----------
    orders : List[BasketOrder]
        List of basket orders.
    broker_vpa : str
        Broker UPI ID.
    broker_name : str
        Broker display name.
    basket_name : str
        Basket identifier.
    reference_id : Optional[str]
        Reference ID for transaction tracking.
    slip_protection : bool, default False
        Whether to dynamically apply limit slip-protection guardrails on the orders.
    buffer_pct : float, default 0.003 (+/- 0.3%)
        Default buffer for liquid instruments.
    asset_buffers : Optional[Dict[str, float]]
        Custom overrides for specific symbols (e.g. 0.008 for REITs/InvITs).
    """
    effective_orders = apply_slip_protection(orders, buffer_pct=buffer_pct, asset_buffers=asset_buffers) if slip_protection else orders
    csv_payload = generate_trade_racer_csv(effective_orders)

    total_buy_value = 0.0
    total_sell_value = 0.0
    for order in effective_orders:
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
        "total_orders": len(effective_orders),
        "total_buy_value": round(total_buy_value, 2),
        "total_sell_value": round(total_sell_value, 2),
        "net_capital_required": round(net_capital_needed, 2),
        "upi_mandate": upi_summary,
    }
