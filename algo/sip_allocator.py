"""Stage-Gated Bell-Curve Pullback Formula for SIP Quant Multipliers and Portfolio Allocation."""

from typing import Dict, Optional, Tuple, Union

import numpy as np
import pandas as pd


def check_liquidity_health(
    ticker: str,
    avg_daily_turnover_1m: float,
    price_to_nav_spread_pct: float,
    asset_class: str = "EQUITY",
) -> Tuple[bool, str]:
    """Evaluate liquidity and bid-ask / NAV spread health for equities, ETFs, and InvITs/REITs.

    Thresholds:
    - REIT_INVIT: requires 1-month average daily turnover >= ₹15,00,000 (15 Lakhs)
      and |Price - NAV| / NAV <= 8% (spread <= 8.0%, rejecting if premium > 8% or
      extreme discount indicating distress).
    - DEBT_ETF: requires average daily turnover >= ₹25,00,000 (25 Lakhs)
      and spread <= 0.5%.
    - EQUITY (Small/Midcaps): requires average daily turnover >= ₹50,00,000 (50 Lakhs)
      to prevent market impact slippage.

    Parameters
    ----------
    ticker : str
        Stock or instrument ticker symbol.
    avg_daily_turnover_1m : float
        1-month average daily turnover (ADTV) in INR (Rupees).
    price_to_nav_spread_pct : float
        Price to NAV spread percentage: |Price - NAV| / NAV * 100 or signed/absolute spread pct.
        Treated in absolute percentage points (e.g. 5.0 for 5%).
    asset_class : str, default 'EQUITY'
        Asset class category ('REIT_INVIT', 'REIT', 'INVIT', 'DEBT_ETF', 'EQUITY', etc.)

    Returns
    -------
    Tuple[bool, str]
        (is_healthy, rationale)
    """
    clean_asset = (asset_class or "EQUITY").upper()
    turnover = float(avg_daily_turnover_1m)
    abs_spread = abs(float(price_to_nav_spread_pct))

    # Standardize REIT / INVIT asset class keys
    if clean_asset in ("REIT_INVIT", "REIT", "INVIT"):
        min_turnover = 15_00_000.0  # ₹15 Lakhs
        max_spread = 8.0  # 8%

        if turnover < min_turnover:
            return (
                False,
                f"REIT/InvIT turnover ₹{turnover:,.0f} below ₹15L threshold (illiquid order book)",
            )
        if abs_spread > max_spread:
            return (
                False,
                f"REIT/InvIT NAV spread {abs_spread:.2f}% breaches 8.0% guardrail (stretched premium/distress discount)",
            )
        return True, "Healthy REIT/InvIT liquidity and NAV spread"

    elif clean_asset in ("DEBT_ETF", "DEBT"):
        min_turnover = 25_00_000.0  # ₹25 Lakhs
        max_spread = 0.5  # 0.5%

        if turnover < min_turnover:
            return (
                False,
                f"Debt ETF turnover ₹{turnover:,.0f} below ₹25L threshold (liquidity risk)",
            )
        if abs_spread > max_spread:
            return (
                False,
                f"Debt ETF price-to-NAV spread {abs_spread:.2f}% breaches 0.5% guardrail",
            )
        return True, "Healthy Debt ETF liquidity and NAV spread"

    else:
        # Default EQUITY (Small/Midcaps)
        min_turnover = 50_00_000.0  # ₹50 Lakhs

        if turnover < min_turnover:
            return (
                False,
                f"Equity turnover ₹{turnover:,.0f} below ₹50L threshold (slippage risk)",
            )
        return True, "Healthy equity turnover"


def compute_sip_multiplier(
    stage: int,
    z: float,
    rs: float = 80.0,
    dist_days: int = 0,
    beta: float = 0.5,
    z_star: float = 0.5,
    sigma: float = 1.5,
) -> float:
    """Helper alias for calculate_sip_multiplier directly using z score.

    Formula:
        M_i = S_stage * clip(0.5 + 1.5 * exp(-((z - z*)^2)/(2 * sigma^2)) * (RS/80)^beta * V_pen, 0.5, 2.0)
        For z* = 0.5, sigma = 1.5, 2 * sigma^2 = 4.5.
    """
    if stage in (3, 4):
        return 0.0

    if stage == 1:
        s_stage = 0.5
    elif stage == 2:
        s_stage = 1.0
    else:
        s_stage = 0.5

    import math

    denom = 2.0 * (sigma**2)
    gauss = math.exp(-((z - z_star) ** 2) / denom)
    rs_term = (max(0.0, float(rs)) / 80.0) ** beta
    v_pen = 0.6 if dist_days >= 4 else 1.0

    raw = 0.5 + 1.5 * gauss * rs_term * v_pen
    clipped = max(0.5, min(2.0, raw))
    return float(s_stage * clipped)


