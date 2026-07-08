"""Weinstein Stage Analysis classification (Stage 1-4)."""

import pandas as pd

def classify_stage(df: pd.DataFrame) -> dict:
    if len(df) < 170:
        return {"stage": 0, "sma_150": 0.0, "slope": 0.0}
    
    df = df.sort_values('date').reset_index(drop=True)
    df['sma_150'] = df['close'].rolling(window=150).mean()
    
    current_close = df['close'].iloc[-1]
    current_sma_150 = df['sma_150'].iloc[-1]
    past_sma_150 = df['sma_150'].iloc[-21]  # 20 days ago
    
    slope = (current_sma_150 - past_sma_150) / past_sma_150
    
    stage = 0
    # Basic logic
    if slope > 0.02:
        # Rising SMA
        if current_close > current_sma_150:
            stage = 2
        else:
            stage = 2 # Pullback within Stage 2
    elif slope < -0.02:
        # Falling SMA
        if current_close < current_sma_150:
            stage = 4
        else:
            stage = 4 # Early warning / rally within Stage 4
    else: # slope between -0.02 and 0.02
        if current_close > current_sma_150:
            stage = 1
        else:
            stage = 3

    return {"stage": stage, "sma_150": current_sma_150, "slope": slope}
