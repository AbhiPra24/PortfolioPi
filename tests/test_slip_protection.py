"""Unit tests for Dynamic Limit Slip-Protection Guardrails.

Verifies:
- BUY orders capped at CMP * (1 + buffer) (default +0.3% / 0.003).
- SELL orders floor at CMP * (1 - buffer) (default -0.3% / 0.003).
- Buffer overrides for REIT/InvIT/Debt ETF/illiquid assets (e.g. 0.8% / 0.008 or custom).
- Disallow unconstrained MARKET orders on illiquid instruments by enforcing OrderType='LIMIT'.
- Helper functions: `apply_slip_protection` and `create_slip_protected_basket_order`.
- Trade Racer CSV generation reflects exact buffered Price and OrderType='LIMIT'.
- Integration with `create_rebalance_execution_payload`.
"""

from algo.order_payloads import (
    BasketOrder,
    apply_slip_protection,
    compute_buffered_price,
    create_rebalance_execution_payload,
    create_slip_protected_basket_order,
    generate_trade_racer_csv,
    is_instrument_illiquid,
    parse_trade_racer_csv,
)


class TestSlipProtection:
    """Test suite for limit slip-protection calculations and guardrails."""

    def test_compute_buffered_price_buy(self):
        # CMP 1000.0, BUY with default +0.3% buffer
        price = compute_buffered_price(cmp=1000.0, action="BUY", buffer_pct=0.003)
        assert price == 1003.0

        # CMP 205.50, BUY with +0.3% buffer: 205.50 * 1.003 = 206.1165 -> 206.12
        price2 = compute_buffered_price(cmp=205.50, action="BUY", buffer_pct=0.003)
        assert price2 == 206.12

    def test_compute_buffered_price_sell(self):
        # CMP 1000.0, SELL with default -0.3% buffer
        price = compute_buffered_price(cmp=1000.0, action="SELL", buffer_pct=0.003)
        assert price == 997.0

        # CMP 290.00, SELL with -0.3% buffer: 290.00 * 0.997 = 289.13
        price2 = compute_buffered_price(cmp=290.00, action="SELL", buffer_pct=0.003)
        assert price2 == 289.13

    def test_illiquid_instrument_detection(self):
        assert is_instrument_illiquid("EMBASSY") is True
        assert is_instrument_illiquid("EMBOFF") is True
        assert is_instrument_illiquid("MINDSPACE") is True
        assert is_instrument_illiquid("BIRET") is True
        assert is_instrument_illiquid("PGINVIT") is True
        assert is_instrument_illiquid("POWINF") is True
        assert is_instrument_illiquid("INDIGRID") is True
        assert is_instrument_illiquid("INDGRI") is True
        assert is_instrument_illiquid("LIQUIDBEES") is True
        assert is_instrument_illiquid("EBBETF0430") is True
        assert is_instrument_illiquid("CUSTOM_REIT") is True
        assert is_instrument_illiquid("POWER_INVIT") is True

        # Liquid standard equities
        assert is_instrument_illiquid("RELIANCE") is False
        assert is_instrument_illiquid("INFY") is False
        assert is_instrument_illiquid("TATAMOTORS") is False

    def test_create_slip_protected_basket_order_liquid(self):
        order = create_slip_protected_basket_order(
            stock_code="RELIANCE",
            action="BUY",
            quantity=10,
            cmp=2500.0,
            buffer_pct=0.003,
        )
        assert order.stock_code == "RELIANCE"
        assert order.action == "BUY"
        assert order.quantity == 10
        # 2500 * 1.003 = 2507.50
        assert order.price == 2507.50
        assert order.order_type == "LIMIT"
        assert order.is_illiquid is False

    def test_create_slip_protected_basket_order_reit_auto_buffer(self):
        # Auto-detects illiquid REIT and applies default illiquid buffer (0.8%)
        order = create_slip_protected_basket_order(
            stock_code="EMBASSY",
            action="BUY",
            quantity=100,
            cmp=350.0,
        )
        assert order.stock_code == "EMBASSY"
        assert order.action == "BUY"
        # 350 * (1 + 0.008) = 352.80
        assert order.price == 352.80
        assert order.order_type == "LIMIT"
        assert order.is_illiquid is True

    def test_apply_slip_protection_caps_buy_and_floors_sell(self):
        orders = [
            BasketOrder(stock_code="RELIANCE", action="BUY", quantity=10, price=2500.0, order_type="LIMIT"),
            BasketOrder(stock_code="TCS", action="SELL", quantity=5, price=4000.0, order_type="LIMIT"),
        ]

        protected = apply_slip_protection(orders, buffer_pct=0.003)
        assert len(protected) == 2

        buy_order = protected[0]
        assert buy_order.stock_code == "RELIANCE"
        assert buy_order.price == 2507.50  # 2500 * 1.003
        assert buy_order.order_type == "LIMIT"

        sell_order = protected[1]
        assert sell_order.stock_code == "TCS"
        assert sell_order.price == 3988.00  # 4000 * (1 - 0.003) = 3988.0
        assert sell_order.order_type == "LIMIT"

    def test_apply_slip_protection_reit_invit_custom_overrides(self):
        orders = [
            # Standard liquid stock
            BasketOrder(stock_code="INFY", action="BUY", quantity=20, price=1500.0),
            # REIT with default illiquid detection
            BasketOrder(stock_code="EMBASSY", action="BUY", quantity=50, price=380.0),
            # InvIT with specific custom buffer override of 0.5% (0.005)
            BasketOrder(stock_code="PGINVIT", action="SELL", quantity=100, price=120.0),
            # REIT with specific custom buffer override of 0.8% (0.008)
            BasketOrder(stock_code="BIRET", action="BUY", quantity=60, price=250.0),
        ]

        custom_buffers = {
            "PGINVIT": 0.005,  # 0.5%
            "BIRET": 0.008,  # 0.8%
        }

        protected = apply_slip_protection(orders, buffer_pct=0.003, asset_buffers=custom_buffers)

        # INFY: default 0.3% -> 1500 * 1.003 = 1504.50
        assert protected[0].price == 1504.50
        assert protected[0].order_type == "LIMIT"

        # EMBASSY: auto illiquid default 0.8% -> 380 * 1.008 = 383.04
        assert protected[1].price == 383.04
        assert protected[1].order_type == "LIMIT"
        assert protected[1].is_illiquid is True

        # PGINVIT: custom override 0.5% SELL floor -> 120 * (1 - 0.005) = 119.40
        assert protected[2].price == 119.40
        assert protected[2].order_type == "LIMIT"

        # BIRET: custom override 0.8% BUY cap -> 250 * 1.008 = 252.00
        assert protected[3].price == 252.00
        assert protected[3].order_type == "LIMIT"

    def test_market_orders_converted_to_limit_on_illiquid(self):
        # Illiquid instrument order with order_type="MARKET" must be constrained to LIMIT
        illiquid_order = BasketOrder(
            stock_code="INDIGRID",
            action="BUY",
            quantity=100,
            price=140.0,
            order_type="MARKET",
        )
        liquid_market_order = BasketOrder(
            stock_code="TCS",
            action="BUY",
            quantity=10,
            price=4000.0,
            order_type="MARKET",
        )

        protected = apply_slip_protection([illiquid_order, liquid_market_order])

        # INDIGRID is converted to LIMIT with 0.8% buffer: 140 * 1.008 = 141.12
        assert protected[0].stock_code == "INDIGRID"
        assert protected[0].order_type == "LIMIT"
        assert protected[0].price == 141.12

        # Any market order passed through slip protection is constrained to LIMIT with buffer
        assert protected[1].stock_code == "TCS"
        assert protected[1].order_type == "LIMIT"
        assert protected[1].price == 4012.00  # 4000 * 1.003

    def test_trade_racer_csv_reflects_buffered_limit_price(self):
        orders = [
            BasketOrder(stock_code="FEDERALBNK", action="BUY", quantity=50, price=200.0),
            BasketOrder(stock_code="EMBASSY", action="BUY", quantity=100, price=350.0),
            BasketOrder(stock_code="BEL", action="SELL", quantity=20, price=300.0),
        ]

        protected = apply_slip_protection(
            orders,
            buffer_pct=0.003,
            asset_buffers={"EMBASSY": 0.008},
        )

        csv_str = generate_trade_racer_csv(protected)
        parsed = parse_trade_racer_csv(csv_str)

        assert len(parsed) == 3

        # FEDERALBNK BUY: 200 * 1.003 = 200.60
        assert parsed[0].stock_code == "FEDERALBNK"
        assert parsed[0].action == "BUY"
        assert parsed[0].order_type == "LIMIT"
        assert parsed[0].price == 200.60

        # EMBASSY BUY: 350 * 1.008 = 352.80
        assert parsed[1].stock_code == "EMBASSY"
        assert parsed[1].action == "BUY"
        assert parsed[1].order_type == "LIMIT"
        assert parsed[1].price == 352.80

        # BEL SELL: 300 * 0.997 = 299.10
        assert parsed[2].stock_code == "BEL"
        assert parsed[2].action == "SELL"
        assert parsed[2].order_type == "LIMIT"
        assert parsed[2].price == 299.10

    def test_rebalance_execution_payload_with_slip_protection(self):
        orders = [
            BasketOrder(stock_code="FEDERALBNK", action="BUY", quantity=100, price=200.0),
            BasketOrder(stock_code="INDIGRID", action="BUY", quantity=50, price=140.0),
        ]

        # Without slip protection
        raw_payload = create_rebalance_execution_payload(orders, slip_protection=False)
        # 100*200 + 50*140 = 20000 + 7000 = 27000.0
        assert raw_payload["total_buy_value"] == 27000.0
        assert raw_payload["net_capital_required"] == 27000.0

        # With slip protection enabled
        # FEDERALBNK: 200 * 1.003 = 200.60 -> 100 * 200.60 = 20060.0
        # INDIGRID (illiquid InvIT): 140 * 1.008 = 141.12 -> 50 * 141.12 = 7056.0
        # Total buy value: 20060 + 7056 = 27116.0
        prot_payload = create_rebalance_execution_payload(
            orders,
            slip_protection=True,
            buffer_pct=0.003,
            asset_buffers={"INDIGRID": 0.008},
        )
        assert prot_payload["total_buy_value"] == 27116.0
        assert prot_payload["net_capital_required"] == 27116.0
        assert "200.60" in prot_payload["csv_payload"]
        assert "141.12" in prot_payload["csv_payload"]
        assert "am=27116.00" in prot_payload["upi_mandate"]["deep_link"]
