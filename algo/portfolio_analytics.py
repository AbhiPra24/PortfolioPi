"""Portfolio-level analytics: concentration, correlation/beta, stage-health rollup."""
import pandas as pd

def compute_concentration(holdings_df: pd.DataFrame, top_n=5) -> dict:
    total = holdings_df['Current Value'].sum()
    top = holdings_df.nlargest(top_n, 'Current Value')
    return {
        "top_n_pct": float(top['Current Value'].sum() / total * 100) if total else 0.0,
        "top_holdings": top[['Stock Code', 'Current Value']].to_dict('records'),
    }

def compute_beta(stock_closes: pd.Series, index_closes: pd.Series, window=252) -> float:
    stock_returns = stock_closes.pct_change().dropna().tail(window)
    index_returns = index_closes.pct_change().dropna().tail(window)
    n = min(len(stock_returns), len(index_returns))
    if n < 30:
        return 0.0
    stock_returns, index_returns = stock_returns.tail(n), index_returns.tail(n)
    covariance = stock_returns.cov(index_returns)
    variance = index_returns.var()
    return float(covariance / variance) if variance else 0.0

def rollup_stage_health(stage_rows: list[tuple]) -> dict[int, int]:
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for row in stage_rows:
        stage = row[0]
        if stage in counts:
            counts[stage] += 1
    return counts
