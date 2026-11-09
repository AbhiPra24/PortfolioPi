"""REIT and InvIT Return of Capital (ROC) Amortization Hook and Yield-on-Cost Recalculation.

In Indian REITs & InvITs (e.g. EMBASSY, MINDSPACE, BIRET, POWERGRID, PGInvIT):
Distributions are partitioned into:
1. Dividend (taxable or exempt depending on SPV tax regime)
2. Interest (taxable at investor slab)
3. Return of Capital (ROC) / repayment of debt (non-taxable upon distribution, but reduces unit cost basis)

When ROC is received, tax law requires reducing the acquisition cost of the units:
    Adjusted Cost Basis = max(0.01, Previous Cost Basis - ROC_per_unit)
Yield-on-Cost (YoC) must then be recalculated:
    YoC = (expected_dpu / new_average_price) * 100

This module ensures dynamic execution on the ex-date, persists to SQLite, and maintains
an audit trail in `roc_amortization_log`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass
class DistributionComponents:
    """Distribution breakdown for REIT/InvIT corporate action."""

    stock_code: str
    ex_date: str  # YYYY-MM-DD
    dividend_amount: float = 0.0
    interest_amount: float = 0.0
    roc_amount: float = 0.0  # Return of capital per unit
    total_dpu: float = 0.0

    def __post_init__(self):
        if self.total_dpu <= 0:
            self.total_dpu = round(self.dividend_amount + self.interest_amount + self.roc_amount, 4)


@dataclass
class RocAmortizationResult:
    """Outcome of applying ROC amortization."""

    stock_code: str
    ex_date: str
    roc_per_unit: float
    old_cost: float
    new_cost: float
    old_yoc: Optional[float]
    new_yoc: Optional[float]
    units_affected: int
    amortized: bool
    message: str


async def ensure_reit_tables(db: aiosqlite.Connection) -> None:
    """Ensure roc_amortization_log and distribution component columns exist."""
    # Ensure roc_amortization_log exists
    await db.execute("""
        CREATE TABLE IF NOT EXISTS roc_amortization_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            ex_date TEXT NOT NULL,
            roc_per_unit REAL NOT NULL,
            old_cost REAL NOT NULL,
            new_cost REAL NOT NULL,
            processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, ex_date)
        )
    """)

    # Ensure dividend_schedule exists
    await db.execute("""
        CREATE TABLE IF NOT EXISTS dividend_schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            stock_code TEXT,
            asset_type TEXT NOT NULL,
            expected_dpu REAL NOT NULL,
            payout_frequency TEXT,
            last_ex_date TEXT,
            next_expected_payout TEXT,
            est_annual_cashflow REAL,
            yield_on_cost REAL,
            dividend_amount REAL DEFAULT 0.0,
            interest_amount REAL DEFAULT 0.0,
            roc_amount REAL DEFAULT 0.0,
            ex_date TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Try adding partitioning columns to dividend_schedule if not present
    for col_def in [
        "dividend_amount REAL DEFAULT 0.0",
        "interest_amount REAL DEFAULT 0.0",
        "roc_amount REAL DEFAULT 0.0",
        "ex_date TEXT",
    ]:
        col_def.split()[0]
        try:
            await db.execute(f"ALTER TABLE dividend_schedule ADD COLUMN {col_def};")
        except Exception:
            pass  # Already exists


