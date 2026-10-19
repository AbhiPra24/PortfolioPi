import unittest
import numpy as np
import pandas as pd

from algo.portfolio_analytics import (
    compute_beta,
    compute_concentration,
    compute_drawdown_series,
    compute_risk_metrics,
    optimize_portfolio_weights,
    check_guardrails,
    rollup_stage_health,
)


class TestPortfolioAnalytics(unittest.TestCase):
    def test_compute_concentration(self):
        # 5 holdings
        data = {
            "Stock Code": ["RELIANCE", "TCS", "INFY", "HDFC", "ITC"],
            "Current Value": [50000.0, 30000.0, 10000.0, 6000.0, 4000.0]
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
        stock_closes = index_closes * 1.5 # monotonic scaled, returns will be identical

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
        self.assertLessEqual(metrics["max_drawdown"], 0.0)

    def test_compute_drawdown_series(self):
        prices = pd.Series([100, 105, 110, 99, 105, 120])
        dd = compute_drawdown_series(prices)
        self.assertEqual(dd.iloc[2], 0.0)  # at peak 110
        self.assertAlmostEqual(dd.iloc[3], -10.0, places=1) # 99 is 10% below 110
        self.assertEqual(dd.iloc[5], 0.0)  # new peak 120

    def test_optimize_portfolio_weights(self):
        dates = pd.date_range("2025-01-01", periods=100)
        np.random.seed(42)
        df_prices = pd.DataFrame({
            "STOCK_A": 100 * np.exp(np.cumsum(np.random.normal(0.001, 0.01, 100))),
            "STOCK_B": 100 * np.exp(np.cumsum(np.random.normal(0.0005, 0.02, 100))),
            "STOCK_C": 100 * np.exp(np.cumsum(np.random.normal(0.0002, 0.008, 100))),
        }, index=dates)

        w_sharpe = optimize_portfolio_weights(df_prices, objective="max_sharpe")
        self.assertAlmostEqual(sum(w_sharpe.values()), 100.0, delta=1.0)

        w_min_vol = optimize_portfolio_weights(df_prices, objective="min_volatility")
        self.assertAlmostEqual(sum(w_min_vol.values()), 100.0, delta=1.0)

        w_inv_vol = optimize_portfolio_weights(df_prices, objective="inverse_volatility")
        self.assertAlmostEqual(sum(w_inv_vol.values()), 100.0, delta=1.0)

    def test_check_guardrails(self):
        holdings = pd.DataFrame({
            "Stock Code": ["HEAVY_STOCK", "NORMAL_STOCK", "TINY_STOCK"],
            "Current Value": [85000.0, 10000.0, 5000.0]
        })
        alerts = check_guardrails(holdings, max_position_cap_pct=15.0, min_holdings_count=5)
        # Should flag under-diversification and HEAVY_STOCK concentration breach (85%)
        rules = [a["rule"] for a in alerts]
        self.assertIn("MAX_CONCENTRATION_BREACH", rules)
        self.assertIn("UNDER_DIVERSIFICATION", rules)
