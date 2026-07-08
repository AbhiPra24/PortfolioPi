import pandas as pd
import numpy as np

def calculate_chandelier_exit(df: pd.DataFrame, period: int = 22, atr_multiplier: float = 3.0) -> float:
    if len(df) < period:
        return 0.0
    
    df = df.sort_values('date').reset_index(drop=True)
    df['prev_close'] = df['close'].shift(1)
    
    df['tr1'] = df['high'] - df['low']
    df['tr2'] = (df['high'] - df['prev_close']).abs()
    df['tr3'] = (df['low'] - df['prev_close']).abs()
    
    df['tr'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)
    df['atr'] = df['tr'].rolling(window=period).mean()
    
    highest_high = df['high'].rolling(window=period).max()
    df['chandelier_exit'] = highest_high - (df['atr'] * atr_multiplier)
    
    return df['chandelier_exit'].iloc[-1]
