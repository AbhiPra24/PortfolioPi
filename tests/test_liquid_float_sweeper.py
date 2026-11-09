import sqlite3

import aiosqlite
import pytest

from algo.liquid_float_sweeper import (
    RedeploymentAction,
    calculate_paused_sip_float,
    calculate_paused_sip_float_async,
    compute_unallocated_cash_float,
    evaluate_liquid_float_sweep,
)
from algo.order_payloads import parse_trade_racer_csv


@pytest.fixture
def memory_db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE sip_book (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            stock_code TEXT,
            asset_category TEXT NOT NULL,
            monthly_sip REAL NOT NULL,
            execution_day INTEGER DEFAULT 31,
            status TEXT DEFAULT 'ACTIVE',
            quant_stage INTEGER DEFAULT 2,
            target_action TEXT DEFAULT 'CONTINUE',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Insert test rows:
    # 1. Active Stage 2 continue -> should NOT be counted as paused float
    conn.execute("""
        INSERT INTO sip_book (ticker, stock_code, asset_category, monthly_sip, quant_stage, target_action, status)
        VALUES ('TCS', 'TCS', 'EQUITY', 5000.0, 2, 'CONTINUE', 'ACTIVE')
    """)
    # 2. Stage 3 breakdown trimmed/paused -> should be counted (2500.0)
    conn.execute("""
        INSERT INTO sip_book (ticker, stock_code, asset_category, monthly_sip, quant_stage, target_action, status)
        VALUES ('INFY', 'INFY', 'EQUITY', 2500.0, 3, 'TRIM / PAUSE SIP', 'ACTIVE')
    """)
    # 3. Stage 4 breakdown stopped -> should be counted (3500.0)
    conn.execute("""
        INSERT INTO sip_book (ticker, stock_code, asset_category, monthly_sip, quant_stage, target_action, status)
        VALUES ('WIPRO', 'WIPRO', 'EQUITY', 3500.0, 4, 'SELL / STOP SIP', 'ACTIVE')
    """)
    conn.commit()
    yield conn
    conn.close()


def test_calculate_paused_sip_float_sqlite(memory_db):
    """Verify sum of paused/frozen monthly SIP amounts in sqlite."""
    paused_sum = calculate_paused_sip_float(memory_db)
    # 2500 + 3500 = 6000.0
    assert paused_sum == 6000.0


@pytest.mark.anyio
async def test_calculate_paused_sip_float_async():
    """Verify async query for paused SIP amounts."""
    async with aiosqlite.connect(":memory:") as db:
        await db.execute("""
            CREATE TABLE sip_book (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                stock_code TEXT,
                asset_category TEXT NOT NULL,
                monthly_sip REAL NOT NULL,
                execution_day INTEGER DEFAULT 31,
                status TEXT DEFAULT 'ACTIVE',
                quant_stage INTEGER DEFAULT 2,
                target_action TEXT DEFAULT 'CONTINUE'
            )
        """)
        await db.execute("""
            INSERT INTO sip_book (ticker, asset_category, monthly_sip, quant_stage, target_action, status)
            VALUES ('RELIANCE', 'EQUITY', 4000.0, 4, 'SELL / STOP SIP', 'PAUSED')
        """)
        await db.commit()

        paused_sum = await calculate_paused_sip_float_async(db)
        assert paused_sum == 4000.0


def test_compute_unallocated_cash_float():
    float_amt = compute_unallocated_cash_float(unallocated_cash=4500.0, paused_sip_amount=6000.0)
    assert float_amt == 10500.0


def test_sweep_to_liquidbees_when_no_stage2_leader():
    """When screener finds no new Stage 2 leader, sweep unallocated cash float into LIQUIDBEES."""
    # unallocated_cash: 2500, paused_sip: 6000 -> float: 8500.
    # ETF price: 1000.0 -> qty = floor(8500 / 1000) = 8.
    report = evaluate_liquid_float_sweep(
        unallocated_cash=2500.0,
        paused_sip_amount=6000.0,
        stage2_leaders=[],
        liquid_etf_ticker="LIQUIDBEES",
        liquid_etf_price=1000.0,
    )

    assert report.action == RedeploymentAction.SWEEP_TO_LIQUIDBEES
    assert report.unallocated_cash_float == 8500.0
    assert report.quantity == 8
    assert report.allocation_amount == 8000.0
    assert report.remaining_cash == 500.0
    assert report.target_ticker == "LIQUIDBEES"
    assert len(report.orders) == 1
    assert report.orders[0].action == "BUY"
    assert report.orders[0].stock_code == "LIQUIDBEES"
    assert report.orders[0].quantity == 8

    # Verify Trade Racer CSV generated
    assert "Exchange,StockCode,Action,OrderType,Validity,Quantity,Price" in report.trade_racer_csv
    assert "NSE,LIQUIDBEES,BUY,LIMIT,DAY,8,1000.00" in report.trade_racer_csv

    # Parse back
    parsed = parse_trade_racer_csv(report.trade_racer_csv)
    assert len(parsed) == 1
    assert parsed[0].stock_code == "LIQUIDBEES"
    assert parsed[0].quantity == 8

    # Verify UPI deep link generated
    assert report.execution_payload is not None
    assert "upi_mandate" in report.execution_payload
    assert "upi://pay?" in report.execution_payload["upi_mandate"]["deep_link"]


def test_redeploy_to_stage2_leader_when_breakout_found():
    """When screener identifies Stage 2 leader, emit redeployment directive and orders."""
    candidates = [
        {"ticker": "FEDERALBNK", "stage": 2, "price": 200.0, "rs_score": 88.0, "score": 92.0},
        {"ticker": "SBIN", "stage": 1, "price": 750.0, "rs_score": 50.0, "score": 40.0},
    ]

    # Float: 10,000. Leader price: 200 -> qty = 50
    report = evaluate_liquid_float_sweep(
        unallocated_cash=4000.0,
        paused_sip_amount=6000.0,
        stage2_leaders=candidates,
        liquid_etf_ticker="LIQUIDBEES",
        liquid_etf_price=1000.0,
    )

    assert report.action == RedeploymentAction.REDEPLOY_TO_STAGE2_LEADER
    assert report.unallocated_cash_float == 10000.0
    assert report.target_ticker == "FEDERALBNK"
    assert report.quantity == 50
    assert report.allocation_amount == 10000.0
    assert report.remaining_cash == 0.0
    assert len(report.orders) == 1
    assert report.orders[0].stock_code == "FEDERALBNK"
    assert report.orders[0].action == "BUY"
    assert report.orders[0].quantity == 50

    # Trade Racer CSV
    assert "NSE,FEDERALBNK,BUY,LIMIT,DAY,50,200.00" in report.trade_racer_csv


def test_redeploy_harvests_existing_liquidbees():
    """When redeploying with existing LIQUIDBEES holdings, sells LIQUIDBEES to fund Stage 2 leader."""
    candidates = [
        {"ticker": "DIXON", "stage": 2, "price": 10000.0, "rs_score": 95.0, "score": 90.0},
    ]

    # Cash float = 5,000. Holding 10 LIQUIDBEES @ 1000 = 10,000. Total capital = 15,000.
    # Leader price = 10,000 -> qty = 1 share, remaining = 5,000.
    report = evaluate_liquid_float_sweep(
        unallocated_cash=2000.0,
        paused_sip_amount=3000.0,
        stage2_leaders=candidates,
        liquid_etf_ticker="LIQUIDBEES",
        liquid_etf_price=1000.0,
        liquidbees_holdings_qty=10,
    )

    assert report.action == RedeploymentAction.REDEPLOY_TO_STAGE2_LEADER
    assert report.target_ticker == "DIXON"
    assert report.quantity == 1
    assert report.allocation_amount == 10000.0
    assert report.remaining_cash == 5000.0

    # Orders should have both SELL LIQUIDBEES and BUY DIXON
    assert len(report.orders) == 2
    sell_order = report.orders[0]
    buy_order = report.orders[1]

    assert sell_order.stock_code == "LIQUIDBEES"
    assert sell_order.action == "SELL"
    assert sell_order.quantity == 10

    assert buy_order.stock_code == "DIXON"
    assert buy_order.action == "BUY"
    assert buy_order.quantity == 1


def test_sweep_below_threshold_no_action():
    report = evaluate_liquid_float_sweep(
        unallocated_cash=200.0,
        paused_sip_amount=300.0,
        min_sweep_threshold=1000.0,
    )
    assert report.action == RedeploymentAction.NO_ACTION
    assert report.quantity == 0
    assert len(report.orders) == 0