def calculate_sip_multiplier(
    df: Optional[pd.DataFrame] = None,
    stage: int = 2,
    rs_score: float = 80.0,
    dist_days: int = 0,
    current_price: Optional[float] = None,
    sma50: Optional[float] = None,
    atr22: Optional[float] = None,
    ticker: Optional[str] = None,
    avg_daily_turnover_1m: Optional[float] = None,
    price_to_nav_spread_pct: Optional[float] = None,
    asset_class: str = "EQUITY",
    return_details: bool = False,
) -> Union[float, Dict[str, Union[float, bool, str]]]:
    """Calculate the Stage-Gated Bell-Curve Pullback Multiplier (M_i) for a single asset.

    Formula:
        z_i = (Price_i - SMA_50,i) / ATR_22,i
        S_stage = 1.0 (Stage 2), 0.5 (Stage 1), 0.0 (Stage 3 or 4)
        V_pen = 0.6 if dist_days >= 4 else 1.0
        If S_stage == 0.0: M_i = 0.0
        Else:
            M_i = S_stage * clip(0.5 + 1.5 * exp(-(z_i - 0.5)^2 / 4.5) * (RS_i / 80)^0.5 * V_pen, 0.5, 2.0)

    Liquidity Guardrail:
        If avg_daily_turnover_1m or price_to_nav_spread_pct is provided, check_liquidity_health is evaluated.
        If the check fails, the multiplier M_i is gated/throttled to 0.0.

    Parameters
    ----------
    df : Optional[pd.DataFrame]
        Historical price data containing 'close', 'high', 'low'.
    stage : int, default 2
        Weinstein stage classification (1, 2, 3, or 4).
    rs_score : float, default 80.0
        Relative strength score. Clipped to max(0.0, rs_score) for root computation.
    dist_days : int, default 0
        Distribution day count.
    current_price : Optional[float]
        Optional override for current price.
    sma50 : Optional[float]
        Optional override for 50-day SMA.
    atr22 : Optional[float]
        Optional override for 22-day ATR.
    ticker : Optional[str]
        Optional ticker symbol for liquidity evaluation.
    avg_daily_turnover_1m : Optional[float]
        1-month average daily turnover in INR.
    price_to_nav_spread_pct : Optional[float]
        Price-to-NAV spread percentage.
    asset_class : str, default 'EQUITY'
        Asset class category.
    return_details : bool, default False
        Whether to return a dictionary with multiplier, liquidity status, and rationale.

    Returns
    -------
    Union[float, Dict[str, Union[float, bool, str]]]
        Calculated SIP multiplier M_i, or detailed dict if return_details is True.
    """
    liquidity_ok = True
    liquidity_rationale = "Liquidity check passed or not evaluated"

    # Liquidity check gating
    if avg_daily_turnover_1m is not None or price_to_nav_spread_pct is not None:
        turnover_val = avg_daily_turnover_1m if avg_daily_turnover_1m is not None else float("inf")
        nav_spread_val = price_to_nav_spread_pct if price_to_nav_spread_pct is not None else 0.0
        liquidity_ok, liquidity_rationale = check_liquidity_health(
            ticker=ticker or "UNKNOWN",
            avg_daily_turnover_1m=turnover_val,
            price_to_nav_spread_pct=nav_spread_val,
            asset_class=asset_class,
        )

    # Stage gating
    if stage == 2:
        s_stage = 1.0
    elif stage == 1:
        s_stage = 0.5
    else:
        # Stage 3 or 4 or unknown
        m_i = 0.0
        if return_details:
            return {
                "multiplier": m_i,
                "liquidity_ok": liquidity_ok,
                "rationale": f"Stage {stage} exit / halt; {liquidity_rationale}",
            }
        return m_i

    # Volume/distribution penalty
    v_pen = 0.6 if dist_days >= 4 else 1.0

    # Extract price, sma50, atr22 if not explicitly provided
    if current_price is None or sma50 is None or atr22 is None:
        if df is None or len(df) < 50:
            # Fallback when insufficient data to calculate z-score
            # Default z_i = 0.5 (ideal pullback) or neutral multiplier
            raw_m = 0.5 + 1.5 * (max(0.0, float(rs_score)) / 80.0) ** 0.5 * v_pen
            m_i = float(np.clip(s_stage * np.clip(raw_m, 0.5, 2.0), 0.0, 2.0))
        else:
            close_series = df["close"].astype(float)
            price_val = float(close_series.iloc[-1]) if current_price is None else float(current_price)
            sma50_val = float(close_series.rolling(window=50).mean().iloc[-1]) if sma50 is None else float(sma50)

            if atr22 is None:
                high_series = df["high"].astype(float)
                low_series = df["low"].astype(float)
                prev_close = close_series.shift(1)
                tr1 = high_series - low_series
                tr2 = (high_series - prev_close).abs()
                tr3 = (low_series - prev_close).abs()
                tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
                atr22_val = float(tr.rolling(window=22).mean().iloc[-1])
            else:
                atr22_val = float(atr22)

            if atr22_val <= 0 or np.isnan(atr22_val) or np.isnan(sma50_val):
                z_i = 0.0
            else:
                z_i = (price_val - sma50_val) / atr22_val

            bell_curve = np.exp(-((z_i - 0.5) ** 2) / 4.5)
            clean_rs = max(0.0, float(rs_score))
            rs_term = (clean_rs / 80.0) ** 0.5
            inner_val = 0.5 + 1.5 * bell_curve * rs_term * v_pen
            clipped_inner = np.clip(inner_val, 0.5, 2.0)
            m_i = float(s_stage * clipped_inner)
    else:
        price_val = float(current_price)
        sma50_val = float(sma50)
        atr22_val = float(atr22)

        if atr22_val <= 0 or np.isnan(atr22_val) or np.isnan(sma50_val):
            z_i = 0.0
        else:
            z_i = (price_val - sma50_val) / atr22_val

        bell_curve = np.exp(-((z_i - 0.5) ** 2) / 4.5)
        clean_rs = max(0.0, float(rs_score))
        rs_term = (clean_rs / 80.0) ** 0.5
        inner_val = 0.5 + 1.5 * bell_curve * rs_term * v_pen
        clipped_inner = np.clip(inner_val, 0.5, 2.0)
        m_i = float(s_stage * clipped_inner)

    # Gate multiplier to 0.0 if liquidity check failed
    if not liquidity_ok:
        m_i = 0.0

    if return_details:
        return {
            "multiplier": m_i,
            "liquidity_ok": liquidity_ok,
            "rationale": liquidity_rationale,
        }

    return m_i


