"""Unit tests for One-Tap Execution Payloads, Cash Drag Sweeps, and SGB Arbitrage Engine."""

import pytest

from algo.cash_sweeper import (
    SweepTargetType,
    evaluate_cash_drag,
)
from algo.order_payloads import (
    BasketOrder,
    create_rebalance_execution_payload,
    generate_trade_racer_csv,
    generate_upi_deep_link,
    parse_trade_racer_csv,
)
from algo.sgb_arbitrage import (
    analyze_sgb_secondary_market,
    calculate_gold_spot_from_goldbees,
    calculate_sgb_ytm,
    compute_sgb_discount_to_spot,
    parse_sgb_symbol_metadata,
)


class TestOrderPayloads:
    """Test ICICI Trade Racer CSV and UPI payment deep-link generation."""

    def test_trade_racer_csv_format(self):
        orders = [
            BasketOrder(
                exchange="NSE",
                stock_code="FEDERALBNK",
                action="BUY",
                order_type="LIMIT",
                validity="DAY",
                quantity=50,
                price=205.50,
                disclosed_quantity=0,
                trigger_price=0.0,
            ),
            BasketOrder(
                exchange="NSE",
                stock_code="BEL",
                action="SELL",
                order_type="LIMIT",
                validity="DAY",
                quantity=20,
                price=290.00,
            ),
        ]

        csv_text = generate_trade_racer_csv(orders)
        lines = [line.strip() for line in csv_text.strip().split("\n")]
        assert lines[0] == "Exchange,StockCode,Action,OrderType,Validity,Quantity,Price,DisclosedQuantity,TriggerPrice"
        assert lines[1] == "NSE,FEDERALBNK,BUY,LIMIT,DAY,50,205.50,0,0"
        assert lines[2] == "NSE,BEL,SELL,LIMIT,DAY,20,290.00,0,0"

    def test_parse_trade_racer_csv(self):
        sample_csv = (
            "Exchange,StockCode,Action,OrderType,Validity,Quantity,Price,DisclosedQuantity,TriggerPrice\n"
            "NSE,FEDERALBNK,BUY,LIMIT,DAY,50,205.50,0,0\n"
        )
        parsed = parse_trade_racer_csv(sample_csv)
        assert len(parsed) == 1
        assert parsed[0].stock_code == "FEDERALBNK"
        assert parsed[0].action == "BUY"
        assert parsed[0].quantity == 50
        assert parsed[0].price == 205.50

    def test_upi_deep_link_generation(self):
        link = generate_upi_deep_link(
            payee_vpa="icicidirect@icici",
            payee_name="ICICI Direct",
            amount=15000.50,
            note="Portfolio Rebalance Nov 2026",
            transaction_ref="ORD12345",
        )
        assert link.startswith("upi://pay?")
        assert "pa=icicidirect%40icici" in link
        assert "pn=ICICI%20Direct" in link
        assert "am=15000.50" in link
        assert "cu=INR" in link
        assert "tr=ORD12345" in link

    def test_create_rebalance_execution_payload(self):
        orders = [
            BasketOrder(stock_code="TATAMOTORS", action="BUY", quantity=10, price=800.0),
            BasketOrder(stock_code="INFY", action="SELL", quantity=2, price=1500.0),
        ]
        # Net required: (10 * 800) - (2 * 1500) = 8000 - 3000 = 5000
        payload = create_rebalance_execution_payload(orders, basket_name="Weekly_Rebalance")
        assert payload["total_orders"] == 2
        assert payload["total_buy_value"] == 8000.0
        assert payload["total_sell_value"] == 3000.0
        assert payload["net_capital_required"] == 5000.0
        assert payload["upi_mandate"] is not None
        assert "am=5000.00" in payload["upi_mandate"]["deep_link"]


