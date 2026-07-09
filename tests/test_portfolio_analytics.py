import unittest
import pandas as pd
import numpy as np

from algo.portfolio_analytics import compute_concentration, compute_beta, rollup_stage_health

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