def normalize_sip_budget(
    base_allocations: Dict[str, float],
    multipliers: Dict[str, float],
    total_budget: float,
) -> Dict[str, float]:
    """Normalize SIP allocations across the portfolio so the total equals total_budget.

    Each stock gets a raw scaled weight:
        w_i = base_allocations[i] * multipliers[i]
    Then normalized to sum to total_budget:
        allocated_i = total_budget * (w_i / sum(w_j))

    If all w_i are 0 (e.g. all stocks in stage 3 or 4), returns 0.0 for all stocks.

    Parameters
    ----------
    base_allocations : dict
        Mapping of stock code to base SIP allocation (percentage or absolute amount).
    multipliers : dict
        Mapping of stock code to SIP quant multiplier M_i.
    total_budget : float
        Total periodic SIP budget to distribute.

    Returns
    -------
    dict
        Mapping of stock code to allocated budget amount.
    """
    if total_budget <= 0:
        return {k: 0.0 for k in base_allocations}

    raw_weighted: Dict[str, float] = {}
    for ticker, base_alloc in base_allocations.items():
        mult = multipliers.get(ticker, 1.0)
        # Multipliers and base allocations should not be negative
        valid_base = max(0.0, float(base_alloc))
        valid_mult = max(0.0, float(mult))
        raw_weighted[ticker] = valid_base * valid_mult

    total_weighted = sum(raw_weighted.values())

    if total_weighted <= 0:
        return {k: 0.0 for k in base_allocations}

    return {
        ticker: round(float(total_budget * (weight / total_weighted)), 2) for ticker, weight in raw_weighted.items()
    }
