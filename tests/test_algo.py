import numpy as np
import pandas as pd

from algo.indicators import calculate_macd, calculate_rsi, calculate_sma, proximity_to_52w_high, volume_spike
from algo.ranking import calculate_composite_score


def test_calculate_rsi():
    # Simple monotonic increase
    series = pd.Series(np.arange(100, 120, 1))
    rsi = calculate_rsi(series, 14)
    assert rsi > 50.0

    # Monotonic decrease
    series = pd.Series(np.arange(120, 100, -1))
    rsi = calculate_rsi(series, 14)
    assert rsi < 50.0

def test_calculate_macd():
    series = pd.Series(np.linspace(100, 200, 50))
    macd_line, macd_signal = calculate_macd(series)
    assert isinstance(macd_line, float)
    assert isinstance(macd_signal, float)

def test_calculate_sma():
    series = pd.Series([10, 20, 30, 40, 50])
    sma = calculate_sma(series, 3)
    assert sma == 40.0

def test_proximity_to_52w_high():
    assert proximity_to_52w_high(90.0, 100.0) == 10.0
    assert proximity_to_52w_high(100.0, 100.0) == 0.0
    assert proximity_to_52w_high(110.0, 100.0) == -10.0
    assert proximity_to_52w_high(100.0, None) is None
    assert proximity_to_52w_high(100.0, 0.0) is None

def test_volume_spike():
    vol_series = pd.Series([100]*19 + [200])
    ratio = volume_spike(200, vol_series, 20)
    assert ratio > 1.0

def test_calculate_composite_score():
    score = calculate_composite_score(
        rsi14=50,          # 10
        macd_line=1,       # macd_line > macd_signal (20)
        macd_signal=0,
        current_price=150, # > sma50 (15) > sma200 (10)
        sma50=120,         # > sma200 (20)
        sma200=100,
        pct_from_52w=2,    # 0 <= pct <= 5 (15)
        vol_ratio=2.0      # > 1.5 (10)
    )
    # Expected: 10 + 20 + 15 + 10 + 20 + 15 + 10 = 100
    assert score == 100

    score_low = calculate_composite_score(
        rsi14=30,          # 0
        macd_line=0,       # 0
        macd_signal=1,
        current_price=90,  # 0
        sma50=100,         # 0
        sma200=110,
        pct_from_52w=10,   # 0
        vol_ratio=1.0      # 0
    )
    assert score_low == 0
