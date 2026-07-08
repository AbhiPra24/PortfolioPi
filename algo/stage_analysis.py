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
    if slope > 0.02 and current_close > current_sma_150:
        stage = 2
    elif slope < -0.02 and current_close < current_sma_150:
        stage = 4
    elif slope <= 0.02 and slope >= -0.02:
        if current_close > current_sma_150:
            stage = 1
        else:
            stage = 3
    else:
        stage = 0 # Undefined

    return {"stage": stage, "sma_150": current_sma_150, "slope": slope}
