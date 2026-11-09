"""Unit tests for Chandelier Exit dynamic scaling, SIP quant multipliers, and budget normalization."""

import numpy as np
import pandas as pd
import pytest

from algo.action_classifier import get_stock_action
from algo.sip_allocator import calculate_sip_multiplier, check_liquidity_health, normalize_sip_budget
from algo.trailing_stop import calculate_chandelier_exit


def create_mock_ohlcv(n_bars: int = 150, start_price: float = 100.0, trend: float = 0.5, spread: float = 2.0) -> pd.DataFrame:
    """Generate deterministic synthetic OHLCV data for testing."""
    dates = pd.date_range(start="2024-01-01", periods=n_bars, freq="D").strftime("%Y-%m-%d").tolist()
    closes = []
    highs = []
    lows = []
    opens = []
    volumes = []

    price = start_price
    for i in range(n_bars):
        price += trend + (0.5 if i % 2 == 0 else -0.4)
        c = round(price, 2)
        h = round(c + spread, 2)
        l = round(c - spread, 2)
        o = round(c - 0.2, 2)
        v = 100000 + (i * 500)
        closes.append(c)
        highs.append(h)
        lows.append(l)
        opens.append(o)
        volumes.append(v)

    return pd.DataFrame({
        "date": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


# ---------------------------------------------------------------------------
# 1. Chandelier Exit Dynamic Scaling Tests
# ---------------------------------------------------------------------------

def test_chandelier_exit_backward_compatibility():
    """Verify calculate_chandelier_exit without new params returns a scalar float stop_loss."""
    df = create_mock_ohlcv(50)
    stop_loss = calculate_chandelier_exit(df)
    assert isinstance(stop_loss, float)
    assert stop_loss > 0.0

    # Explicit atr_multiplier backward compatibility
    custom_stop = calculate_chandelier_exit(df, atr_multiplier=3.5)
    assert custom_stop < stop_loss  # higher multiplier = lower stop


def test_chandelier_exit_return_details():
    """Verify return_details returns both stop_loss and dynamic k_t multiplier."""
    df = create_mock_ohlcv(120)
    res = calculate_chandelier_exit(df, stage=2, return_details=True)
    assert isinstance(res, dict)
    assert "stop_loss" in res
    assert "k_t" in res
    assert 2.0 <= res["k_t"] <= 4.2


def test_chandelier_exit_stage_base_multipliers():
    """Verify base k_stage varies with Weinstein stages: Stage 2 (3.2), Stage 3 (2.2), Stage 1/4 (2.8)."""
    # Using df with identical recent and long-term ATR (ATR22 == ATR100)
    df = create_mock_ohlcv(150, trend=0.0, spread=2.0)

    res_stage2 = calculate_chandelier_exit(df, stage=2, return_details=True)
    res_stage3 = calculate_chandelier_exit(df, stage=3, return_details=True)
    res_stage1 = calculate_chandelier_exit(df, stage=1, return_details=True)
    res_stage4 = calculate_chandelier_exit(df, stage=4, return_details=True)

    # With steady ATR ratio and no VIX, k_t should match base k_stage
    assert pytest.approx(res_stage2["k_t"], 0.05) == 3.2
    assert pytest.approx(res_stage3["k_t"], 0.05) == 2.2
    assert pytest.approx(res_stage1["k_t"], 0.05) == 2.8
    assert pytest.approx(res_stage4["k_t"], 0.05) == 2.8


def test_chandelier_exit_asset_class():
    """Verify commodity/gold/reit gets base multiplier 3.5."""
    df = create_mock_ohlcv(150, trend=0.0, spread=2.0)
    res_gold = calculate_chandelier_exit(df, stage=2, asset_class="GOLD_ETF", return_details=True)
    res_reit = calculate_chandelier_exit(df, stage=2, asset_class="REIT", return_details=True)
    res_comm = calculate_chandelier_exit(df, stage=2, asset_class="COMMODITY", return_details=True)

    assert pytest.approx(res_gold["k_t"], 0.05) == 3.5
    assert pytest.approx(res_reit["k_t"], 0.05) == 3.5
    assert pytest.approx(res_comm["k_t"], 0.05) == 3.5


def test_chandelier_exit_vix_spike_and_compression():
    """Verify VIX spike contracts k_t (tightens stop) while calm VIX expands k_t."""
    df = create_mock_ohlcv(120, trend=0.0, spread=2.0)

    # Base vix series: 50 days at 15.0
    vix_calm = pd.Series([15.0] * 50)
    res_calm = calculate_chandelier_exit(df, vix_series=vix_calm, stage=2, return_details=True)

    # VIX spike: 49 days at 15.0, final day spikes to 30.0 (VIX_t >> VIX_SMA50)
    vix_spike = pd.Series([15.0] * 49 + [30.0])
    res_spike = calculate_chandelier_exit(df, vix_series=vix_spike, stage=2, return_details=True)

    # VIX crash/calm: final day drops to 10.0 (VIX_t << VIX_SMA50)
    vix_low = pd.Series([15.0] * 49 + [10.0])
    res_low = calculate_chandelier_exit(df, vix_series=vix_low, stage=2, return_details=True)

    assert res_spike["k_t"] < res_calm["k_t"]
    assert res_low["k_t"] > res_calm["k_t"]
    # Clipping bounds check [2.0, 4.2]
    assert 2.0 <= res_spike["k_t"] <= 4.2
    assert 2.0 <= res_low["k_t"] <= 4.2


def test_chandelier_exit_fallback_short_history():
    """Verify graceful fallback when length < 100 or insufficient VIX data."""
    df_short = create_mock_ohlcv(30)
    res_short = calculate_chandelier_exit(df_short, stage=2, return_details=True)
    assert res_short["k_t"] == 3.2

    # Under period (22 bars)
    df_tiny = create_mock_ohlcv(10)
    assert calculate_chandelier_exit(df_tiny) == 0.0
    assert calculate_chandelier_exit(df_tiny, return_details=True)["stop_loss"] == 0.0


# ---------------------------------------------------------------------------
# 2. SIP Quant Multiplier Tests
# ---------------------------------------------------------------------------

def test_sip_multiplier_stage_gating():
    """Verify S_stage: Stage 2 -> 1.0; Stage 1 -> 0.5; Stage 3/4 -> 0.0."""
    df = create_mock_ohlcv(60)
    m_stage2 = calculate_sip_multiplier(df, stage=2, rs_score=80.0, dist_days=0)
    m_stage1 = calculate_sip_multiplier(df, stage=1, rs_score=80.0, dist_days=0)
    m_stage3 = calculate_sip_multiplier(df, stage=3, rs_score=80.0, dist_days=0)
    m_stage4 = calculate_sip_multiplier(df, stage=4, rs_score=80.0, dist_days=0)

    assert m_stage2 > 0.0
    assert pytest.approx(m_stage1, 0.01) == m_stage2 * 0.5
    assert m_stage3 == 0.0
    assert m_stage4 == 0.0


def test_sip_multiplier_bell_curve_pullback_sweet_spot():
    """Verify peak multiplier occurs at z_i ~ 0.5 (slight pullback to SMA50)."""
    # Using explicit overrides: price, sma50, atr22
    # At z = 0.5 -> Price = SMA50 + 0.5 * ATR22
    # Peak inner value = 0.5 + 1.5 * 1.0 * 1.0 * 1.0 = 2.0
    m_peak = calculate_sip_multiplier(
        df=None,
        stage=2,
        rs_score=80.0,
        dist_days=0,
        current_price=101.0,
        sma50=100.0,
        atr22=2.0,  # z = (101 - 100) / 2 = 0.5
    )
    assert pytest.approx(m_peak, 0.01) == 2.0

    # Overextended breakout: z = 3.5 -> Price = 107, SMA50 = 100, ATR22 = 2.0
    m_overextended = calculate_sip_multiplier(
        df=None,
        stage=2,
        rs_score=80.0,
        dist_days=0,
        current_price=107.0,
        sma50=100.0,
        atr22=2.0,
    )
    # Bell curve exp(-(3.0)^2 / 4.5) = exp(-2) = 0.135
    # Inner = 0.5 + 1.5 * 0.135 = 0.702
    assert m_overextended < m_peak
    assert 0.5 <= m_overextended <= 2.0


def test_sip_multiplier_distribution_day_penalty():
    """Verify V_pen: dist_days >= 4 scales by 0.6."""
    m_normal = calculate_sip_multiplier(
        df=None, stage=2, rs_score=80.0, dist_days=2, current_price=101.0, sma50=100.0, atr22=2.0
    )
    m_penalized = calculate_sip_multiplier(
        df=None, stage=2, rs_score=80.0, dist_days=4, current_price=101.0, sma50=100.0, atr22=2.0
    )
    # At z=0.5, RS=80:
    # normal inner = 0.5 + 1.5 * 1.0 = 2.0
    # penalized inner = 0.5 + 1.5 * 0.6 = 1.4
    assert pytest.approx(m_normal, 0.01) == 2.0
    assert pytest.approx(m_penalized, 0.01) == 1.4


def test_sip_multiplier_relative_strength_scaling():
    """Verify higher RS increases multiplier, while low/zero RS reduces it."""
    m_high_rs = calculate_sip_multiplier(df=None, stage=2, rs_score=100.0, dist_days=0, current_price=102.0, sma50=100.0, atr22=2.0)
    m_low_rs = calculate_sip_multiplier(df=None, stage=2, rs_score=40.0, dist_days=0, current_price=102.0, sma50=100.0, atr22=2.0)
    m_zero_rs = calculate_sip_multiplier(df=None, stage=2, rs_score=0.0, dist_days=0, current_price=102.0, sma50=100.0, atr22=2.0)

    assert m_high_rs > m_low_rs > m_zero_rs
    assert pytest.approx(m_zero_rs, 0.01) == 0.5  # clipped to min 0.5


# ---------------------------------------------------------------------------
# 3. Budget Normalization Tests
# ---------------------------------------------------------------------------

def test_normalize_sip_budget_proportional_distribution():
    """Verify total budget is distributed according to base allocations and multipliers."""
    base_allocations = {"STOCK_A": 5000.0, "STOCK_B": 5000.0}
    multipliers = {"STOCK_A": 2.0, "STOCK_B": 1.0}
    total_budget = 30000.0

    # Stock A weight: 5000 * 2.0 = 10000 (2/3) -> 20000.0
    # Stock B weight: 5000 * 1.0 = 5000 (1/3)  -> 10000.0
    allocated = normalize_sip_budget(base_allocations, multipliers, total_budget)

    assert pytest.approx(allocated["STOCK_A"], 0.1) == 20000.0
    assert pytest.approx(allocated["STOCK_B"], 0.1) == 10000.0
    assert pytest.approx(sum(allocated.values()), 0.1) == total_budget


def test_normalize_sip_budget_all_paused():
    """Verify edge case where all multipliers are 0 (e.g. all stage 4)."""
    base_allocations = {"STOCK_A": 5000.0, "STOCK_B": 5000.0}
    multipliers = {"STOCK_A": 0.0, "STOCK_B": 0.0}
    total_budget = 10000.0

    allocated = normalize_sip_budget(base_allocations, multipliers, total_budget)
    assert allocated["STOCK_A"] == 0.0
    assert allocated["STOCK_B"] == 0.0


def test_normalize_sip_budget_zero_or_negative_budget():
    """Verify total_budget <= 0 returns zero allocations safely."""
    base_allocations = {"STOCK_A": 5000.0}
    multipliers = {"STOCK_A": 1.5}
    assert normalize_sip_budget(base_allocations, multipliers, 0.0) == {"STOCK_A": 0.0}
    assert normalize_sip_budget(base_allocations, multipliers, -100.0) == {"STOCK_A": 0.0}


# ---------------------------------------------------------------------------
# 4. Action Classifier Integration Tests
# ---------------------------------------------------------------------------

def test_action_classifier_attaches_dynamic_fields():
    """Verify get_stock_action attaches k_t and sip_multiplier."""
    stock_df = create_mock_ohlcv(220, trend=0.8, spread=2.0)
    nifty_df = create_mock_ohlcv(220, trend=0.4, spread=1.5)

    res = get_stock_action("TEST_STOCK", stock_df, nifty_df, is_holding=True)

    assert "k_t" in res
    assert "sip_multiplier" in res
    assert "stop_loss" in res
    assert 2.0 <= res["k_t"] <= 4.2
    assert 0.0 <= res["sip_multiplier"] <= 2.0


# ---------------------------------------------------------------------------
# 5. Liquidity & Bid-Ask / NAV Spread Guardrail Tests
# ---------------------------------------------------------------------------

def test_check_liquidity_health_reit_invit():
    """Verify REIT/InvIT liquidity requirements: ADTV >= ₹15L and |Price-NAV|/NAV <= 8%."""
    # Healthy case: Turnover ₹20L, Spread 3%
    ok, msg = check_liquidity_health("EMBASSY", avg_daily_turnover_1m=20_00_000, price_to_nav_spread_pct=3.0, asset_class="REIT_INVIT")
    assert ok is True
    assert "Healthy" in msg

    # Boundary case: Turnover ₹15L exactly, Spread 8% exactly
    ok_boundary, _ = check_liquidity_health("POWERGRID_INVIT", avg_daily_turnover_1m=15_00_000, price_to_nav_spread_pct=8.0, asset_class="INVIT")
    assert ok_boundary is True

    # Negative case: Illiquid turnover (e.g. ₹10L < ₹15L)
    ok_low_vol, msg_low_vol = check_liquidity_health("ILLIQUID_REIT", avg_daily_turnover_1m=10_00_000, price_to_nav_spread_pct=2.0, asset_class="REIT")
    assert ok_low_vol is False
    assert "below ₹15L threshold" in msg_low_vol

    # Negative case: Excessive NAV premium (e.g. 10% > 8%)
    ok_prem, msg_prem = check_liquidity_health("OVERPRICED_INVIT", avg_daily_turnover_1m=25_00_000, price_to_nav_spread_pct=10.0, asset_class="REIT_INVIT")
    assert ok_prem is False
    assert "breaches 8.0% guardrail" in msg_prem

    # Negative case: Extreme discount indicating distress (e.g. -12% spread -> abs(spread)=12% > 8%)
    ok_disc, msg_disc = check_liquidity_health("DISTRESSED_REIT", avg_daily_turnover_1m=25_00_000, price_to_nav_spread_pct=-12.0, asset_class="REIT")
    assert ok_disc is False
    assert "breaches 8.0% guardrail" in msg_disc


def test_check_liquidity_health_debt_etf():
    """Verify Debt ETF liquidity requirements: ADTV >= ₹25L and spread <= 0.5%."""
    # Healthy case: Turnover ₹30L, spread 0.2%
    ok, _ = check_liquidity_health("LIQUIDBEES", avg_daily_turnover_1m=30_00_000, price_to_nav_spread_pct=0.2, asset_class="DEBT_ETF")
    assert ok is True

    # Negative case: Turnover ₹20L < ₹25L
    ok_low_vol, msg_low_vol = check_liquidity_health("DEBT_ETF_A", avg_daily_turnover_1m=20_00_000, price_to_nav_spread_pct=0.1, asset_class="DEBT_ETF")
    assert ok_low_vol is False
    assert "below ₹25L threshold" in msg_low_vol

    # Negative case: Spread 0.8% > 0.5%
    ok_spread, msg_spread = check_liquidity_health("DEBT_ETF_B", avg_daily_turnover_1m=50_00_000, price_to_nav_spread_pct=0.8, asset_class="DEBT_ETF")
    assert ok_spread is False
    assert "breaches 0.5% guardrail" in msg_spread


def test_check_liquidity_health_equity():
    """Verify Equity (small/midcaps) requires ADTV >= ₹50L to prevent slippage."""
    # Healthy case: ₹60L turnover
    ok, _ = check_liquidity_health("MIDCAP_STOCK", avg_daily_turnover_1m=60_00_000, price_to_nav_spread_pct=0.0, asset_class="EQUITY")
    assert ok is True

    # Illiquid case: ₹35L turnover (< ₹50L)
    ok_illiquid, msg_illiquid = check_liquidity_health("MICROCAP_STOCK", avg_daily_turnover_1m=35_00_000, price_to_nav_spread_pct=0.0, asset_class="EQUITY")
    assert ok_illiquid is False
    assert "below ₹50L threshold" in msg_illiquid


def test_sip_multiplier_gated_by_liquidity():
    """Verify calculate_sip_multiplier gates multiplier to 0.0 when liquidity health fails."""
    # Normal stage 2 without liquidity failure -> multiplier > 0.5
    m_normal = calculate_sip_multiplier(
        df=None,
        stage=2,
        rs_score=80.0,
        dist_days=0,
        current_price=101.0,
        sma50=100.0,
        atr22=2.0,
        ticker="HEALTHY_REIT",
        avg_daily_turnover_1m=20_00_000,
        price_to_nav_spread_pct=2.0,
        asset_class="REIT_INVIT",
    )
    assert m_normal > 1.0

    # Illiquid InvIT -> throttled to 0.0
    m_illiquid = calculate_sip_multiplier(
        df=None,
        stage=2,
        rs_score=80.0,
        dist_days=0,
        current_price=101.0,
        sma50=100.0,
        atr22=2.0,
        ticker="ILLIQUID_REIT",
        avg_daily_turnover_1m=5_00_000,  # 5 Lakhs < 15 Lakhs
        price_to_nav_spread_pct=2.0,
        asset_class="REIT_INVIT",
    )
    assert m_illiquid == 0.0

    # Stretched NAV premium -> throttled to 0.0
    m_stretched = calculate_sip_multiplier(
        df=None,
        stage=2,
        rs_score=80.0,
        dist_days=0,
        current_price=101.0,
        sma50=100.0,
        atr22=2.0,
        ticker="PREMIUM_REIT",
        avg_daily_turnover_1m=20_00_000,
        price_to_nav_spread_pct=11.5,  # 11.5% > 8%
        asset_class="REIT_INVIT",
    )
    assert m_stretched == 0.0

    # Detailed return check
    details = calculate_sip_multiplier(
        df=None,
        stage=2,
        rs_score=80.0,
        ticker="PREMIUM_REIT",
        avg_daily_turnover_1m=20_00_000,
        price_to_nav_spread_pct=11.5,
        asset_class="REIT_INVIT",
        return_details=True,
    )
    assert details["multiplier"] == 0.0
    assert details["liquidity_ok"] is False
    assert "breaches 8.0% guardrail" in details["rationale"]


def test_action_classifier_downgrades_on_liquidity_failure():
    """Verify get_stock_action downgrades recommendation from BUY MORE to HOLD / PAUSE SIP on liquidity failure."""
    stock_df = create_mock_ohlcv(220, trend=0.8, spread=2.0)
    nifty_df = create_mock_ohlcv(220, trend=0.4, spread=1.5)

    # 1. Healthy Stage 2 REIT -> BUY MORE / CONTINUE SIP
    res_healthy = get_stock_action(
        stock_code="EMBASSY_REIT",
        stock_df=stock_df,
        nifty_df=nifty_df,
        is_holding=True,
        asset_class="REIT_INVIT",
        avg_daily_turnover_1m=25_00_000,
        price_to_nav_spread_pct=2.0,
    )
    assert res_healthy["action"] == "BUY MORE / CONTINUE SIP"
    assert res_healthy["sip_multiplier"] > 0.0

    # 2. Stretched NAV premium -> Downgraded to HOLD / PAUSE SIP and multiplier 0.0
    res_stretched = get_stock_action(
        stock_code="EXPENSIVE_REIT",
        stock_df=stock_df,
        nifty_df=nifty_df,
        is_holding=True,
        asset_class="REIT_INVIT",
        avg_daily_turnover_1m=25_00_000,
        price_to_nav_spread_pct=12.0,  # > 8%
    )
    assert res_stretched["action"] == "HOLD / PAUSE SIP"
    assert res_stretched["sip_multiplier"] == 0.0
    assert "Liquidity/Spread guardrail failed" in res_stretched["rationale"]

    # 3. Illiquid turnover InvIT -> Downgraded to HOLD / PAUSE SIP
    res_illiquid = get_stock_action(
        stock_code="ILLIQUID_INVIT",
        stock_df=stock_df,
        nifty_df=nifty_df,
        is_holding=False,
        asset_class="REIT_INVIT",
        avg_daily_turnover_1m=8_00_000,  # < ₹15L
        price_to_nav_spread_pct=3.0,
    )
    assert res_illiquid["action"] == "HOLD / PAUSE SIP"
    assert res_illiquid["sip_multiplier"] == 0.0
    assert "Liquidity/Spread guardrail failed" in res_illiquid["rationale"]
