"""Unit tests for FIFO Tax-Lot Matching Engine, Tax-Conversion Hurdle, and Two-Tier Tax Hurdle Matrix."""

from datetime import date, timedelta
import pytest

from algo.tax_lots import (
    LTCG_ANNUAL_EXEMPTION_LIMIT,
    compute_fifo_tax_lots,
    compute_tax_conversion_hurdle,
    evaluate_tax_hurdle_rule,
)


def test_compute_fifo_tax_lots_basic():
    as_of = date(2026, 11, 9)
    trades = [
        {"trade_id": "T1", "stock_code": "INFY", "action": "Buy", "quantity": 100, "price": 1400.0, "trade_date": "2025-11-01"},
        {"trade_id": "T2", "stock_code": "INFY", "action": "Buy", "quantity": 50, "price": 1500.0, "trade_date": "2026-05-01"},
        {"trade_id": "T3", "stock_code": "INFY", "action": "Sell", "quantity": 120, "price": 1600.0, "trade_date": "2026-06-01"},
    ]
    current_prices = {"INFY": 1700.0}

    lots = compute_fifo_tax_lots(trades, current_prices=current_prices, as_of_date=as_of)

    # 100 bought @ 1400 closed completely (120 sold - 100 = 20 remaining to close from 2nd lot)
    # 2nd lot: 50 - 20 = 30 remaining
    assert len(lots) == 1
    lot = lots[0]
    assert lot["stock_code"] == "INFY"
    assert lot["quantity"] == 30
    assert lot["buy_price"] == 1500.0
    assert lot["current_price"] == 1700.0
    assert lot["unrealized_gain_per_share"] == 200.0
    assert lot["unrealized_gain"] == 6000.0

    # Bought 2026-05-01, as_of 2026-11-09 -> 192 days held -> STCG
    assert lot["holding_days"] == 192
    assert lot["tax_category"] == "STCG"
    assert lot["days_to_ltcg"] == 365 - 192
    assert lot["price_cushion_required"] > 0


def test_compute_fifo_tax_lots_ltcg_and_cushion():
    as_of = date(2026, 11, 9)
    trades = [
        {"trade_id": "B1", "stock_code": "TCS", "action": "Buy", "quantity": 10, "price": 3000.0, "trade_date": "2024-01-01"},
    ]
    current_prices = {"TCS": 4000.0}

    lots = compute_fifo_tax_lots(trades, current_prices=current_prices, as_of_date=as_of)
    assert len(lots) == 1
    lot = lots[0]
    assert lot["tax_category"] == "LTCG"
    assert lot["days_to_ltcg"] == 0
    assert lot["price_cushion_required"] == 0.0  # Already LTCG, no cushion needed to convert


def test_compute_tax_conversion_hurdle():
    # If unrealized gain is 50,000, market value is 200,000, YTD LTCG is 0
    # Remaining exemption is 1.25L, so LTCG tax would be 0!
    # STCG tax would be 20% * 50,000 = 10,000.
    # Tax saved = 10,000.
    # Hurdle score = (10,000 / 200,000) * 100 = 5.0%
    score = compute_tax_conversion_hurdle(
        unrealized_gain=50000.0,
        current_market_value=200000.0,
        ytd_realized_ltcg=0.0,
    )
    assert score == 5.0

    # With zero or negative gains
    assert compute_tax_conversion_hurdle(0.0, 100000.0) == 0.0
    assert compute_tax_conversion_hurdle(-5000.0, 100000.0) == 0.0


def test_two_tier_tax_hurdle_rule_matrix():
    # Rule 1: Hard stop breach / Stage 4 -> SELL_100_IMMEDIATELY
    lot_stcg = {
        "stock_code": "RELIANCE",
        "tax_category": "STCG",
        "days_to_ltcg": 15,
        "price_cushion_required": 3.0,
        "unrealized_gain": 10000.0,
    }
    r1 = evaluate_tax_hurdle_rule(
        lot=lot_stcg,
        base_action="TRIM / PAUSE SIP",
        stage=4,
        hard_stop_breached=False,
    )
    assert r1["tax_action"] == "SELL_100_IMMEDIATELY"
    assert r1["decision"] == "EXIT"

    r1_stop = evaluate_tax_hurdle_rule(
        lot=lot_stcg,
        base_action="TRIM / PAUSE SIP",
        stage=2,
        hard_stop_breached=True,
    )
    assert r1_stop["tax_action"] == "SELL_100_IMMEDIATELY"

    # Rule 2: Discretionary TRIM with STCG and days_to_ltcg <= 30 and price cushion >= 2.5% -> DEFER_TRIM_TAX_HOLD
    r2 = evaluate_tax_hurdle_rule(
        lot=lot_stcg,
        base_action="TRIM / PAUSE SIP",
        stage=3,
        hard_stop_breached=False,
    )
    assert r2["tax_action"] == "DEFER_TRIM_TAX_HOLD"
    assert r2["decision"] == "HOLD"

    # Rule 3: Discretionary TRIM with STCG and days_to_ltcg > 30 and realized STCL available -> PAIRED_TAX_NEUTRAL_TRIM
    lot_stcg_far = {
        "stock_code": "RELIANCE",
        "tax_category": "STCG",
        "days_to_ltcg": 90,
        "price_cushion_required": 3.0,
        "unrealized_gain": 10000.0,
    }
    r3 = evaluate_tax_hurdle_rule(
        lot=lot_stcg_far,
        base_action="TRIM / PAUSE SIP",
        stage=3,
        hard_stop_breached=False,
        realized_stcl_available=True,
    )
    assert r3["tax_action"] == "PAIRED_TAX_NEUTRAL_TRIM"
    assert r3["decision"] == "TRIM"

    # Rule 4: Discretionary TRIM with LTCG and remaining Section 112A exemption > 0 -> PRIORITY_TAX_FREE_TRIM
    lot_ltcg = {
        "stock_code": "HDFCBANK",
        "tax_category": "LTCG",
        "days_to_ltcg": 0,
        "price_cushion_required": 0.0,
        "unrealized_gain": 25000.0,
    }
    r4 = evaluate_tax_hurdle_rule(
        lot=lot_ltcg,
        base_action="TRIM / PAUSE SIP",
        stage=3,
        hard_stop_breached=False,
        remaining_ltcg_exemption=50000.0,
    )
    assert r4["tax_action"] == "PRIORITY_TAX_FREE_TRIM"
    assert r4["decision"] == "TRIM"

    # Rule 5: Fiscal year-end March 15-31 audit: GAIN_HARVEST_RESET if unused exemption >= 10,000
    r5 = evaluate_tax_hurdle_rule(
        lot=lot_ltcg,
        base_action="HOLD",
        stage=2,
        hard_stop_breached=False,
        remaining_ltcg_exemption=20000.0,
        as_of_date=date(2027, 3, 20),
    )
    assert r5["tax_action"] == "GAIN_HARVEST_RESET"
    assert r5["decision"] == "HARVEST"
