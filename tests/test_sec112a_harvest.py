"""Unit tests for Section 112A Tax-Gain Harvest Reset and Trade Racer Basket generation."""

from datetime import date
import pytest

from algo.order_payloads import parse_trade_racer_csv
from algo.tax_lots import plan_section_112a_harvest_reset
from algo.tax_pairing import (
    Section112AHarvestItem,
    Section112AHarvestResult,
)


def test_section_112a_available_exemption_cap():
    """Verify available exemption calculation under varying YTD LTCG realized amounts."""
    tax_lots = [
        {
            "stock_code": "INFY",
            "lot_id": "INFY_L1",
            "quantity": 100,
            "buy_price": 1000.0,
            "current_price": 2500.0,  # gain = 1500 * 100 = 1,50,000
            "tax_category": "LTCG",
        }
    ]

    # Case 1: YTD = 0, full 1.25L available
    res1 = plan_section_112a_harvest_reset(tax_lots, ytd_realized_ltcg=0.0)
    assert res1.available_exemption_remaining >= 0.0
    # 1500/share -> max shares = floor(125000 / 1500) = 83 shares
    # 83 * 1500 = 1,24,500 <= 1,25,000
    assert len(res1.harvested_items) == 1
    assert res1.harvested_items[0].shares == 83
    assert res1.total_ltcg_harvested == 124500.0
    assert res1.total_tax_saved == round(124500.0 * 0.125, 2)
    assert res1.available_exemption_remaining == 500.0

    # Case 2: YTD = 50,000 -> Available = 75,000
    res2 = plan_section_112a_harvest_reset(tax_lots, ytd_realized_ltcg=50000.0)
    # floor(75000 / 1500) = 50 shares
    # 50 * 1500 = 75,000
    assert res2.harvested_items[0].shares == 50
    assert res2.total_ltcg_harvested == 75000.0
    assert res2.available_exemption_remaining == 0.0
    assert res2.total_tax_saved == round(75000.0 * 0.125, 2)

    # Case 3: YTD = 1,25,000 or higher -> Available = 0
    res3 = plan_section_112a_harvest_reset(tax_lots, ytd_realized_ltcg=125000.0)
    assert res3.total_ltcg_harvested == 0.0
    assert res3.available_exemption_remaining == 0.0
    assert len(res3.harvested_items) == 0
    assert len(res3.sell_orders) == 0
    assert len(res3.buy_orders) == 0


def test_section_112a_filter_lots_stcg_and_loss_excluded():
    """Verify STCG lots and loss-making LTCG lots are strictly ignored."""
    tax_lots = [
        {
            "stock_code": "TCS",
            "lot_id": "TCS_STCG",
            "quantity": 100,
            "buy_price": 3000.0,
            "current_price": 4000.0,
            "tax_category": "STCG",  # Should be excluded (STCG)
            "holding_days": 100,
        },
        {
            "stock_code": "HDFCBANK",
            "lot_id": "HDFC_LOSS",
            "quantity": 100,
            "buy_price": 1700.0,
            "current_price": 1500.0,  # Should be excluded (Loss)
            "tax_category": "LTCG",
            "holding_days": 400,
        },
        {
            "stock_code": "RELIANCE",
            "lot_id": "RELIANCE_LTCG",
            "quantity": 50,
            "buy_price": 2000.0,
            "current_price": 2500.0,  # Gain = 500 * 50 = 25,000
            "tax_category": "LTCG",
            "holding_days": 500,
        },
    ]

    result = plan_section_112a_harvest_reset(tax_lots, ytd_realized_ltcg=0.0)
    assert len(result.harvested_items) == 1
    assert result.harvested_items[0].stock_code == "RELIANCE"
    assert result.harvested_items[0].shares == 50
    assert result.total_ltcg_harvested == 25000.0
    assert result.total_tax_saved == 3125.0  # 12.5% of 25,000


def test_section_112a_paired_orders_and_csv_generation():
    """Verify paired same-day SELL and BUY orders with limit buffer and CSV output."""
    tax_lots = [
        {
            "stock_code": "ICICIBANK",
            "lot_id": "ICICI_L1",
            "quantity": 40,
            "buy_price": 800.0,
            "current_price": 1000.0,  # gain = 200 * 40 = 8,000
            "holding_days": 450,
        }
    ]

    buffer_pct = 0.005  # 0.5%
    result = plan_section_112a_harvest_reset(
        tax_lots,
        ytd_realized_ltcg=0.0,
        buffer_pct=buffer_pct,
    )

    assert len(result.sell_orders) == 1
    assert len(result.buy_orders) == 1

    sell_order = result.sell_orders[0]
    buy_order = result.buy_orders[0]

    assert sell_order.stock_code == "ICICIBANK"
    assert sell_order.action == "SELL"
    assert sell_order.quantity == 40
    # CMP * (1 - 0.005) = 1000 * 0.995 = 995.0
    assert sell_order.price == 995.0
    assert sell_order.order_type == "LIMIT"

    assert buy_order.stock_code == "ICICIBANK"
    assert buy_order.action == "BUY"
    assert buy_order.quantity == 40
    # CMP * (1 + 0.005) = 1000 * 1.005 = 1005.0
    assert buy_order.price == 1005.0
    assert buy_order.order_type == "LIMIT"

    # Verify CSV payload contains both SELL and BUY
    assert result.trade_racer_csv != ""
    rows = parse_trade_racer_csv(result.trade_racer_csv)
    assert len(rows) == 2
    assert rows[0].action == "SELL"
    assert rows[0].quantity == 40
    assert rows[0].price == 995.0

    assert rows[1].action == "BUY"
    assert rows[1].quantity == 40
    assert rows[1].price == 1005.0


def test_section_112a_new_cost_basis_and_summary():
    """Verify new cost basis is reset to CMP and tax saved is calculated accurately."""
    tax_lots = [
        {
            "stock_code": "BHARTIARTL",
            "lot_id": "BHARTI_L1",
            "quantity": 100,
            "buy_price": 900.0,
            "tax_category": "LTCG",
        }
    ]
    current_prices = {"BHARTIARTL": 1400.0}  # gain = 500 * 100 = 50,000

    result = plan_section_112a_harvest_reset(
        tax_lots,
        ytd_realized_ltcg=25000.0,
        current_prices=current_prices,
        annual_exemption=125000.0,
    )

    assert result.total_ltcg_harvested == 50000.0
    assert result.total_tax_saved == 6250.0  # 12.5% of 50,000
    assert result.available_exemption_remaining == 50000.0  # 125000 - 25000 - 50000 = 50000

    item = result.harvested_items[0]
    assert item.stock_code == "BHARTIARTL"
    assert item.shares == 100
    assert item.original_cost == 90000.0
    assert item.cmp == 1400.0
    assert item.new_cost_basis == 140000.0  # Reset to 100 * 1400.0
    assert item.tax_saved == 6250.0

    summary_str = result.summary()
    assert "Section 112A Tax-Gain Harvest Reset Summary" in summary_str
    assert "₹50,000.00" in summary_str
    assert "₹6,250.00" in summary_str
