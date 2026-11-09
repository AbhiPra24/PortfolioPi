import unittest

import aiosqlite
import numpy as np
import pandas as pd
import pytest

from algo.portfolio_analytics import (
    check_guardrails,
    compute_beta,
    compute_concentration,
    compute_drawdown_series,
    compute_risk_metrics,
    optimize_portfolio_weights,
    rollup_stage_health,
)
from app_config import settings
from core.database import init_db
from core.portfolio_analytics import (
    compute_downside_deviation,
    compute_jensens_alpha,
    compute_rolling_max_drawdown,
    compute_sortino_ratio,
    compute_treynor_ratio,
    update_portfolio_risk_metrics,
)


class TestPortfolioAnalytics(unittest.TestCase):
    def test_compute_concentration(self):
        # 5 holdings
        data = {
            "Stock Code": ["RELIANCE", "TCS", "INFY", "HDFC", "ITC"],
            "Current Value": [50000.0, 30000.0, 10000.0, 6000.0, 4000.0],
        }
        df = pd.DataFrame(data)

        # Total = 100,000. Top 5 = 100,000 (100%)
        res = compute_concentration(df, top_n=5)
        self.assertEqual(res["top_n_pct"], 100.0)
        self.assertEqual(len(res["top_holdings"]), 5)
        self.assertEqual(res["top_holdings"][0]["Stock Code"], "RELIANCE")

        # Top 2 = 80,000 (80%)
        res_top2 = compute_concentration(df, top_n=2)
        self.assertEqual(res_top2["top_n_pct"], 80.0)
        self.assertEqual(len(res_top2["top_holdings"]), 2)

    def test_compute_beta_correlated(self):
        # Create a stock that changes exactly as the index (beta should be close to 1)
        np.random.seed(42)
        index_closes = pd.Series([100.0 * (1.0 + 0.01 * x) for x in range(100)])
        stock_closes = index_closes * 1.5  # monotonic scaled, returns will be identical

        beta = compute_beta(stock_closes, index_closes, window=90)
        self.assertAlmostEqual(beta, 1.0, places=4)

    def test_rollup_stage_health(self):
        stage_rows = [(1,), (2,), (2,), (3,), (4,), (4,), (4,)]
        counts = rollup_stage_health(stage_rows)

        self.assertEqual(counts[1], 1)
        self.assertEqual(counts[2], 2)
        self.assertEqual(counts[3], 1)
        self.assertEqual(counts[4], 3)
        self.assertEqual(sum(counts.values()), len(stage_rows))

    def test_compute_risk_metrics(self):
        np.random.seed(42)
        daily_rets = pd.Series(np.random.normal(0.0008, 0.012, 252))
        bench_rets = pd.Series(np.random.normal(0.0005, 0.010, 252))

        metrics = compute_risk_metrics(daily_rets, bench_rets, risk_free_rate=0.065)
        self.assertIn("sharpe_ratio", metrics)
        self.assertIn("sortino_ratio", metrics)
        self.assertIn("max_drawdown", metrics)
        self.assertIn("var_95", metrics)
        self.assertIn("cvar_95", metrics)
        self.assertIn("beta", metrics)
        self.assertIn("alpha", metrics)
        self.assertIn("treynor_ratio", metrics)
        self.assertIn("jensens_alpha", metrics)
        self.assertLessEqual(metrics["max_drawdown"], 0.0)

    def test_compute_drawdown_series(self):
        prices = pd.Series([100, 105, 110, 99, 105, 120])
        dd = compute_drawdown_series(prices)
        self.assertEqual(dd.iloc[2], 0.0)  # at peak 110
        self.assertAlmostEqual(dd.iloc[3], -10.0, places=1)  # 99 is 10% below 110
        self.assertEqual(dd.iloc[5], 0.0)  # new peak 120

    def test_optimize_portfolio_weights(self):
        dates = pd.date_range("2025-01-01", periods=100)
        np.random.seed(42)
        df_prices = pd.DataFrame(
            {
                "STOCK_A": 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.01, 100))),
                "STOCK_B": 100 * np.exp(np.cumsum(np.random.normal(0.0005, 0.02, 100))),
                "STOCK_C": 100 * np.exp(np.cumsum(np.random.normal(0.0002, 0.008, 100))),
            },
            index=dates,
        )

        w_sharpe = optimize_portfolio_weights(df_prices, objective="max_sharpe")
        self.assertAlmostEqual(sum(w_sharpe.values()), 100.0, delta=1.0)

        w_min_vol = optimize_portfolio_weights(df_prices, objective="min_volatility")
        self.assertAlmostEqual(sum(w_min_vol.values()), 100.0, delta=1.0)

        w_inv_vol = optimize_portfolio_weights(df_prices, objective="inverse_volatility")
        self.assertAlmostEqual(sum(w_inv_vol.values()), 100.0, delta=1.0)

    def test_check_guardrails(self):
        holdings = pd.DataFrame(
            {"Stock Code": ["HEAVY_STOCK", "NORMAL_STOCK", "TINY_STOCK"], "Current Value": [85000.0, 10000.0, 5000.0]}
        )
        alerts = check_guardrails(holdings, max_position_cap_pct=15.0, min_holdings_count=5)
        rules = [a["rule"] for a in alerts]
        self.assertIn("MAX_CONCENTRATION_BREACH", rules)
        self.assertIn("UNDER_DIVERSIFICATION", rules)

    # -------------------------------------------------------------------------
    # Comprehensive Downside Telemetry & Edge Case Tests
    # -------------------------------------------------------------------------

    def test_compute_downside_deviation_standard(self):
        target = 0.06 / 252
        returns = pd.Series([0.01, -0.02, 0.03, -0.01, 0.02])
        # underperformance:
        # min(0, 0.01 - t) = 0
        # min(0, -0.02 - t) = -0.02 - t
        # min(0, 0.03 - t) = 0
        # min(0, -0.01 - t) = -0.01 - t
        # min(0, 0.02 - t) = 0
        diff = np.minimum(0.0, returns.to_numpy() - target)
        expected = float(np.sqrt(np.mean(diff**2)))
        actual = compute_downside_deviation(returns, target_return=target)
        self.assertAlmostEqual(actual, expected, places=7)

    def test_compute_sortino_ratio_standard(self):
        target = 0.06 / 252
        returns = pd.Series([0.005, -0.002, 0.008, 0.001, -0.003])
        sortino = compute_sortino_ratio(returns, target_return=target)
        downside_dev = compute_downside_deviation(returns, target_return=target)
        expected = (float(returns.mean()) - target) / downside_dev
        self.assertAlmostEqual(sortino, expected, places=5)

    def test_compute_sortino_zero_downside_volatility(self):
        """When all returns are strictly higher than target return, downside volatility is 0."""
        target = 0.06 / 252
        all_gain_returns = pd.Series([0.01, 0.02, 0.015, 0.03, 0.005])
        # Downside deviation must be 0.0
        dd = compute_downside_deviation(all_gain_returns, target_return=target)
        self.assertEqual(dd, 0.0)
        # Sortino must safely return 0.0 without ZeroDivisionError
        sortino = compute_sortino_ratio(all_gain_returns, target_return=target)
        self.assertEqual(sortino, 0.0)

    def test_compute_sortino_empty_or_nan(self):
        target = 0.06 / 252
        self.assertEqual(compute_sortino_ratio([], target_return=target), 0.0)
        self.assertEqual(compute_sortino_ratio([np.nan, np.nan], target_return=target), 0.0)
        self.assertEqual(compute_downside_deviation([]), 0.0)

    def test_compute_treynor_ratio_standard(self):
        portfolio_ret = 0.15
        rf = 0.06
        beta = 1.2
        expected = (0.15 - 0.06) / 1.2
        self.assertAlmostEqual(compute_treynor_ratio(portfolio_ret, rf, beta), expected, places=6)

    def test_compute_treynor_ratio_zero_beta(self):
        """Zero systematic risk beta must safely return 0.0 without ZeroDivisionError."""
        self.assertEqual(compute_treynor_ratio(0.12, 0.06, 0.0), 0.0)
        self.assertEqual(compute_treynor_ratio(0.12, 0.06, np.nan), 0.0)
        self.assertEqual(compute_treynor_ratio(0.12, 0.06, None), 0.0)

    def test_compute_treynor_ratio_negative_excess(self):
        """Treynor ratio can be negative when portfolio underperforms risk free rate."""
        res = compute_treynor_ratio(0.04, 0.06, 1.0)
        self.assertAlmostEqual(res, -0.02, places=6)

    def test_compute_jensens_alpha_positive_and_negative(self):
        rf = 0.06
        rm = 0.12
        beta = 1.1

        # Positive alpha case
        rp_good = 0.18
        # Expected = 0.06 + 1.1 * (0.12 - 0.06) = 0.06 + 0.066 = 0.126
        # Alpha = 0.18 - 0.126 = 0.054
        alpha_good = compute_jensens_alpha(rp_good, rm, rf, beta)
        self.assertAlmostEqual(alpha_good, 0.054, places=6)

        # Negative alpha case
        rp_bad = 0.10
        alpha_bad = compute_jensens_alpha(rp_bad, rm, rf, beta)
        self.assertAlmostEqual(alpha_bad, -0.026, places=6)
        self.assertLess(alpha_bad, 0.0)

    def test_compute_jensens_alpha_edge_cases(self):
        self.assertEqual(compute_jensens_alpha(0.10, np.nan, 0.06, 1.0), 0.0)
        self.assertEqual(compute_jensens_alpha(None, 0.10, 0.06, 1.0), 0.0)

    def test_compute_rolling_max_drawdown_standard(self):
        curve = [100.0, 120.0, 90.0, 110.0, 80.0, 130.0]
        # Peak 120 -> trough 80 => (80 - 120) / 120 = -40 / 120 = -0.3333333333333333
        mdd = compute_rolling_max_drawdown(curve)
        self.assertAlmostEqual(mdd, -1.0 / 3.0, places=6)

    def test_compute_rolling_max_drawdown_monotone_increasing(self):
        curve = [100.0, 105.0, 110.0, 120.0, 130.0]
        self.assertEqual(compute_rolling_max_drawdown(curve), 0.0)

    def test_compute_rolling_max_drawdown_flat_and_empty(self):
        self.assertEqual(compute_rolling_max_drawdown([100.0, 100.0, 100.0]), 0.0)
        self.assertEqual(compute_rolling_max_drawdown([]), 0.0)
        self.assertEqual(compute_rolling_max_drawdown([100.0]), 0.0)