class TestCashDragSweeper:
    """Test cash drag detection and allocation sweep directives."""

    def test_idle_cash_below_threshold(self):
        report = evaluate_cash_drag(
            unallocated_cash=2000.0,
            accrued_dividends=1500.0,
            paused_sip_budget=500.0,
            threshold=5000.0,
        )
        assert report.total_idle_cash == 4000.0
        assert not report.sweep_triggered
        assert report.option_a_directive is None
        assert report.option_b_directive is None

    def test_idle_cash_exceeds_threshold_generates_options(self):
        candidates = [
            {"symbol": "FEDERALBNK", "price": 200.0, "stage": 2, "rs_score": 88.0},
            {"symbol": "BEL", "price": 300.0, "stage": 2, "rs_score": 82.0},
        ]
        report = evaluate_cash_drag(
            unallocated_cash=6000.0,
            accrued_dividends=1000.0,
            paused_sip_budget=1000.0,
            threshold=5000.0,
            stage2_candidates=candidates,
            liquid_etf_symbol="LIQUIDBEES",
            liquid_etf_price=1000.0,
        )
        assert report.total_idle_cash == 8000.0
        assert report.sweep_triggered

        # Option A check: FEDERALBNK at 200 => 40 shares
        opt_a = report.option_a_directive
        assert opt_a is not None
        assert opt_a.target_type == SweepTargetType.STAGE2_COMPOUNDER
        assert opt_a.symbol == "FEDERALBNK"
        assert opt_a.quantity == 40
        assert opt_a.allocation_amount == 8000.0
        assert report.execution_payload_option_a is not None
        assert "FEDERALBNK" in report.execution_payload_option_a["csv_payload"]

        # Option B check: LIQUIDBEES at 1000 => 8 units
        opt_b = report.option_b_directive
        assert opt_b is not None
        assert opt_b.target_type == SweepTargetType.LIQUID_ETF
        assert opt_b.symbol == "LIQUIDBEES"
        assert opt_b.quantity == 8
        assert opt_b.allocation_amount == 8000.0
        assert report.execution_payload_option_b is not None
        assert "LIQUIDBEES" in report.execution_payload_option_b["csv_payload"]


class TestSgbArbitrageEngine:
    """Test Sovereign Gold Bond pricing, discount to spot, and YTM calculation."""

    def test_gold_spot_derivation(self):
        # 1 unit GOLDBEES = 0.01g gold. If GOLDBEES = 76.50, spot per gram = 7650.0
        spot = calculate_gold_spot_from_goldbees(76.50)
        assert pytest.approx(spot, rel=1e-3) == 7650.0

    def test_discount_to_spot(self):
        spot = 7500.0
        sgb_price = 7200.0
        discount = compute_sgb_discount_to_spot(sgb_price, spot)
        # (7500 - 7200) / 7500 * 100 = 300 / 7500 * 100 = 4.0%
        assert pytest.approx(discount, rel=1e-3) == 4.0

    def test_sgb_metadata_parsing(self):
        meta_known = parse_sgb_symbol_metadata("SGBJUN28")
        assert meta_known["maturity_date"] == "2028-06-16"
        assert meta_known["issue_price"] == 4677.0

        meta_inferred = parse_sgb_symbol_metadata("SGBMAY29")
        assert meta_inferred["maturity_date"] == "2029-05-25"

    def test_sgb_ytm_calculation(self):
        # Test a bond trading at a discount
        # Issue price: 5000, Coupon: 2.5% p.a. (125/year), Traded price: 7000, Spot gold: 7500
        # Maturity in 2 years
        ytm = calculate_sgb_ytm(
            traded_price=7000.0,
            maturity_date="2028-11-09",
            issue_price=5000.0,
            current_gold_spot=7500.0,
            coupon_rate=0.025,
            current_date="2026-11-09",
        )
        assert ytm > 5.0  # Coupon + capital redemption gain exceeds 5% annualized

    def test_analyze_sgb_secondary_market_arbitrage(self):
        quotes = {
            "SGBJUN28": 7100.0,  # Below spot -> arbitrage candidate
            "SGBDE31I": 7450.0,  # Slight discount
            "SGBOCT26": 7600.0,  # Premium to spot
        }
        spot = 7500.0

        opps, payload = analyze_sgb_secondary_market(
            sgb_quotes=quotes,
            gold_spot_per_gram=spot,
            discount_threshold_pct=3.0,
            current_date="2026-11-09",
        )

        assert len(opps) == 3
        # Top opportunity should be SGBJUN28 with ~5.33% discount
        top = opps[0]
        assert top.symbol == "SGBJUN28"
        assert top.discount_pct > 5.0
        assert top.is_arbitrage_flagged is True

        # SGBOCT26 is at a premium (negative discount)
        oct26 = next(o for o in opps if o.symbol == "SGBOCT26")
        assert oct26.discount_pct < 0.0
        assert oct26.is_arbitrage_flagged is False

        # Basket payload must include the flagged order
        assert payload is not None
        assert "SGBJUN28" in payload["csv_payload"]
        assert payload["net_capital_required"] > 0
        assert payload["upi_mandate"] is not None
