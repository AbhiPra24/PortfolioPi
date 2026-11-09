"""Sovereign Gold Bond (SGB) Secondary Market Arbitrage Engine.

Analyzes secondary market SGB quotes against spot gold / benchmark ETF (GOLDBEES),
calculates:
  1. Discount-to-Spot %
  2. Yield-to-Maturity (YTM) accounting for semi-annual 2.5% p.a. sovereign coupon on issue price
     and capital redemption at gold spot at maturity.
  3. Identifies and flags high-yield arbitrage opportunities (e.g. discount >= 3.0%).
  4. Generates execution order basket and UPI funding deep link.
"""

from dataclasses import dataclass
import datetime
import math
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import pandas as pd

from algo.order_payloads import BasketOrder, create_rebalance_execution_payload


# Standard SGB Tranche Reference Database with Issue Prices & Maturity Dates
# Default coupon rate: 2.5% p.a. payable semi-annually on nominal issue price
SGB_TRANCHE_METADATA: Dict[str, Dict[str, Any]] = {
    # 2026 Maturities
    "SGBOCT26": {"maturity_date": "2026-10-23", "issue_price": 5051.0, "coupon_rate": 0.025},
    "SGBNOV26": {"maturity_date": "2026-11-18", "issue_price": 5177.0, "coupon_rate": 0.025},
    "SGBDEC26": {"maturity_date": "2026-12-28", "issue_price": 5000.0, "coupon_rate": 0.025},
    # 2027 Maturities
    "SGBJAN27": {"maturity_date": "2027-01-18", "issue_price": 5104.0, "coupon_rate": 0.025},
    "SGBFEB27": {"maturity_date": "2027-02-17", "issue_price": 4912.0, "coupon_rate": 0.025},
    "SGBMAY27": {"maturity_date": "2027-05-25", "issue_price": 4777.0, "coupon_rate": 0.025},
    "SGBJUN27": {"maturity_date": "2027-06-08", "issue_price": 4889.0, "coupon_rate": 0.025},
    "SGBJUL27": {"maturity_date": "2027-07-14", "issue_price": 4852.0, "coupon_rate": 0.025},
    "SGBOCT27": {"maturity_date": "2027-10-18", "issue_price": 4761.0, "coupon_rate": 0.025},
    # 2028 Maturities
    "SGBJUN28": {"maturity_date": "2028-06-16", "issue_price": 4677.0, "coupon_rate": 0.025},
    "SGBDEC28": {"maturity_date": "2028-12-28", "issue_price": 4800.0, "coupon_rate": 0.025},
    # 2029 Maturities
    "SGBMAY29": {"maturity_date": "2029-05-25", "issue_price": 4842.0, "coupon_rate": 0.025},
    "SGBOCT29": {"maturity_date": "2029-10-14", "issue_price": 5041.0, "coupon_rate": 0.025},
    "SGBNOV29": {"maturity_date": "2029-11-20", "issue_price": 5085.0, "coupon_rate": 0.025},
    # 2030 Maturities
    "SGBJAN30": {"maturity_date": "2030-01-15", "issue_price": 5109.0, "coupon_rate": 0.025},
    "SGBFEB30": {"maturity_date": "2030-02-12", "issue_price": 5117.0, "coupon_rate": 0.025},
    "SGBMAY30": {"maturity_date": "2030-05-20", "issue_price": 5334.0, "coupon_rate": 0.025},
    "SGBSEP30": {"maturity_date": "2030-09-17", "issue_price": 5054.0, "coupon_rate": 0.025},
    # 2031 Maturities
    "SGBDE31I": {"maturity_date": "2031-12-28", "issue_price": 6199.0, "coupon_rate": 0.025},
    "SGBFE32": {"maturity_date": "2032-02-21", "issue_price": 6263.0, "coupon_rate": 0.025},
}

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    "MR": 3, "AL": 4, "JL": 7, "AG": 8, "SP": 9, "OC": 10, "NV": 11, "DE": 12,
}


