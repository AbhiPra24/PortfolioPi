"""FIFO Tax-Loss Harvest Pairing and Trade Racer Basket Payload Generation.

Under Section 70 and Section 111A of the Indian Income Tax Act:
- Short-Term Capital Gains (STCG) on equity (taxed at 20%) can be set off against
  Short-Term Capital Losses (STCL) incurred during the same assessment year.
- When an engine or rule triggers SELL_100_IMMEDIATELY or a trim on a lot incurring STCG,
  we can actively scan portfolio tax lots for legacy Stage 4 micro-loss lots (e.g. SWILIM, XCHSOL, CANBAN)
  with negative unrealized gains to harvest matching losses, neutralizing the STCG tax drag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
from typing import Any, Dict, List, Optional, Union

from algo.order_payloads import BasketOrder, generate_trade_racer_csv, generate_upi_deep_link


@dataclass
class HarvestPairingItem:
    """Represents a matched loss lot harvested to offset a gain."""
    stock_code: str
    lot_id: str
    shares_to_sell: int
    current_price: float
    buy_price: float
    loss_per_share: float
    total_loss_harvested: float


@dataclass
class HarvestPairingResult:
    """Structured result of tax-loss harvest pairing for an STCG trigger."""
    gain_stock_code: str
    gain_lot_id: str
    gain_shares_sold: int
    gain_current_price: float
    gain_buy_price: float
    stcg_gain: float
    stcg_tax_liability: float  # at STCG_RATE (20%)
    harvested_losses: List[HarvestPairingItem] = field(default_factory=list)
    total_loss_harvested: float = 0.0
    net_stcg_remaining: float = 0.0
    net_tax_saved: float = 0.0
    orders: List[BasketOrder] = field(default_factory=list)
    trade_racer_csv: str = ""
    upi_deep_link: Optional[str] = None
    settlement_notes: str = ""

    def is_fully_offset(self) -> bool:
        """Return True if harvested loss >= stcg_gain."""
        return self.total_loss_harvested >= (self.stcg_gain - 1e-4)

    def summary(self) -> str:
        """Readable textual summary for trade execution and tax reporting."""
        lines = [
            f"Tax-Harvest Pairing for {self.gain_stock_code}:",
            f"  STCG Gain: ₹{self.stcg_gain:,.2f} on {self.gain_shares_sold} shares",
            f"  STCG Tax Liability (@20%): ₹{self.stcg_tax_liability:,.2f}",
            f"  Harvested Losses: ₹{self.total_loss_harvested:,.2f} across {len(self.harvested_losses)} positions",
            f"  Net Tax Saved under Sec 70/111A: ₹{self.net_tax_saved:,.2f}",
            f"  Remaining Net STCG: ₹{self.net_stcg_remaining:,.2f}",
            f"  Orders Generated: {len(self.orders)}",
        ]
        return "\n".join(lines)


def pair_tax_loss_harvest(
    gain_lot: Dict[str, Any],
    candidate_lots: List[Dict[str, Any]],
    current_prices: Optional[Dict[str, float]] = None,
    stage_map: Optional[Dict[str, int]] = None,
    upi_payee_vpa: Optional[str] = None,
    upi_payee_name: Optional[str] = None,
) -> HarvestPairingResult:
    """Pair an STCG gain lot with candidate Stage 4 micro-loss lots under Section 70.

    Args:
        gain_lot: The lot incurring STCG to be sold/trimmed. Contains keys:
                  stock_code, quantity, buy_price, current_price (or in current_prices),
                  lot_id (optional), unrealized_gain (optional).
        candidate_lots: List of available tax lots across portfolio (e.g. from compute_fifo_tax_lots).
        current_prices: Optional mapping of stock_code -> CMP.
        stage_map: Optional mapping of stock_code -> Stage (1, 2, 3, 4). If provided,
                   Stage 4 lots are prioritized first.
        upi_payee_vpa: Optional VPA for settlement/broker link.
        upi_payee_name: Optional payee name.

    Returns:
        HarvestPairingResult containing matched loss trades and 1-tap Trade Racer CSV payload.
    """
    if current_prices is None:
        current_prices = {}
    if stage_map is None:
        stage_map = {}

    gain_code = str(gain_lot.get("stock_code") or "").upper().strip()
    gain_shares = int(gain_lot.get("quantity") or 0)
    gain_buy = float(gain_lot.get("buy_price") or 0.0)
    gain_cmp = float(current_prices.get(gain_code, gain_lot.get("current_price", gain_buy)))
    gain_lot_id = str(gain_lot.get("lot_id") or f"{gain_code}_LOT")

    gain_per_share = gain_cmp - gain_buy
    stcg_gain = max(0.0, gain_per_share * gain_shares)
    stcg_tax = stcg_gain * 0.20  # Section 111A 20% rate

    # Filter candidate lots that have unrealized losses
    # Eligible:
    # 1. unrealized_gain_per_share < 0 (or cmp < buy_price)
    # 2. Must not be the same lot as gain_lot
    # 3. Tax category typically STCG (or unclassified short-term / any STCL eligible under Section 70)
    loss_candidates: List[Dict[str, Any]] = []
    for cand in candidate_lots:
        cand_code = str(cand.get("stock_code") or "").upper().strip()
        cand_lot_id = str(cand.get("lot_id") or "")
        cand_qty = int(cand.get("quantity") or 0)
        if cand_qty <= 0:
            continue
        if cand_code == gain_code and cand_lot_id == gain_lot_id:
            continue

        cand_buy = float(cand.get("buy_price") or 0.0)
        cand_cmp = float(current_prices.get(cand_code, cand.get("current_price", cand_buy)))
        cand_loss_per_share = cand_buy - cand_cmp  # positive number for loss

        if cand_loss_per_share > 1e-4:
            stg = stage_map.get(cand_code, 4 if cand_code in {"SWILIM", "XCHSOL", "CANBAN"} else 0)
            loss_candidates.append({
                "lot": cand,
                "stock_code": cand_code,
                "lot_id": cand_lot_id or f"{cand_code}_LOT",
                "available_qty": cand_qty,
                "buy_price": cand_buy,
                "current_price": cand_cmp,
                "loss_per_share": cand_loss_per_share,
                "total_lot_loss": cand_loss_per_share * cand_qty,
                "stage": stg,
                "tax_category": cand.get("tax_category", "STCG"),
            })

    # Priority sorting:
    # 1. Stage 4 positions first (descending priority for stage == 4)
    # 2. Prefer STCG/STCL categories
    # 3. Known Stage 4 legacy losers (SWILIM, XCHSOL, CANBAN)
    # 4. Largest loss per share / largest total loss
    def _cand_sort_key(c: Dict[str, Any]):
        is_stage_4 = 1 if c["stage"] == 4 or c["stock_code"] in {"SWILIM", "XCHSOL", "CANBAN"} else 0
        is_stcg = 1 if c.get("tax_category") == "STCG" else 0
        return (-is_stage_4, -is_stcg, -c["total_lot_loss"])

    loss_candidates.sort(key=_cand_sort_key)

    needed_loss = stcg_gain
    harvested_items: List[HarvestPairingItem] = []
    total_loss_harvested = 0.0

    for c in loss_candidates:
        if needed_loss <= 1e-4:
            break

        loss_per_share = c["loss_per_share"]
        if loss_per_share <= 0:
            continue

        # Shares needed to cover remaining needed_loss
        shares_needed = math.ceil(needed_loss / loss_per_share)
        shares_to_sell = min(c["available_qty"], shares_needed)

        if shares_to_sell <= 0:
            continue

        loss_realized = round(shares_to_sell * loss_per_share, 2)
        total_loss_harvested += loss_realized
        needed_loss = max(0.0, needed_loss - loss_realized)

        harvested_items.append(
            HarvestPairingItem(
                stock_code=c["stock_code"],
                lot_id=c["lot_id"],
                shares_to_sell=shares_to_sell,
                current_price=round(c["current_price"], 2),
                buy_price=round(c["buy_price"], 2),
                loss_per_share=round(loss_per_share, 2),
                total_loss_harvested=loss_realized,
            )
        )

    net_stcg_remaining = max(0.0, round(stcg_gain - total_loss_harvested, 2))
    # Tax saved is min(stcg_gain, total_loss_harvested) * 20%
    offset_amount = min(stcg_gain, total_loss_harvested)
    net_tax_saved = round(offset_amount * 0.20, 2)

    # Build Trade Racer Orders
    # 1. Exit/trim order for the gain lot
    orders: List[BasketOrder] = []
    if gain_shares > 0:
        orders.append(
            BasketOrder(
                stock_code=gain_code,
                action="SELL",
                quantity=gain_shares,
                price=round(gain_cmp, 2),
                exchange="NSE",
                order_type="LIMIT",
                validity="DAY",
            )
        )

    # 2. Sell orders for each loss lot
    for h in harvested_items:
        orders.append(
            BasketOrder(
                stock_code=h.stock_code,
                action="SELL",
                quantity=h.shares_to_sell,
                price=h.current_price,
                exchange="NSE",
                order_type="LIMIT",
                validity="DAY",
            )
        )

    trade_racer_csv = generate_trade_racer_csv(orders)

    # UPI deep-link generation if requested
    upi_link = None
    if upi_payee_vpa and upi_payee_name:
        # Total proceeds expected from both gain and loss sales
        total_proceeds = round((gain_shares * gain_cmp) + sum(h.shares_to_sell * h.current_price for h in harvested_items), 2)
        upi_link = generate_upi_deep_link(
            payee_vpa=upi_payee_vpa,
            payee_name=upi_payee_name,
            amount=total_proceeds,
            currency="INR",
            note=f"Harvest Pair {gain_code}",
        )

    settlement_notes = (
        f"Pairing STCG in {gain_code} (gain: ₹{stcg_gain:,.2f}) with "
        f"{len(harvested_items)} Stage 4 tax-loss lot(s) (loss: ₹{total_loss_harvested:,.2f}). "
        f"Section 70 offset: ₹{offset_amount:,.2f}. Tax saved: ₹{net_tax_saved:,.2f}."
    )

    return HarvestPairingResult(
        gain_stock_code=gain_code,
        gain_lot_id=gain_lot_id,
        gain_shares_sold=gain_shares,
        gain_current_price=gain_cmp,
        gain_buy_price=gain_buy,
        stcg_gain=round(stcg_gain, 2),
        stcg_tax_liability=round(stcg_tax, 2),
        harvested_losses=harvested_items,
        total_loss_harvested=round(total_loss_harvested, 2),
        net_stcg_remaining=net_stcg_remaining,
        net_tax_saved=net_tax_saved,
        orders=orders,
        trade_racer_csv=trade_racer_csv,
        upi_deep_link=upi_link,
        settlement_notes=settlement_notes,
    )


@dataclass
class Section112AHarvestItem:
    """Represents a lot harvested and cost-basis reset under Section 112A."""
    stock_code: str
    lot_id: str
    shares: int
    original_cost: float
    cmp: float
    unrealized_ltcg: float
    new_cost_basis: float
    tax_saved: float


@dataclass
class Section112AHarvestResult:
    """Structured result of Section 112A tax-gain harvest and cost-basis reset."""
    total_ltcg_harvested: float = 0.0
    total_tax_saved: float = 0.0  # @ 12.5%
    available_exemption_remaining: float = 125000.0
    harvested_items: List[Section112AHarvestItem] = field(default_factory=list)
    sell_orders: List[BasketOrder] = field(default_factory=list)
    buy_orders: List[BasketOrder] = field(default_factory=list)
    trade_racer_csv: str = ""

    def summary(self) -> str:
        """Readable summary for Section 112A harvest reset."""
        lines = [
            "Section 112A Tax-Gain Harvest Reset Summary:",
            f"  Total LTCG Harvested: ₹{self.total_ltcg_harvested:,.2f}",
            f"  Tax Saved (@12.5%): ₹{self.total_tax_saved:,.2f}",
            f"  Available Exemption Remaining: ₹{self.available_exemption_remaining:,.2f}",
            f"  Positions Reset: {len(self.harvested_items)}",
            f"  SELL Orders: {len(self.sell_orders)}",
            f"  BUY Orders: {len(self.buy_orders)}",
        ]
        return "\n".join(lines)


def plan_section_112a_harvest_reset(
    tax_lots: List[Dict[str, Any]],
    ytd_realized_ltcg: float = 0.0,
    current_prices: Optional[Dict[str, float]] = None,
    annual_exemption: float = 125000.0,
    as_of_date: Optional[date] = None,
    buffer_pct: float = 0.003,
) -> Section112AHarvestResult:
    """Plan Automated Section 112A Tax-Gain Harvest Reset.

    Under Section 112A of the Indian Income Tax Act:
    - Long-Term Capital Gains (LTCG) on equity up to ₹1,25,000 per financial year is taxed at 0%.
    - Gains above ₹1,25,000 are taxed at 12.5%.
    - By executing a paired same-day SELL and BUY of long-term winning lots within the annual
      exemption limit, an investor resets the acquisition cost basis to the current market price (CMP)
      with zero net tax liability, permanently locking in tax-free appreciation and reducing future tax drag.

    Args:
        tax_lots: List of tax lot dicts (e.g. from compute_fifo_tax_lots).
        ytd_realized_ltcg: Cumulative LTCG already realized in current FY.
        current_prices: Optional mapping of stock_code -> CMP.
        annual_exemption: Threshold limit under Sec 112A (default ₹1,25,000).
        as_of_date: Evaluation date for holding periods.
        buffer_pct: Limit price execution buffer (default 0.3% / 0.003) for paired SELL/BUY.

    Returns:
        Section112AHarvestResult with paired SELL/BUY orders and Trade Racer CSV payload.
    """
    if current_prices is None:
        current_prices = {}
    if as_of_date is None:
        as_of_date = date.today()

    available_exemption = max(0.0, float(annual_exemption) - float(ytd_realized_ltcg))
    if available_exemption <= 1e-4:
        return Section112AHarvestResult(
            total_ltcg_harvested=0.0,
            total_tax_saved=0.0,
            available_exemption_remaining=0.0,
            harvested_items=[],
            sell_orders=[],
            buy_orders=[],
            trade_racer_csv="",
        )

    # 1. Filter candidate lots
    candidates: List[Dict[str, Any]] = []
    for lot in tax_lots:
        code = str(lot.get("stock_code") or "").upper().strip()
        qty = int(lot.get("quantity") or 0)
        if qty <= 0:
            continue

        tax_cat = str(lot.get("tax_category") or "").upper().strip()
        holding_days = lot.get("holding_days")
        if holding_days is None:
            # Try computing from buy_date and as_of_date if available
            buy_d = lot.get("buy_date")
            if buy_d:
                from algo.tax_lots import _parse_date
                parsed_d = _parse_date(buy_d)
                if parsed_d:
                    holding_days = (as_of_date - parsed_d).days

        is_ltcg = (tax_cat == "LTCG") or (holding_days is not None and holding_days > 365)
        if not is_ltcg:
            continue

        buy_price = float(lot.get("buy_price") or 0.0)
        cmp_val = float(current_prices.get(code, lot.get("current_price", buy_price)))
        gain_per_share = cmp_val - buy_price

        # Unrealized gains must be > 0
        if gain_per_share <= 1e-4:
            continue

        unrealized_gain = round(gain_per_share * qty, 2)
        gain_pct = (gain_per_share / buy_price) if buy_price > 0 else 0.0

        candidates.append({
            "lot": lot,
            "stock_code": code,
            "lot_id": str(lot.get("lot_id") or f"{code}_LOT"),
            "quantity": qty,
            "buy_price": buy_price,
            "cmp": cmp_val,
            "gain_per_share": gain_per_share,
            "unrealized_gain": unrealized_gain,
            "gain_pct": gain_pct,
        })

    # Sort by highest unrealized gain % or highest dollar gain (prefer highest dollar gain, then highest gain %)
    candidates.sort(key=lambda c: (-c["unrealized_gain"], -c["gain_pct"]))

    remaining_exemption = available_exemption
    harvested_items: List[Section112AHarvestItem] = []
    sell_orders: List[BasketOrder] = []
    buy_orders: List[BasketOrder] = []

    for c in candidates:
        if remaining_exemption <= 1e-4:
            break

        gain_per_share = c["gain_per_share"]
        if gain_per_share <= 1e-4:
            continue

        # Determine shares to allocate such that harvested gain <= remaining_exemption
        # shares_alloc = floor(remaining_exemption / gain_per_share)
        max_shares_for_cap = int(math.floor(remaining_exemption / gain_per_share))
        shares_to_harvest = min(c["quantity"], max_shares_for_cap)

        if shares_to_harvest <= 0:
            continue

        lot_gain = round(shares_to_harvest * gain_per_share, 2)
        remaining_exemption = max(0.0, round(remaining_exemption - lot_gain, 2))

        # Tax saved under Section 112A is 12.5% of the harvested gain (which is tax-free)
        lot_tax_saved = round(lot_gain * 0.125, 2)

        # New cost basis is the current market price CMP (per share)
        # and total new cost basis is shares_to_harvest * CMP
        new_cost_basis_total = round(shares_to_harvest * c["cmp"], 2)
        orig_cost_total = round(shares_to_harvest * c["buy_price"], 2)

        harvested_items.append(
            Section112AHarvestItem(
                stock_code=c["stock_code"],
                lot_id=c["lot_id"],
                shares=shares_to_harvest,
                original_cost=orig_cost_total,
                cmp=round(c["cmp"], 2),
                unrealized_ltcg=lot_gain,
                new_cost_basis=new_cost_basis_total,
                tax_saved=lot_tax_saved,
            )
        )

        sell_price = round(c["cmp"] * (1.0 - buffer_pct), 2)
        buy_price = round(c["cmp"] * (1.0 + buffer_pct), 2)

        sell_orders.append(
            BasketOrder(
                stock_code=c["stock_code"],
                action="SELL",
                quantity=shares_to_harvest,
                price=sell_price,
                exchange="NSE",
                order_type="LIMIT",
                validity="DAY",
            )
        )
        buy_orders.append(
            BasketOrder(
                stock_code=c["stock_code"],
                action="BUY",
                quantity=shares_to_harvest,
                price=buy_price,
                exchange="NSE",
                order_type="LIMIT",
                validity="DAY",
            )
        )

    total_ltcg_harvested = round(sum(item.unrealized_ltcg for item in harvested_items), 2)
    total_tax_saved = round(total_ltcg_harvested * 0.125, 2)
    available_exemption_remaining = round(available_exemption - total_ltcg_harvested, 2)

    # Combined Trade Racer CSV with SELL orders first, followed by BUY orders
    all_orders = sell_orders + buy_orders
    trade_racer_csv = generate_trade_racer_csv(all_orders) if all_orders else ""

    return Section112AHarvestResult(
        total_ltcg_harvested=total_ltcg_harvested,
        total_tax_saved=total_tax_saved,
        available_exemption_remaining=available_exemption_remaining,
        harvested_items=harvested_items,
        sell_orders=sell_orders,
        buy_orders=buy_orders,
        trade_racer_csv=trade_racer_csv,
    )
