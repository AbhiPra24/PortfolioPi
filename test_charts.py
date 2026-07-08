import pandas as pd
import sqlite3
import numpy as np

def calculate_sma(series, window):
    return series.rolling(window=window).mean()

def calculate_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    roll_up = up.ewm(span=period, adjust=False).mean()
    roll_down = down.ewm(span=period, adjust=False).mean()
    rs = roll_up / roll_down
    return 100.0 - (100.0 / (1.0 + rs))

def calculate_macd(series, slow=26, fast=12, signal_period=9):
    exp1 = series.ewm(span=fast, adjust=False).mean()
    exp2 = series.ewm(span=slow, adjust=False).mean()
    macd_line = exp1 - exp2
    signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
    return macd_line, signal_line

conn = sqlite3.connect("data/portfoliopi.db")
stocks = pd.read_sql("SELECT DISTINCT stock_code FROM ohlcv_cache", conn)
stock = stocks.iloc[0, 0]
print(f"Testing stock: {stock}")

data = pd.read_sql("SELECT date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date ASC", conn, params=(stock,))
df = pd.DataFrame(data, columns=['date', 'open', 'high', 'low', 'close', 'volume'])
df['date'] = pd.to_datetime(df['date'])

df['SMA50'] = calculate_sma(df['close'], 50)
df['SMA200'] = calculate_sma(df['close'], 200)
df['RSI'] = calculate_rsi(df['close'])
macd_line, signal_line = calculate_macd(df['close'])
df['MACD'] = macd_line
df['MACD_Signal'] = signal_line
df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

print(df.tail())

import plotly.graph_objects as go
from plotly.subplots import make_subplots

fig = make_subplots(rows=4, cols=1)
fig.add_trace(go.Bar(x=df['date'], y=df['MACD_Hist'], name='Histogram', marker_color=['green' if val >= 0 else 'red' for val in df['MACD_Hist']]), row=4, col=1)

print("Figure created successfully!")
