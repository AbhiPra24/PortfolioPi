import asyncio
import os
import tempfile
import pytest
import aiosqlite
from unittest.mock import MagicMock

from core.database import init_db
from core.data_refresh import sync_breeze_trades_and_orders
from app_config import settings


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    test_db_path = str(tmp_path / "test_portfoliopi.db")
    monkeypatch.setattr(settings, "db_path", test_db_path)
    return test_db_path


@pytest.mark.anyio
async def test_sync_breeze_trades_and_orders(temp_db):
    await init_db()

    mock_breeze = MagicMock()
    mock_breeze.get_trade_list.return_value = {
        "Success": [
            {
                "order_id": "ORD001",
                "trade_id": "TRD001",
                "trade_date": "09-Nov-2026",
                "stock_code": "INFTEC",
                "action": "BUY",
                "quantity": "10",
                "average_cost": "1500.50",
                "ltp": "1502.00",
                "brokerage_amount": "20.00",
                "total_taxes": "3.60",
                "product_type": "Cash",
                "exchange_code": "NSE"
            }
        ]
    }
    mock_breeze.get_order_list.return_value = {
        "Success": [
            {
                "order_id": "ORD001",
                "order_datetime": "2026-11-09 10:15:00",
                "stock_code": "INFTEC",
                "action": "BUY",
                "order_type": "Limit",
                "quantity": "10",
                "price": "1500.50",
                "average_price": "1500.50",
                "status": "Executed",
                "exchange_code": "NSE"
            }
        ]
    }

    # First sync run
    stats = await sync_breeze_trades_and_orders(mock_breeze)
    assert stats["trades_synced"] == 1
    assert stats["orders_synced"] == 1

    # Verify rows in DB
    async with aiosqlite.connect(temp_db) as db:
        async with db.execute("SELECT trade_id, stock_code, quantity, average_cost, brokerage_amount FROM trades_history") as cur:
            trades = await cur.fetchall()
            assert len(trades) == 1
            assert trades[0][0] == "TRD001"
            assert trades[0][1] == "INFTEC"
            assert trades[0][2] == 10
            assert trades[0][3] == 1500.50
            assert trades[0][4] == 20.00

        async with db.execute("SELECT order_id, stock_code, status, price FROM orders_history") as cur:
            orders = await cur.fetchall()
            assert len(orders) == 1
            assert orders[0][0] == "ORD001"
            assert orders[0][1] == "INFTEC"
            assert orders[0][2] == "Executed"
            assert orders[0][3] == 1500.50

    # Idempotent upsert verification (same trade, updated cost/status)
    mock_breeze.get_trade_list.return_value = {
        "Success": [
            {
                "order_id": "ORD001",
                "trade_id": "TRD001",
                "trade_date": "09-Nov-2026",
                "stock_code": "INFTEC",
                "action": "BUY",
                "quantity": "15",
                "average_cost": "1510.00",
                "ltp": "1515.00",
                "brokerage_amount": "25.00",
                "total_taxes": "4.50",
                "product_type": "Cash",
                "exchange_code": "NSE"
            }
        ]
    }
    stats_updated = await sync_breeze_trades_and_orders(mock_breeze)
    assert stats_updated["trades_synced"] == 1

    async with aiosqlite.connect(temp_db) as db:
        async with db.execute("SELECT trade_id, quantity, average_cost FROM trades_history") as cur:
            trades = await cur.fetchall()
            assert len(trades) == 1
            assert trades[0][0] == "TRD001"
            assert trades[0][1] == 15
            assert trades[0][2] == 1510.00


@pytest.mark.anyio
async def test_sync_breeze_error_handling(temp_db):
    await init_db()

    mock_breeze = MagicMock()
    mock_breeze.get_trade_list.side_effect = Exception("API connection error")
    mock_breeze.get_order_list.return_value = {"Error": "Session expired"}

    # Should not raise exception
    stats = await sync_breeze_trades_and_orders(mock_breeze)
    assert stats["trades_synced"] == 0
    assert stats["orders_synced"] == 0