def parse_sgb_symbol_metadata(symbol: str) -> Dict[str, Any]:
    """Extract maturity date and default issue price estimates from SGB symbol name."""
    clean = symbol.upper().replace(".NS", "").replace(".BO", "").strip()

    if clean in SGB_TRANCHE_METADATA:
        meta = dict(SGB_TRANCHE_METADATA[clean])
        meta["symbol"] = clean
        return meta

    # Heuristic fallback parsing pattern like SGBJUN28, SGBDE31I, SGB28IV
    # Standard format: SGB + [3 or 2 letter Month] + [2 digit year]
    m = re.match(r"^SGB([A-Z]{2,3})(\d{2})", clean)
    if m:
        mon_str = m.group(1)
        yr_str = m.group(2)
        month = MONTH_MAP.get(mon_str, 6)
        year = 2000 + int(yr_str)
        # Default day 15
        maturity_str = f"{year:04d}-{month:02d}-15"
        return {
            "symbol": clean,
            "maturity_date": maturity_str,
            "issue_price": 5000.0,
            "coupon_rate": 0.025,
        }

    # Default fallback: 3 years from today
    today = datetime.date.today()
    default_maturity = f"{today.year + 3}-06-15"
    return {
        "symbol": clean,
        "maturity_date": default_maturity,
        "issue_price": 5000.0,
        "coupon_rate": 0.025,
    }


def calculate_gold_spot_from_goldbees(goldbees_price: float, gram_ratio: float = 0.01) -> float:
    """Derive gold spot price per gram from GOLDBEES market price.

    1 unit of GOLDBEES represents approximately 0.01 gram (or 1/100th of a gram) of 99.5%+ pure physical gold.
    Therefore:
        Gold Spot per Gram ≈ GOLDBEES_Price / 0.01 (or GOLDBEES_Price * 100)
    If gram_ratio is custom (e.g. 1 unit = 0.01g), spot = price / gram_ratio.
    """
    if gram_ratio <= 0:
        gram_ratio = 0.01
    return float(goldbees_price) / float(gram_ratio)


def compute_sgb_discount_to_spot(sgb_price: float, spot_per_gram: float) -> float:
    """Calculate discount to spot gold in percentage terms.

    Positive discount means SGB is cheaper than spot gold (favorable buying opportunity).
    Discount % = ((Spot - SGB_Price) / Spot) * 100
    """
    if spot_per_gram <= 0:
        return 0.0
    return float((spot_per_gram - sgb_price) / spot_per_gram * 100.0)


