"""Unit and integration tests for Breeze trades parsing and performance analytics."""

import pytest

from scripts.analyse_trade_performance import (
    calculate_trade_metrics,
    format_trades_summary_header,
)


@pytest.fixture
def mock_breeze_trades():
    return [
        {
            "action": "Buy",
            "stock_code": "INFY",
            "quantity": 10,
            "average_cost": 15000.0,
            "ltp": 1500.0,
            "brokerage_amount": 25.0,
            "total_taxes": 15.0,
            "trade_date": "2026-11-01T10:00:00.000Z",
            "order_id": "ORD001",
            "trade_id": "TRD001",
        },
        {
            "action": "Buy",
            "stock_code": "RELIANCE",
            "quantity": 5,
            "average_cost": 12500.0,
            "ltp": 2500.0,
            "brokerage_amount": 20.0,
            "total_taxes": 12.0,
            "trade_date": "2026-11-02T11:30:00.000Z",
            "order_id": "ORD002",
            "trade_id": "TRD002",
        },
        {
            "action": "Sell",
            "stock_code": "INFY",
            "quantity": 10,
            "average_cost": 16000.0,
            "ltp": 1600.0,
            "brokerage_amount": 25.0,
            "total_taxes": 16.0,
            "trade_date": "2026-11-05T14:15:00.000Z",
            "order_id": "ORD003",
            "trade_id": "TRD003",
        },
    ]


def test_calculate_trade_metrics(mock_breeze_trades):
    metrics = calculate_trade_metrics(mock_breeze_trades)

    # 15000 + 12500 + 16000 = 43500
    assert metrics["total_turnover"] == 43500.0
    assert metrics["buy_turnover"] == 27500.0
    assert metrics["sell_turnover"] == 16000.0
    assert metrics["buy_count"] == 2
    assert metrics["sell_count"] == 1
    assert metrics["total_trades"] == 3

    # Brokerage: 25 + 20 + 25 = 70
    assert metrics["total_brokerage"] == 70.0
    # Taxes: 15 + 12 + 16 = 43
    assert metrics["total_taxes"] == 43.0
    # Frictions: 70 + 43 = 113
    assert metrics["total_frictions"] == 113.0

    # Drag % = 113 / 43500 * 100 ~ 0.25977% -> 0.26%
    assert 0.25 < metrics["drag_pct"] < 0.27

    # Average trade size = 43500 / 3 = 14500.0
    assert metrics["avg_trade_size"] == 14500.0

    # Realized PnL:
    # INFY Buy: 10 @ 1500, Sell: 10 @ 1600 -> Gross PnL = +1000
    assert metrics["closed_trades_count"] == 1
    assert metrics["winning_trades"] == 1
    assert metrics["losing_trades"] == 0
    assert metrics["win_rate_pct"] == 100.0
    assert metrics["realized_gross_pnl"] == 1000.0
    # Net PnL = 1000 - 113 = 887.0
    assert metrics["net_realized_pnl"] == 887.0


def test_calculate_trade_metrics_empty():
    metrics = calculate_trade_metrics([])
    assert metrics["total_trades"] == 0
    assert metrics["total_turnover"] == 0.0
    assert metrics["drag_pct"] == 0.0
    assert metrics["avg_trade_size"] == 0.0
    assert metrics["closed_trades_count"] == 0
    assert metrics["win_rate_pct"] == 0.0


def test_format_trades_summary_header(mock_breeze_trades):
    metrics = calculate_trade_metrics(mock_breeze_trades)
    header = format_trades_summary_header(metrics)

    assert "<b>Total Turnover:</b> ₹43,500.00" in header
    assert "<b>Transaction Drag:</b>" in header
    assert "Brokerage: ₹70.00" in header
    assert "Taxes: ₹43.00" in header
    assert "Win Rate:</b> 100.0%" in header
