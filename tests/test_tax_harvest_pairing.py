"""Unit tests for Automated FIFO Tax-Loss Harvest Pairing and Trade Racer Basket generation."""

from algo.order_payloads import parse_trade_racer_csv
from algo.tax_lots import evaluate_tax_hurdle_rule
from algo.tax_pairing import (
    HarvestPairingResult,
    pair_tax_loss_harvest,
)


def test_pair_tax_loss_harvest_basic_offset():
    """Verify STCG gain is paired with exact shares of Stage 4 loss lot under Section 70."""
    gain_lot = {
        "stock_code": "GOLDEX",
        "lot_id": "GOLDEX_L1",
        "quantity": 100,
        "buy_price": 50.0,
        "current_price": 70.0,  # gain = 20 * 100 = 2000
    }
    candidate_lots = [
        {
            "stock_code": "SWILIM",
            "lot_id": "SWI_L1",
            "quantity": 500,
            "buy_price": 100.0,
            "current_price": 80.0,  # loss = 20/share
            "tax_category": "STCG",
        }
    ]

    result = pair_tax_loss_harvest(gain_lot=gain_lot, candidate_lots=candidate_lots)

    assert isinstance(result, HarvestPairingResult)
    assert result.gain_stock_code == "GOLDEX"
    assert result.stcg_gain == 2000.0
    assert result.stcg_tax_liability == 400.0  # 20% of 2000

    # Needed loss = 2000. SWILIM loss per share = 20. Shares needed = 100
    assert len(result.harvested_losses) == 1
    h_item = result.harvested_losses[0]
    assert h_item.stock_code == "SWILIM"
    assert h_item.shares_to_sell == 100
    assert h_item.loss_per_share == 20.0
    assert h_item.total_loss_harvested == 2000.0

    assert result.total_loss_harvested == 2000.0
    assert result.net_stcg_remaining == 0.0
    assert result.net_tax_saved == 400.0
    assert result.is_fully_offset() is True


def test_pair_tax_loss_partial_trim_multiple_lots():
    """Verify pairing across multiple Stage 4 loss candidates when single lot is insufficient."""
    gain_lot = {
        "stock_code": "NIPSIL",
        "lot_id": "NIPSIL_L1",
        "quantity": 50,
        "buy_price": 100.0,
        "current_price": 200.0,  # gain = 100 * 50 = 5000
    }
    # Set candidate lots where CANBAN alone is not enough (e.g. 100 shares * 30 = 3000 max loss)
    # and XCHSOL has 100 shares * 20 = 2000 max loss
    # Total needed = 5000, so CANBAN (3000) + XCHSOL (2000) are both needed.
    candidate_lots = [
        {
            "stock_code": "XCHSOL",
            "lot_id": "XCH_L1",
            "quantity": 100,
            "buy_price": 50.0,
            "current_price": 30.0,  # loss = 20/share * 100 = 2000
            "tax_category": "STCG",
        },
        {
            "stock_code": "CANBAN",
            "lot_id": "CAN_L1",
            "quantity": 100,
            "buy_price": 120.0,
            "current_price": 90.0,  # loss = 30/share * 100 = 3000
            "tax_category": "STCG",
        },
    ]

    stage_map = {"XCHSOL": 4, "CANBAN": 4}
    result = pair_tax_loss_harvest(
        gain_lot=gain_lot,
        candidate_lots=candidate_lots,
        stage_map=stage_map,
    )

    assert result.stcg_gain == 5000.0
    assert len(result.harvested_losses) == 2

    # Verify both lots contributed
    h_codes = [h.stock_code for h in result.harvested_losses]
    assert "XCHSOL" in h_codes
    assert "CANBAN" in h_codes

    assert result.total_loss_harvested == 5000.0
    assert result.net_stcg_remaining == 0.0
    assert result.net_tax_saved == 1000.0  # 20% of 5000


def test_trade_racer_csv_payload_generation():
    """Verify 1-tap Trade Racer CSV payload contains both gain exit order and loss harvest orders."""
    gain_lot = {
        "stock_code": "TCS",
        "quantity": 10,
        "buy_price": 3000.0,
        "current_price": 3500.0,  # gain = 5000
    }
    candidate_lots = [
        {
            "stock_code": "SWILIM",
            "lot_id": "LOT_SWI",
            "quantity": 100,
            "buy_price": 250.0,
            "current_price": 200.0,  # loss = 50/share
        }
    ]

    result = pair_tax_loss_harvest(
        gain_lot=gain_lot,
        candidate_lots=candidate_lots,
        upi_payee_vpa="broker@icici",
        upi_payee_name="ICICI Direct",
    )

    # 10 shares of TCS (gain exit) + 100 shares of SWILIM (loss harvest)
    assert len(result.orders) == 2
    assert (
        "Exchange,StockCode,Action,OrderType,Validity,Quantity,Price,DisclosedQuantity,TriggerPrice"
        in result.trade_racer_csv
    )

    parsed_orders = parse_trade_racer_csv(result.trade_racer_csv)
    assert len(parsed_orders) == 2

    assert parsed_orders[0].stock_code == "TCS"
    assert parsed_orders[0].action == "SELL"
    assert parsed_orders[0].quantity == 10
    assert parsed_orders[0].price == 3500.0

    assert parsed_orders[1].stock_code == "SWILIM"
    assert parsed_orders[1].action == "SELL"
    assert parsed_orders[1].quantity == 100
    assert parsed_orders[1].price == 200.0

    assert result.upi_deep_link is not None
    assert "upi://pay" in result.upi_deep_link
    assert "broker%40icici" in result.upi_deep_link or "broker@icici" in result.upi_deep_link


def test_evaluate_tax_hurdle_rule_triggers_harvest_pairing():
    """Verify evaluate_tax_hurdle_rule triggers SELL_100_IMMEDIATELY and attaches harvest_pairing."""
    gain_lot = {
        "stock_code": "INFY",
        "quantity": 20,
        "buy_price": 1000.0,
        "current_price": 1500.0,
        "tax_category": "STCG",
        "unrealized_gain": 10000.0,
    }
    candidate_lots = [
        {
            "stock_code": "CANBAN",
            "quantity": 200,
            "buy_price": 150.0,
            "current_price": 100.0,  # loss = 50/share
        }
    ]

    eval_res = evaluate_tax_hurdle_rule(
        lot=gain_lot,
        base_action="SELL / STOP SIP",
        stage=4,
        hard_stop_breached=True,
        candidate_lots=candidate_lots,
    )

    assert eval_res["tax_action"] == "SELL_100_IMMEDIATELY"
    assert "harvest_pairing" in eval_res
    pairing: HarvestPairingResult = eval_res["harvest_pairing"]
    assert pairing.gain_stock_code == "INFY"
    assert pairing.total_loss_harvested == 10000.0
    assert pairing.net_tax_saved == 2000.0
    assert "Paired with 1 Stage 4 loss lot(s)" in eval_res["rationale"]