def calculate_sgb_ytm(
    traded_price: float,
    maturity_date: Union[str, datetime.date],
    issue_price: float,
    current_gold_spot: float,
    coupon_rate: float = 0.025,
    current_date: Optional[Union[str, datetime.date]] = None,
) -> float:
    """Calculate annualized Yield-To-Maturity (YTM) for an SGB tranche.

    Cash Flows:
    - Initial outflow at t=0: -traded_price
    - Semi-annual coupon payments: (coupon_rate / 2) * issue_price
    - Maturity redemption at t=T: redeemed at prevailing gold price (assumed spot) + final coupon.

    Uses internal rate of return (Newton-Raphson method) solved for semi-annual rate `r`,
    then annualizes: YTM = (1 + r)^2 - 1 or nominal YTM = r * 2.
    """
    if isinstance(maturity_date, str):
        mat_dt = datetime.datetime.strptime(maturity_date, "%Y-%m-%d").date()
    else:
        mat_dt = maturity_date

    if current_date is None:
        cur_dt = datetime.date.today()
    elif isinstance(current_date, str):
        cur_dt = datetime.datetime.strptime(current_date, "%Y-%m-%d").date()
    else:
        cur_dt = current_date

    days_to_mat = (mat_dt - cur_dt).days
    if days_to_mat <= 15:
        # Near or past maturity: instantaneous simple yield
        if traded_price <= 0:
            return 0.0
        return float((current_gold_spot - traded_price) / traded_price * 100.0)

    years_to_mat = days_to_mat / 365.25
    num_periods = max(1, int(round(years_to_mat * 2)))  # Semi-annual periods

    # Periodic coupon payment
    semi_coupon = (coupon_rate / 2.0) * float(issue_price)
    face_redemption = float(current_gold_spot)

    # Solve for semi-annual yield 'y' using Newton-Raphson:
    # f(y) = traded_price - sum_{k=1}^N [semi_coupon / (1 + y)^k] - [face_redemption / (1 + y)^N] = 0
    # Initial guess based on approximate yield formula:
    # y_approx = [Coupon + (Redemption - Price)/Years] / [(Redemption + Price)/2]
    annual_coupon = coupon_rate * issue_price
    approx_ann_yield = (
        annual_coupon + (face_redemption - traded_price) / years_to_mat
    ) / ((face_redemption + traded_price) / 2.0)
    y_guess = max(0.001, approx_ann_yield / 2.0)

    y = y_guess
    for _ in range(50):
        # f(y) and f'(y)
        pv = 0.0
        dpv = 0.0
        for k in range(1, num_periods + 1):
            disc = (1.0 + y) ** (-k)
            pv += semi_coupon * disc
            dpv += -k * semi_coupon * ((1.0 + y) ** (-k - 1))

        # Redemption at N
        disc_n = (1.0 + y) ** (-num_periods)
        pv += face_redemption * disc_n
        dpv += -num_periods * face_redemption * ((1.0 + y) ** (-num_periods - 1))

        f_val = traded_price - pv
        if abs(f_val) < 1e-4:
            break
        if abs(dpv) < 1e-6:
            break

        y_next = y - f_val / (-dpv)
        if y_next <= -0.5:
            y_next = y / 2.0
        if abs(y_next - y) < 1e-6:
            y = y_next
            break
        y = y_next

    # Effective annual rate: (1 + y)^2 - 1
    effective_ytm = ((1.0 + y) ** 2 - 1.0) * 100.0
    return float(round(effective_ytm, 2))


@dataclass
class SgbArbitrageOpportunity:
    """Represents an SGB secondary market arbitrage opportunity."""
    symbol: str
    traded_price: float
    spot_per_gram: float
    discount_pct: float
    ytm_pct: float
    maturity_date: str
    years_to_maturity: float
    issue_price: float
    annual_coupon_inr: float
    is_arbitrage_flagged: bool
    rationale: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "traded_price": round(self.traded_price, 2),
            "spot_per_gram": round(self.spot_per_gram, 2),
            "discount_pct": round(self.discount_pct, 2),
            "ytm_pct": round(self.ytm_pct, 2),
            "maturity_date": self.maturity_date,
            "years_to_maturity": round(self.years_to_maturity, 2),
            "issue_price": round(self.issue_price, 2),
            "annual_coupon_inr": round(self.annual_coupon_inr, 2),
            "is_arbitrage_flagged": self.is_arbitrage_flagged,
            "rationale": self.rationale,
        }


