import pandas as pd


def calculate_rsi(series: pd.Series, period=14) -> float:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.iloc[-1] if not rsi.empty and not pd.isna(rsi.iloc[-1]) else 50.0

def calculate_macd(series: pd.Series, fast=12, slow=26, signal=9):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    if macd_line.empty or signal_line.empty:
        return 0.0, 0.0
    return macd_line.iloc[-1], signal_line.iloc[-1]

def calculate_sma(series: pd.Series, period: int) -> float:
    sma = series.rolling(window=period).mean()
    return sma.iloc[-1] if not sma.empty and not pd.isna(sma.iloc[-1]) else 0.0

def proximity_to_52w_high(current_price: float, high_52w: float) -> float:
    if high_52w is None or pd.isna(high_52w) or high_52w == 0:
        return None
    return ((high_52w - current_price) / high_52w) * 100.0

def volume_spike(current_volume: int, volume_series: pd.Series, period=20) -> float:
    avg_vol = volume_series.rolling(window=period).mean()
    avg_vol_val = avg_vol.iloc[-1] if not avg_vol.empty and not pd.isna(avg_vol.iloc[-1]) else 0.0
    if avg_vol_val == 0:
        return 1.0
    return current_volume / avg_vol_val
