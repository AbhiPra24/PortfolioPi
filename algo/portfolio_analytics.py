"""Portfolio-level analytics: concentration, risk metrics (Sharpe, Sortino, VaR, CVaR, Drawdown),
portfolio optimization (Mean-Variance, Min Vol, HRP), and guardrail validation.
Inspired by Finance-Guru and EigenLedger.
"""
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


def compute_concentration(holdings_df: pd.DataFrame, top_n: int = 5) -> dict:
    """Computes Top-N concentration and individual weights."""
    if holdings_df.empty or 'Current Value' not in holdings_df.columns:
        return {"top_n_pct": 0.0, "top_holdings": []}
    
    total = holdings_df['Current Value'].sum()
    top = holdings_df.nlargest(top_n, 'Current Value')
    return {
        "top_n_pct": float(top['Current Value'].sum() / total * 100) if total else 0.0,
        "top_holdings": top[['Stock Code', 'Current Value']].to_dict('records'),
    }


def compute_beta(stock_closes: pd.Series, index_closes: pd.Series, window: int = 252) -> float:
    """Computes beta of a stock or portfolio against a benchmark index."""
    stock_returns = stock_closes.pct_change().dropna().tail(window)
    index_returns = index_closes.pct_change().dropna().tail(window)
    n = min(len(stock_returns), len(index_returns))
    if n < 30:
        return 0.0
    stock_returns, index_returns = stock_returns.tail(n), index_returns.tail(n)
    covariance = stock_returns.cov(index_returns)
    variance = index_returns.var()
    return float(covariance / variance) if variance and not np.isnan(variance) else 0.0


def rollup_stage_health(stage_rows: list) -> Dict[int, int]:
    """Rollup counts of stocks in Weinstein Stages 1 through 4."""
    counts = {1: 0, 2: 0, 3: 0, 4: 0}
    for row in stage_rows:
        stage = row[0] if isinstance(row, (tuple, list)) else row
        if stage in counts:
            counts[stage] += 1
    return counts


# ==============================================================================
# 1. RISK & RETURN METRICS (Inspired by Finance-Guru & EigenLedger)
# ==============================================================================

def compute_risk_metrics(
    returns: pd.Series,
    benchmark_returns: Optional[pd.Series] = None,
    risk_free_rate: float = 0.065,
    periods_per_year: int = 252
) -> Dict[str, float]:
    """
    Computes institutional risk metrics:
    - Annualized Return, Annualized Volatility
    - Sharpe Ratio, Sortino Ratio
    - Value at Risk (VaR 95%), Conditional VaR (CVaR / Expected Shortfall 95%)
    - Max Drawdown (MDD)
    - Beta and Alpha (if benchmark provided)
    Default risk_free_rate is 6.5% (~91-day Indian T-bill rate).
    """
    clean_rets = returns.dropna()
    if len(clean_rets) < 10:
        return {
            "cagr": 0.0, "annualized_vol": 0.0, "sharpe_ratio": 0.0,
            "sortino_ratio": 0.0, "max_drawdown": 0.0, "var_95": 0.0,
            "cvar_95": 0.0, "beta": 0.0, "alpha": 0.0
        }

    # Annualized Return & Vol
    mean_daily = clean_rets.mean()
    cagr = float((1 + mean_daily) ** periods_per_year - 1)
    daily_vol = clean_rets.std()
    annualized_vol = float(daily_vol * np.sqrt(periods_per_year))

    # Daily risk-free rate
    rf_daily = (1 + risk_free_rate) ** (1 / periods_per_year) - 1
    excess_rets = clean_rets - rf_daily

    # Sharpe Ratio
    sharpe = float((excess_rets.mean() / daily_vol) * np.sqrt(periods_per_year)) if daily_vol > 0 else 0.0

    # Sortino Ratio (downside deviation only)
    downside_rets = clean_rets[clean_rets < rf_daily] - rf_daily
    downside_std = downside_rets.std()
    sortino = float((excess_rets.mean() / downside_std) * np.sqrt(periods_per_year)) if (downside_std and downside_std > 0) else 0.0

    # Max Drawdown
    cum_returns = (1 + clean_rets).cumprod()
    peak = cum_returns.cummax()
    drawdowns = (cum_returns - peak) / peak
    max_dd = float(drawdowns.min())  # negative float

    # Historical VaR and CVaR at 95% confidence (1-day)
    var_95 = float(np.percentile(clean_rets, 5))
    cvar_sub = clean_rets[clean_rets <= var_95]
    cvar_95 = float(cvar_sub.mean()) if len(cvar_sub) > 0 else var_95

    # Beta & Alpha relative to benchmark
    beta = 0.0
    alpha = 0.0
    if benchmark_returns is not None:
        bench_clean = benchmark_returns.dropna()
        common_idx = clean_rets.index.intersection(bench_clean.index)
        if len(common_idx) >= 20:
            s_aligned = clean_rets.loc[common_idx]
            b_aligned = bench_clean.loc[common_idx]
            cov = s_aligned.cov(b_aligned)
            bench_var = b_aligned.var()
            if bench_var > 0:
                beta = float(cov / bench_var)
                bench_cagr = float((1 + b_aligned.mean()) ** periods_per_year - 1)
                alpha = float(cagr - (risk_free_rate + beta * (bench_cagr - risk_free_rate)))

    return {
        "cagr": round(cagr * 100, 2),  # in %
        "annualized_vol": round(annualized_vol * 100, 2),  # in %
        "sharpe_ratio": round(sharpe, 2),
        "sortino_ratio": round(sortino, 2),
        "max_drawdown": round(max_dd * 100, 2),  # in %
        "var_95": round(abs(var_95) * 100, 2),  # in %
        "cvar_95": round(abs(cvar_95) * 100, 2),  # in %
        "beta": round(beta, 2),
        "alpha": round(alpha * 100, 2),  # in %
    }