def analyze_sgb_secondary_market(
    sgb_quotes: Dict[str, float],
    gold_spot_per_gram: Optional[float] = None,
    goldbees_price: Optional[float] = None,
    discount_threshold_pct: float = 3.0,
    current_date: Optional[Union[str, datetime.date]] = None,
) -> Tuple[List[SgbArbitrageOpportunity], Optional[Dict[str, Any]]]:
    """Scan SGB secondary market tranches, evaluate discount and YTM, and flag opportunities.

    Parameters
    ----------
    sgb_quotes : Dict[str, float]
        Dictionary mapping SGB symbols to secondary market prices (e.g. {'SGBJUN28': 6900.0, 'SGBDE31I': 7000.0}).
    gold_spot_per_gram : Optional[float]
        Direct spot gold price per gram in INR.
    goldbees_price : Optional[float]
        Market price of GOLDBEES ETF used if spot is not provided.
    discount_threshold_pct : float, default 3.0
        Threshold for discount-to-spot arbitrage (e.g. >= 3.0%).
    current_date : Optional[Union[str, datetime.date]]
        Reference evaluation date.

    Returns
    -------
    Tuple[List[SgbArbitrageOpportunity], Optional[Dict[str, Any]]]
        1. List of analyzed opportunities sorted by discount descending.
        2. Execution basket payload for top arbitrage picks.
    """
    if gold_spot_per_gram is None:
        if goldbees_price is not None and goldbees_price > 0:
            gold_spot_per_gram = calculate_gold_spot_from_goldbees(goldbees_price)
        else:
            gold_spot_per_gram = 7500.0  # Conservative benchmark default

    if current_date is None:
        cur_dt = datetime.date.today()
    elif isinstance(current_date, str):
        cur_dt = datetime.datetime.strptime(current_date, "%Y-%m-%d").date()
    else:
        cur_dt = current_date

    opportunities: List[SgbArbitrageOpportunity] = []
    top_picks_orders: List[BasketOrder] = []

    for sym, price in sgb_quotes.items():
        if price <= 0:
            continue
        meta = parse_sgb_symbol_metadata(sym)
        mat_str = meta["maturity_date"]
        mat_dt = datetime.datetime.strptime(mat_str, "%Y-%m-%d").date()
        days_to_mat = max(1, (mat_dt - cur_dt).days)
        years_to_mat = days_to_mat / 365.25
        issue_price = meta.get("issue_price", 5000.0)
        coupon_rate = meta.get("coupon_rate", 0.025)
        annual_coupon = issue_price * coupon_rate

        discount_pct = compute_sgb_discount_to_spot(price, gold_spot_per_gram)
        ytm_pct = calculate_sgb_ytm(
            traded_price=price,
            maturity_date=mat_dt,
            issue_price=issue_price,
            current_gold_spot=gold_spot_per_gram,
            coupon_rate=coupon_rate,
            current_date=cur_dt,
        )

        is_flagged = discount_pct >= discount_threshold_pct
        rationale = (
            f"Trading at ₹{price:.2f} vs Spot ₹{gold_spot_per_gram:.2f} "
            f"(Discount: {discount_pct:+.2f}%, YTM: {ytm_pct:.2f}%). "
            f"Maturity: {mat_str} ({years_to_mat:.1f}y)."
        )

        opp = SgbArbitrageOpportunity(
            symbol=meta["symbol"],
            traded_price=price,
            spot_per_gram=gold_spot_per_gram,
            discount_pct=discount_pct,
            ytm_pct=ytm_pct,
            maturity_date=mat_str,
            years_to_maturity=years_to_mat,
            issue_price=issue_price,
            annual_coupon_inr=annual_coupon,
            is_arbitrage_flagged=is_flagged,
            rationale=rationale,
        )
        opportunities.append(opp)

        if is_flagged:
            top_picks_orders.append(
                BasketOrder(
                    stock_code=meta["symbol"],
                    action="BUY",
                    quantity=10,  # 10 grams default tranche allocation
                    price=price,
                    exchange="NSE",
                    order_type="LIMIT",
                )
            )

    # Sort opportunities: flagged first, then highest discount
    opportunities.sort(key=lambda x: (x.is_arbitrage_flagged, x.discount_pct, x.ytm_pct), reverse=True)

    execution_payload: Optional[Dict[str, Any]] = None
    if top_picks_orders:
        execution_payload = create_rebalance_execution_payload(
            orders=top_picks_orders,
            basket_name="SGB_Arbitrage_Basket",
        )

    return opportunities, execution_payload