async def apply_roc_amortization(
    db_path: str,
    stock_code: str,
    roc_per_unit: float,
    ex_date: str | date,
    as_of_date: Optional[date] = None,
) -> RocAmortizationResult:
    """Dynamically apply Return of Capital (ROC) amortization on/after ex-date.

    Rules:
    - Checks if ex_date is today or passed (relative to as_of_date or date.today()).
    - Idempotent: Checks roc_amortization_log to prevent double amortizing same (stock_code, ex_date).
    - Fetches current holdings_snapshot for stock_code.
    - Adjusts cost basis: new_average_price = max(0.01, old_average_price - roc_per_unit).
    - Updates holdings_snapshot.average_price.
    - Recalculates dividend_schedule.yield_on_cost:
      yield_on_cost = (expected_dpu / new_average_price) * 100.
    - Persists audit trail in roc_amortization_log.
    """
    clean_code = stock_code.strip().upper()
    ex_date_str = ex_date.strftime("%Y-%m-%d") if isinstance(ex_date, date) else str(ex_date).strip()
    check_date = as_of_date or date.today()

    try:
        ex_dt = datetime.strptime(ex_date_str, "%Y-%m-%d").date()
    except Exception:
        return RocAmortizationResult(
            stock_code=clean_code,
            ex_date=ex_date_str,
            roc_per_unit=roc_per_unit,
            old_cost=0.0,
            new_cost=0.0,
            old_yoc=None,
            new_yoc=None,
            units_affected=0,
            amortized=False,
            message=f"Invalid ex_date format: {ex_date_str}",
        )

    # Check if ex_date is in the future
    if ex_dt > check_date:
        return RocAmortizationResult(
            stock_code=clean_code,
            ex_date=ex_date_str,
            roc_per_unit=roc_per_unit,
            old_cost=0.0,
            new_cost=0.0,
            old_yoc=None,
            new_yoc=None,
            units_affected=0,
            amortized=False,
            message=f"Ex-date {ex_date_str} is in future (as of {check_date}). Amortization skipped.",
        )

    if roc_per_unit <= 0:
        return RocAmortizationResult(
            stock_code=clean_code,
            ex_date=ex_date_str,
            roc_per_unit=roc_per_unit,
            old_cost=0.0,
            new_cost=0.0,
            old_yoc=None,
            new_yoc=None,
            units_affected=0,
            amortized=False,
            message=f"ROC per unit {roc_per_unit} <= 0. Nothing to amortize.",
        )

    async with aiosqlite.connect(db_path) as db:
        await ensure_reit_tables(db)

        # 1. Check idempotency in roc_amortization_log
        async with db.execute(
            "SELECT id, old_cost, new_cost FROM roc_amortization_log WHERE stock_code = ? AND ex_date = ?",
            (clean_code, ex_date_str),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return RocAmortizationResult(
                    stock_code=clean_code,
                    ex_date=ex_date_str,
                    roc_per_unit=roc_per_unit,
                    old_cost=row[1],
                    new_cost=row[2],
                    old_yoc=None,
                    new_yoc=None,
                    units_affected=0,
                    amortized=False,
                    message=f"Already amortized on {ex_date_str} for {clean_code} (log id {row[0]}).",
                )

        # 2. Fetch current holding from holdings_snapshot
        async with db.execute(
            "SELECT quantity, average_price FROM holdings_snapshot WHERE UPPER(TRIM(stock_code)) = ?",
            (clean_code,),
        ) as cur:
            h_row = await cur.fetchone()

        if not h_row or (h_row[0] or 0) <= 0:
            return RocAmortizationResult(
                stock_code=clean_code,
                ex_date=ex_date_str,
                roc_per_unit=roc_per_unit,
                old_cost=0.0,
                new_cost=0.0,
                old_yoc=None,
                new_yoc=None,
                units_affected=0,
                amortized=False,
                message=f"No active holdings found in holdings_snapshot for {clean_code}.",
            )

        quantity = int(h_row[0])
        old_average_price = float(h_row[1] or 0.0)

        # 3. Calculate adjusted cost basis
        new_average_price = max(0.01, round(old_average_price - roc_per_unit, 4))

        # 4. Update holdings_snapshot
        await db.execute(
            "UPDATE holdings_snapshot SET average_price = ?, timestamp = CURRENT_TIMESTAMP WHERE UPPER(TRIM(stock_code)) = ?",
            (new_average_price, clean_code),
        )

        # 5. Fetch dividend_schedule to recalculate Yield-on-Cost (YoC)
        old_yoc = None
        new_yoc = None
        async with db.execute(
            "SELECT id, expected_dpu, yield_on_cost FROM dividend_schedule WHERE UPPER(TRIM(stock_code)) = ? OR UPPER(TRIM(ticker)) = ?",
            (clean_code, clean_code),
        ) as cur:
            div_row = await cur.fetchone()

        if div_row:
            div_id = div_row[0]
            expected_dpu = float(div_row[1] or 0.0)
            old_yoc = float(div_row[2]) if div_row[2] is not None else None
            if new_average_price > 0:
                new_yoc = round((expected_dpu / new_average_price) * 100.0, 4)
                await db.execute(
                    """
                    UPDATE dividend_schedule
                    SET yield_on_cost = ?,
                        roc_amount = ?,
                        ex_date = ?,
                        last_ex_date = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (new_yoc, roc_per_unit, ex_date_str, ex_date_str, div_id),
                )

        # 6. Record audit trail in roc_amortization_log
        await db.execute(
            """
            INSERT INTO roc_amortization_log (stock_code, ex_date, roc_per_unit, old_cost, new_cost)
            VALUES (?, ?, ?, ?, ?)
            """,
            (clean_code, ex_date_str, roc_per_unit, old_average_price, new_average_price),
        )

        await db.commit()

    logger.info(
        f"Amortized ROC ₹{roc_per_unit:.2f}/unit for {clean_code} on {ex_date_str}. "
        f"Cost basis: ₹{old_average_price:.2f} -> ₹{new_average_price:.2f}. "
        f"YoC: {old_yoc}% -> {new_yoc}%."
    )

    return RocAmortizationResult(
        stock_code=clean_code,
        ex_date=ex_date_str,
        roc_per_unit=roc_per_unit,
        old_cost=old_average_price,
        new_cost=new_average_price,
        old_yoc=old_yoc,
        new_yoc=new_yoc,
        units_affected=quantity,
        amortized=True,
        message=f"Successfully amortized ROC ₹{roc_per_unit:.2f}/unit for {clean_code}.",
    )