@pytest.mark.anyio
async def test_update_portfolio_risk_metrics_pipeline(tmp_path, monkeypatch):
    """Test full asynchronous database risk telemetry update pipeline."""
    test_db_path = str(tmp_path / "test_downside_analytics.db")
    monkeypatch.setattr(settings, "db_path", test_db_path)

    await init_db()

    async with aiosqlite.connect(test_db_path) as db:
        # Populate mock portfolio history
        base_val = 100000.0
        dates = pd.date_range("2026-01-01", periods=30, freq="D")
        for i, dt in enumerate(dates):
            val = base_val * (1.0 + 0.005 * i - 0.01 * (i % 3))
            await db.execute(
                """
                INSERT INTO portfolio_value_history (timestamp, total_invested, total_current_value, total_pnl)
                VALUES (?, ?, ?, ?)
            """,
                (dt.isoformat(), 100000.0, val, val - 100000.0),
            )

        # Populate mock NIFTY benchmark
        for i, dt in enumerate(dates):
            close = 20000.0 * (1.0 + 0.003 * i)
            await db.execute(
                """
                INSERT INTO ohlcv_cache (stock_code, date, open, high, low, close, volume)
                VALUES ('_NIFTY50', ?, 20000, 20100, 19900, ?, 1000000)
            """,
                (dt.strftime("%Y-%m-%d"), close),
            )
        await db.commit()

        # Run pipeline
        res = await update_portfolio_risk_metrics(db, risk_free_rate=0.065)
        assert res is not None
        assert "sortino_ratio" in res
        assert "treynor_ratio" in res
        assert "jensens_alpha" in res
        assert "max_drawdown" in res
        assert "downside_deviation" in res

        # Verify persisted table row
        async with db.execute("SELECT * FROM portfolio_risk_metrics") as cur:
            rows = await cur.fetchall()
            assert len(rows) == 1
