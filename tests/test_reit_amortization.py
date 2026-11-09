"""Unit tests for Dynamic Ex-Date REIT/InvIT Return of Capital (ROC) Amortization Hook."""

from datetime import date, timedelta

import aiosqlite
import pytest

from core.reit_amortization import (
    apply_roc_amortization,
    ensure_reit_tables,
)


@pytest.fixture
async def setup_test_db(tmp_path):
    """Setup a temporary database initialized with test holding and dividend schedule."""
    db_file = str(tmp_path / "test_portfoliopi.db")
    async with aiosqlite.connect(db_file) as db:
        await ensure_reit_tables(db)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS holdings_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT UNIQUE,
                quantity INTEGER,
                average_price REAL,
                current_price REAL,
                asset_class TEXT DEFAULT 'EQUITY',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Insert initial REIT holding: EMBASSY at cost 350.00
        await db.execute(
            "INSERT INTO holdings_snapshot (stock_code, quantity, average_price, current_price, asset_class) VALUES (?, ?, ?, ?, ?)",
            ("EMBASSY", 100, 350.00, 380.00, "REIT"),
        )
        # Insert dividend_schedule with expected_dpu = 21.00 (initial YoC = (21/350)*100 = 6.0%)
        await db.execute(
            """
            INSERT INTO dividend_schedule (ticker, stock_code, asset_type, expected_dpu, yield_on_cost)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("EMBASSY", "EMBASSY", "REIT", 21.0, 6.0),
        )
        await db.commit()
    return db_file


@pytest.mark.anyio
async def test_roc_amortization_deduction_and_yoc(setup_test_db):
    """Verify cost basis deduction and YoC recalculation."""
    db_file = setup_test_db
    ex_date = date.today().strftime("%Y-%m-%d")
    roc_per_unit = 5.00

    result = await apply_roc_amortization(
        db_path=db_file,
        stock_code="EMBASSY",
        roc_per_unit=roc_per_unit,
        ex_date=ex_date,
    )

    assert result.amortized is True
    assert result.stock_code == "EMBASSY"
    assert result.old_cost == 350.00
    assert result.new_cost == 345.00  # 350 - 5
    assert result.units_affected == 100

    # New YoC = (21.0 / 345.0) * 100 = 6.08695...
    expected_new_yoc = round((21.0 / 345.0) * 100.0, 4)
    assert result.new_yoc == expected_new_yoc

    # Verify SQLite persistence
    async with aiosqlite.connect(db_file) as db:
        async with db.execute("SELECT average_price FROM holdings_snapshot WHERE stock_code = 'EMBASSY'") as cur:
            row = await cur.fetchone()
            assert row[0] == 345.00

        async with db.execute(
            "SELECT yield_on_cost, roc_amount FROM dividend_schedule WHERE stock_code = 'EMBASSY'"
        ) as cur:
            row = await cur.fetchone()
            assert row[0] == expected_new_yoc
            assert row[1] == 5.00

        async with db.execute(
            "SELECT old_cost, new_cost, roc_per_unit FROM roc_amortization_log WHERE stock_code = 'EMBASSY'"
        ) as cur:
            log_row = await cur.fetchone()
            assert log_row is not None
            assert log_row[0] == 350.00
            assert log_row[1] == 345.00
            assert log_row[2] == 5.00


@pytest.mark.anyio
async def test_roc_amortization_idempotency(setup_test_db):
    """Verify calling apply_roc_amortization twice for the same ex_date does not double deduct."""
    db_file = setup_test_db
    ex_date = "2026-11-09"
    roc_per_unit = 10.00

    # First call: succeeds
    res1 = await apply_roc_amortization(
        db_path=db_file,
        stock_code="EMBASSY",
        roc_per_unit=roc_per_unit,
        ex_date=ex_date,
    )
    assert res1.amortized is True
    assert res1.new_cost == 340.00

    # Second call with same ex_date: must skip
    res2 = await apply_roc_amortization(
        db_path=db_file,
        stock_code="EMBASSY",
        roc_per_unit=roc_per_unit,
        ex_date=ex_date,
    )
    assert res2.amortized is False
    assert "Already amortized" in res2.message

    # Verify database cost basis was NOT altered a second time
    async with aiosqlite.connect(db_file) as db:
        async with db.execute("SELECT average_price FROM holdings_snapshot WHERE stock_code = 'EMBASSY'") as cur:
            row = await cur.fetchone()
            assert row[0] == 340.00  # Stays at 340, not 330


@pytest.mark.anyio
async def test_roc_amortization_boundary_at_0_01(setup_test_db):
    """Verify cost basis does not drop to 0 or negative; minimum floor is 0.01."""
    db_file = setup_test_db
    ex_date = "2026-11-09"
    # Old cost is 350.00. Give ROC of 400.00.
    roc_per_unit = 400.00

    result = await apply_roc_amortization(
        db_path=db_file,
        stock_code="EMBASSY",
        roc_per_unit=roc_per_unit,
        ex_date=ex_date,
    )

    assert result.amortized is True
    assert result.old_cost == 350.00
    assert result.new_cost == 0.01

    # Verify YoC handles 0.01
    async with aiosqlite.connect(db_file) as db:
        async with db.execute("SELECT average_price FROM holdings_snapshot WHERE stock_code = 'EMBASSY'") as cur:
            row = await cur.fetchone()
            assert row[0] == 0.01


@pytest.mark.anyio
async def test_roc_amortization_future_ex_date(setup_test_db):
    """Verify future ex-dates are rejected until the ex-date arrives."""
    db_file = setup_test_db
    future_date = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")

    result = await apply_roc_amortization(
        db_path=db_file,
        stock_code="EMBASSY",
        roc_per_unit=5.0,
        ex_date=future_date,
    )

    assert result.amortized is False
    assert "in future" in result.message
