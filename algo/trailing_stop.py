"""Chandelier Exit risk-management stop-loss calculation with dual-factor volatility scaling."""

from typing import Optional, Tuple, Union
import numpy as np
import pandas as pd


def calculate_chandelier_exit(
    df: pd.DataFrame,
    period: int = 22,
    atr_multiplier: Optional[float] = None,
    vix_series: Optional[pd.Series] = None,
    stage: int = 2,
    asset_class: str = "EQUITY",
    alpha: float = 0.35,
    gamma: float = 0.25,
    return_details: bool = False,
) -> Union[float, Tuple[float, float], dict]:
    """Calculate dynamic Chandelier Exit stop-loss and k_t volatility multiplier.

    Parameters
    ----------
    df : pd.DataFrame
        Historical price data containing 'date', 'high', 'low', 'close'.
    period : int, default 22
        Rolling window for ATR and highest high.
    atr_multiplier : Optional[float], default None
        If provided, overrides dynamic k_t multiplier (for backward compatibility).
    vix_series : Optional[pd.Series], default None
        Historical VIX index closing values.
    stage : int, default 2
        Weinstein stage (1, 2, 3, or 4).
    asset_class : str, default 'EQUITY'
        Asset class, e.g., 'EQUITY', 'COMMODITY', 'GOLD', 'ETF', 'REIT'.
    alpha : float, default 0.35
        VIX scaling exponent.
    gamma : float, default 0.25
        ATR term-structure scaling exponent.
    return_details : bool, default False
        If True, returns a dict with {"stop_loss": float, "k_t": float}.

    Returns
    -------
    Union[float, dict]
        Default returns stop_loss (float) for backward compatibility,
        or dict if return_details is True.
    """
    if len(df) < period:
        if return_details:
            return {"stop_loss": 0.0, "k_t": 3.0}
        return 0.0

    df = df.sort_values("date").reset_index(drop=True)
    df["prev_close"] = df["close"].shift(1)

    df["tr1"] = df["high"] - df["low"]
    df["tr2"] = (df["high"] - df["prev_close"]).abs()
    df["tr3"] = (df["low"] - df["prev_close"]).abs()
    df["tr"] = df[["tr1", "tr2", "tr3"]].max(axis=1)

    df["atr22"] = df["tr"].rolling(window=period).mean()

    # Base k_stage resolution
    asset_upper = str(asset_class).upper().strip()
    is_commodity_or_reit = any(
        term in asset_upper for term in ["COMMODITY", "GOLD", "REIT", "SILVER"]
    )

    if is_commodity_or_reit:
        k_stage = 3.5
    elif stage == 2:
        k_stage = 3.2
    elif stage == 3:
        k_stage = 2.2
    elif stage in (1, 4):
        k_stage = 2.8
    else:
        k_stage = 3.0

    if atr_multiplier is not None:
        k_t = float(atr_multiplier)
    else:
        # Determine ATR term structure: (ATR_22 / ATR_100)^(-gamma)
        if len(df) >= 100:
            df["atr100"] = df["tr"].rolling(window=100).mean()
            atr22_val = df["atr22"].iloc[-1]
            atr100_val = df["atr100"].iloc[-1]
            if pd.notna(atr22_val) and pd.notna(atr100_val) and atr100_val > 0 and atr22_val > 0:
                atr_ratio_term = (atr22_val / atr100_val) ** (-gamma)
            else:
                atr_ratio_term = 1.0
        else:
            atr_ratio_term = 1.0

        # Determine VIX term: (VIX_SMA50 / VIX_t)^alpha
        vix_term = 1.0
        if vix_series is not None:
            clean_vix = vix_series.dropna()
            if len(clean_vix) >= 50:
                vix_t = clean_vix.iloc[-1]
                vix_sma50 = clean_vix.rolling(window=50).mean().iloc[-1]
                if pd.notna(vix_t) and pd.notna(vix_sma50) and vix_t > 0 and vix_sma50 > 0:
                    vix_term = (vix_sma50 / vix_t) ** alpha

        # Dynamic formula: clip(k_stage * (VIX_SMA50 / VIX_t)^alpha * (ATR22 / ATR100)^(-gamma), 2.0, 4.2)
        raw_k_t = k_stage * vix_term * atr_ratio_term
        k_t = float(np.clip(raw_k_t, 2.0, 4.2))

    df["atr"] = df["atr22"]
    highest_high = df["high"].rolling(window=period).max()
    df["chandelier_exit"] = highest_high - (df["atr"] * k_t)

    stop_loss = float(df["chandelier_exit"].iloc[-1])

    if return_details:
        return {"stop_loss": stop_loss, "k_t": k_t}
    return stop_loss