def compute_drawdown_series(prices: pd.Series) -> pd.Series:
    """Computes daily percentage underwater drawdown series."""
    if prices.empty:
        return pd.Series(dtype=float)
    peak = prices.cummax()
    return ((prices - peak) / peak) * 100.0


# ==============================================================================
# 2. PORTFOLIO OPTIMIZATION (Inspired by EigenLedger / PyPortfolioOpt)
# ==============================================================================

def optimize_portfolio_weights(
    price_matrix: pd.DataFrame,
    objective: str = "max_sharpe",
    risk_free_rate: float = 0.065
) -> Dict[str, float]:
    """
    Computes analytical asset weights without heavy external dependencies:
    - 'max_sharpe': Mean-variance optimal portfolio targeting highest Sharpe.
    - 'min_volatility': Global minimum variance portfolio.
    - 'inverse_volatility': Weights inversely proportional to individual volatility.
    - 'equal_weight': 1/N baseline allocation.
    Returns a dict mapping stock_code -> optimal percentage weight (summing to 100%).
    """
    if price_matrix.empty or len(price_matrix.columns) == 0:
        return {}

    returns = price_matrix.pct_change().dropna()
    stocks = list(returns.columns)
    n = len(stocks)
    if n == 1:
        return {stocks[0]: 100.0}

    # Fallback to inverse volatility if insufficient time-series depth
    if len(returns) < 20:
        objective = "inverse_volatility"

    if objective == "equal_weight":
        eq = round(100.0 / n, 2)
        return {s: eq for s in stocks}

    cov = returns.cov().values
    vols = returns.std().values

    # Inverse volatility weighting
    if objective == "inverse_volatility" or np.linalg.cond(cov) > 1e12:
        safe_vols = np.where(vols <= 0, 1e-6, vols)
        inv_vols = 1.0 / safe_vols
        weights = inv_vols / inv_vols.sum()
        return {stocks[i]: round(float(weights[i] * 100.0), 2) for i in range(n)}

    # Min Volatility Portfolio (analytical solution: w = S^-1 * 1 / (1' * S^-1 * 1))
    try:
        inv_cov = np.linalg.pinv(cov)
        ones = np.ones(n)

        if objective == "min_volatility":
            w = inv_cov.dot(ones)
            w = w / ones.dot(w)
            w = np.clip(w, 0, 1)  # Long-only constraint
            w = w / w.sum()
            return {stocks[i]: round(float(w[i] * 100.0), 2) for i in range(n)}

        # Max Sharpe: w = S^-1 * (mu - rf) / (1' * S^-1 * (mu - rf))
        daily_rf = (1 + risk_free_rate) ** (1 / 252) - 1
        excess_means = returns.mean().values - daily_rf
        
        # If all excess returns are negative, fall back to minimum volatility
        if np.all(excess_means <= 0):
            return optimize_portfolio_weights(price_matrix, objective="min_volatility", risk_free_rate=risk_free_rate)

        w = inv_cov.dot(np.maximum(excess_means, 0))
        if w.sum() <= 0:
            w = np.ones(n)
        w = np.clip(w, 0, 1)
        w = w / w.sum()
        return {stocks[i]: round(float(w[i] * 100.0), 2) for i in range(n)}

    except Exception:
        # Graceful fallback to inverse volatility
        return optimize_portfolio_weights(price_matrix, objective="inverse_volatility")


# ==============================================================================
# 3. FAIL-CLOSED GUARDRAILS (Inspired by Finance-Guru)
# ==============================================================================

def check_guardrails(
    holdings_df: pd.DataFrame,
    max_position_cap_pct: float = 12.0,
    max_top5_cap_pct: float = 50.0,
    min_holdings_count: int = 5
) -> List[Dict[str, str]]:
    """
    Evaluates fail-closed risk guardrails on current holdings:
    - Position concentration breach (> max_position_cap_pct)
    - Top-5 concentration breach (> max_top5_cap_pct)
    - Under-diversification warning (< min_holdings_count)
    """
    alerts = []
    if holdings_df.empty or 'Current Value' not in holdings_df.columns:
        return [{"level": "ERROR", "rule": "NO_DATA", "message": "No holdings data available to evaluate guardrails."}]

    total_value = holdings_df['Current Value'].sum()
    if total_value <= 0:
        return alerts

    # 1. Total holdings count
    if len(holdings_df) < min_holdings_count:
        alerts.append({
            "level": "WARNING",
            "rule": "UNDER_DIVERSIFICATION",
            "message": f"Portfolio contains only {len(holdings_df)} holdings (recommended minimum is {min_holdings_count})."
        })

    # 2. Individual stock concentration cap
    holdings_df['Weight'] = (holdings_df['Current Value'] / total_value) * 100.0
    overweight = holdings_df[holdings_df['Weight'] > max_position_cap_pct]
    for _, row in overweight.iterrows():
        alerts.append({
            "level": "DANGER",
            "rule": "MAX_CONCENTRATION_BREACH",
            "stock_code": row['Stock Code'],
            "message": f"{row['Stock Code']} comprises {row['Weight']:.1f}% of portfolio (exceeds {max_position_cap_pct}% limit)."
        })

    # 3. Top 5 concentration cap
    top5_pct = holdings_df.nlargest(5, 'Current Value')['Current Value'].sum() / total_value * 100.0
    if top5_pct > max_top5_cap_pct:
        alerts.append({
            "level": "WARNING",
            "rule": "TOP5_CONCENTRATION_BREACH",
            "message": f"Top 5 holdings make up {top5_pct:.1f}% of total portfolio value (limit: {max_top5_cap_pct}%)."
        })

    return alerts
