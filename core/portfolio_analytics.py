"""core/portfolio_analytics.py - Portfolio downside risk telemetry and risk metrics pipeline.

Computes institutional downside risk metrics:
- Sortino Ratio (using target return downside semivariance)
- Treynor Ratio ((portfolio_return - risk_free_rate) / beta)
- Jensen's Alpha (Rp - (Rf + beta * (Rm - Rf)))
- Rolling Maximum Drawdown (peak-to-trough drop)
- Downside Deviation (sqrt(1/N * sum(min(0, r_i - r_f)^2)))

Also integrates database persistence for `portfolio_risk_metrics` and provides
full backward compatibility with `algo.portfolio_analytics`.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Union

import aiosqlite
import numpy as np
import pandas as pd

from app_config import settings

logger = logging.getLogger(__name__)


def compute_downside_deviation(
    returns: Union[pd.Series, np.ndarray, List[float]],
    target_return: float = 0.06 / 252,
) -> float:
    """
    Computes downside deviation (downside semivariance) relative to a target return:
        Downside Deviation = sqrt( (1 / N) * sum( min(0, r_i - target_return)^2 ) )

    Args:
        returns: Array-like sequence of daily/periodic returns.
        target_return: Minimal acceptable return / hurdle rate per period (default 6% / 252).

    Returns:
        float: Downside deviation. Returns 0.0 if empty or zero downside.
    """
    if returns is None:
        return 0.0
    if not isinstance(returns, (pd.Series, np.ndarray)):
        returns = pd.Series(returns, dtype=float)
    clean_rets = pd.Series(returns).dropna().astype(float)
    n = len(clean_rets)
    if n == 0:
        return 0.0

    underperformance = np.minimum(0.0, clean_rets.to_numpy() - target_return)
    semivariance = np.sum(underperformance**2) / n
    return float(np.sqrt(semivariance))


def compute_sortino_ratio(
    returns: Union[pd.Series, np.ndarray, List[float]],
    target_return: float = 0.06 / 252,
    annualize: bool = False,
    periods_per_year: int = 252,
) -> float:
    """
    Measures return relative to downside semivariance:
        Sortino Ratio = (Mean(returns) - target_return) / Downside_Deviation
    When annualized, multiplies by sqrt(periods_per_year).

    Args:
        returns: Array-like sequence of periodic returns.
        target_return: Target / benchmark hurdle rate per period (default 6% / 252).
        annualize: If True, scales ratio by sqrt(periods_per_year).

    Returns:
        float: Sortino ratio. Returns 0.0 if downside deviation is 0.0 or insufficient data.
    """
    if returns is None:
        return 0.0
    if not isinstance(returns, (pd.Series, np.ndarray)):
        returns = pd.Series(returns, dtype=float)
    clean_rets = pd.Series(returns).dropna().astype(float)
    if len(clean_rets) == 0:
        return 0.0

    mean_ret = float(clean_rets.mean())
    downside_dev = compute_downside_deviation(clean_rets, target_return=target_return)
    if downside_dev <= 0.0 or np.isnan(downside_dev):
        return 0.0

    ratio = (mean_ret - target_return) / downside_dev
    if annualize:
        ratio *= np.sqrt(periods_per_year)
    return float(ratio)


def compute_treynor_ratio(
    portfolio_return: float,
    risk_free_rate: float,
    beta: float,
) -> float:
    """
    Computes Treynor Ratio:
        Treynor Ratio = (R_p - R_f) / beta

    Args:
        portfolio_return: Expected or annualized portfolio return (decimal or percentage, consistent with Rf).
        risk_free_rate: Risk-free rate (decimal or percentage, matching portfolio_return scale).
        beta: Systematic risk / market beta.

    Returns:
        float: Treynor Ratio. Returns 0.0 if beta is 0.0 or invalid (no systematic risk denominator).
    """
    if beta is None or np.isnan(beta) or beta == 0.0:
        return 0.0
    if portfolio_return is None or risk_free_rate is None:
        return 0.0
    return float((portfolio_return - risk_free_rate) / beta)


def compute_jensens_alpha(
    portfolio_return: float,
    market_return: float,
    risk_free_rate: float,
    beta: float,
) -> float:
    """
    Computes Jensen's Alpha:
        alpha = R_p - (R_f + beta * (R_m - R_f))

    Args:
        portfolio_return: Portfolio return (R_p).
        market_return: Market / benchmark return (R_m).
        risk_free_rate: Risk-free rate (R_f).
        beta: Portfolio beta relative to benchmark.

    Returns:
        float: Jensen's alpha (can be positive, zero, or negative).
    """
    if any(v is None or np.isnan(v) for v in (portfolio_return, market_return, risk_free_rate, beta)):
        return 0.0
    expected_return = risk_free_rate + beta * (market_return - risk_free_rate)
    return float(portfolio_return - expected_return)


def compute_rolling_max_drawdown(
    equity_curve: Union[pd.Series, np.ndarray, List[float]],
) -> float:
    """
    Computes the peak-to-trough maximum percentage drop of an equity curve / price series.

    Args:
        equity_curve: Sequential series of portfolio equity or price values.

    Returns:
        float: Maximum drawdown as a negative decimal (e.g. -0.20 for a 20% drawdown)
               or 0.0 if monotone increasing / non-negative.
    """
    if equity_curve is None:
        return 0.0
    if not isinstance(equity_curve, (pd.Series, np.ndarray)):
        equity_curve = pd.Series(equity_curve, dtype=float)
    clean_series = pd.Series(equity_curve).dropna().astype(float)
    if len(clean_series) < 2:
        return 0.0

    peak = clean_series.cummax()
    # Guard against division by zero if peak <= 0
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown = np.where(peak > 0, (clean_series - peak) / peak, 0.0)
    min_dd = float(np.nanmin(drawdown))
    return min_dd if min_dd <= 0.0 else 0.0


async def update_portfolio_risk_metrics(
    db: Optional[aiosqlite.Connection] = None,
    risk_free_rate: float = 0.065,
) -> Optional[Dict[str, float]]:
    """
    Pulls recent portfolio value history and benchmark history (_NIFTY50),
    computes institutional downside risk metrics, and writes an entry into `portfolio_risk_metrics`.
    """
    own_connection = False
    if db is None:
        db = await aiosqlite.connect(settings.db_path)
        own_connection = True

    try:
        # 1. Fetch portfolio value history
        async with db.execute(
            "SELECT timestamp, total_current_value FROM portfolio_value_history "
            "WHERE total_current_value IS NOT NULL AND timestamp IS NOT NULL "
            "ORDER BY timestamp ASC"
        ) as cur:
            hist_rows = await cur.fetchall()

        # 2. Fetch NIFTY50 benchmark history
        async with db.execute(
            "SELECT date, close FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date ASC"
        ) as cur:
            nifty_rows = await cur.fetchall()

        if not hist_rows or len(hist_rows) < 5:
            logger.info("Insufficient portfolio_value_history points for risk metrics calculation.")
            return None

        p_hist = pd.DataFrame(hist_rows, columns=["timestamp", "total_value"])
        p_hist["timestamp"] = pd.to_datetime(p_hist["timestamp"], format="mixed")
        p_hist = p_hist.drop_duplicates("timestamp").set_index("timestamp").sort_index()
        p_hist["total_value"] = pd.to_numeric(p_hist["total_value"], errors="coerce")
        port_returns = p_hist["total_value"].pct_change().dropna()

        bench_returns = None
        if nifty_rows and len(nifty_rows) > 10:
            n_df = pd.DataFrame(nifty_rows, columns=["date", "close"])
            n_df["date"] = pd.to_datetime(n_df["date"], format="mixed")
            n_df = n_df.drop_duplicates("date").set_index("date").sort_index()
            n_df["close"] = pd.to_numeric(n_df["close"], errors="coerce")
            bench_returns = n_df["close"].pct_change().dropna()

        # Compute metrics
        periods_per_year = 252
        clean_rets = port_returns.dropna()
        if len(clean_rets) < 5:
            return None

        mean_daily = float(clean_rets.mean())
        cagr = float((1.0 + mean_daily) ** periods_per_year - 1.0)
        daily_vol = float(clean_rets.std())
        float(daily_vol * np.sqrt(periods_per_year))

        rf_daily = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
        excess_rets = clean_rets - rf_daily
        sharpe = float((excess_rets.mean() / daily_vol) * np.sqrt(periods_per_year)) if daily_vol > 0 else 0.0

        downside_dev = compute_downside_deviation(clean_rets, target_return=rf_daily)
        sortino = compute_sortino_ratio(clean_rets, target_return=rf_daily, annualize=True)
        max_dd = compute_rolling_max_drawdown(p_hist["total_value"])

        # Beta & Alpha
        beta = 0.0
        bench_cagr = 0.0
        alpha = 0.0
        treynor = 0.0
        if bench_returns is not None:
            bench_clean = bench_returns.dropna()
            common_idx = clean_rets.index.intersection(bench_clean.index)
            if len(common_idx) >= 10:
                s_aligned = clean_rets.loc[common_idx]
                b_aligned = bench_clean.loc[common_idx]
                cov = float(s_aligned.cov(b_aligned))
                bench_var = float(b_aligned.var())
                if bench_var > 0:
                    beta = float(cov / bench_var)
                    bench_cagr = float((1.0 + b_aligned.mean()) ** periods_per_year - 1.0)
                    alpha = compute_jensens_alpha(cagr, bench_cagr, risk_free_rate, beta)
                    treynor = compute_treynor_ratio(cagr, risk_free_rate, beta)

        now_str = datetime.now(timezone.utc).isoformat()
        cagr_pct = round(cagr * 100, 2)
        bench_cagr_pct = round(bench_cagr * 100, 2)
        beta_val = round(beta, 2)
        jensens_alpha_val = round(alpha, 4)
        jensens_alpha_pct = round(alpha * 100, 2)
        sharpe_val = round(sharpe, 2)
        sortino_val = round(sortino, 2)
        treynor_val = round(treynor, 4)
        max_dd_val = round(max_dd, 4)
        max_dd_pct = round(max_dd * 100, 2)
        downside_dev_val = round(downside_dev, 6)

        await db.execute(
            """
            INSERT INTO portfolio_risk_metrics (
                calculated_at,
                cagr_pct,
                benchmark_cagr_pct,
                beta,
                jensens_alpha,
                jensens_alpha_pct,
                sharpe_ratio,
                sortino_ratio,
                treynor_ratio,
                max_drawdown,
                max_drawdown_pct,
                downside_deviation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                now_str,
                cagr_pct,
                bench_cagr_pct,
                beta_val,
                jensens_alpha_val,
                jensens_alpha_pct,
                sharpe_val,
                sortino_val,
                treynor_val,
                max_dd_val,
                max_dd_pct,
                downside_dev_val,
            ),
        )
        await db.commit()

        metrics_record = {
            "calculated_at": now_str,
            "cagr_pct": cagr_pct,
            "benchmark_cagr_pct": bench_cagr_pct,
            "beta": beta_val,
            "jensens_alpha": jensens_alpha_val,
            "jensens_alpha_pct": jensens_alpha_pct,
            "sharpe_ratio": sharpe_val,
            "sortino_ratio": sortino_val,
            "treynor_ratio": treynor_val,
            "max_drawdown": max_dd_val,
            "max_drawdown_pct": max_dd_pct,
            "downside_deviation": downside_dev_val,
        }
        logger.info(f"Updated portfolio risk metrics: {metrics_record}")
        return metrics_record

    finally:
        if own_connection:
            await db.close()
