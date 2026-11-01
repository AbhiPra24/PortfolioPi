import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from db import query_db
from plotly.subplots import make_subplots

st.header("Historical Charts")

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

stocks = query_db("SELECT DISTINCT stock_code FROM ohlcv_cache ORDER BY stock_code")
if stocks:
    stock = st.selectbox("Select Stock", [s[0] for s in stocks])
    if stock:
        data = query_db("SELECT date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date ASC", (stock,))
        if data:
            df = pd.DataFrame(data, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['Date'] = pd.to_datetime(df['Date'])

            # Calculate Indicators
            df['SMA50'] = calculate_sma(df['Close'], 50)
            df['SMA200'] = calculate_sma(df['Close'], 200)
            df['RSI'] = calculate_rsi(df['Close'])
            macd_line, signal_line = calculate_macd(df['Close'])
            df['MACD'] = macd_line
            df['MACD_Signal'] = signal_line
            df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

            fig = make_subplots(rows=4, cols=1, shared_xaxes=True,
                                vertical_spacing=0.05,
                                row_heights=[0.5, 0.15, 0.15, 0.2],
                                subplot_titles=("Price", "Volume", "RSI", "MACD"))

            # Candlestick
            fig.add_trace(go.Candlestick(x=df['Date'], open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name='Price'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df['Date'], y=df['SMA50'], line=dict(color='blue', width=1), name='SMA50'), row=1, col=1)
            fig.add_trace(go.Scatter(x=df['Date'], y=df['SMA200'], line=dict(color='orange', width=1), name='SMA200'), row=1, col=1)

            # Volume
            fig.add_trace(go.Bar(x=df['Date'], y=df['Volume'], name='Volume', marker_color='grey'), row=2, col=1)

            # RSI
            fig.add_trace(go.Scatter(x=df['Date'], y=df['RSI'], line=dict(color='purple', width=1), name='RSI'), row=3, col=1)
            fig.add_hline(y=70, line_dash="dash", line_color="red", row=3, col=1)
            fig.add_hline(y=30, line_dash="dash", line_color="green", row=3, col=1)

            # MACD
            fig.add_trace(go.Scatter(x=df['Date'], y=df['MACD'], line=dict(color='blue', width=1), name='MACD'), row=4, col=1)
            fig.add_trace(go.Scatter(x=df['Date'], y=df['MACD_Signal'], line=dict(color='orange', width=1), name='Signal'), row=4, col=1)
            fig.add_trace(go.Bar(x=df['Date'], y=df['MACD_Hist'], name='Histogram', marker_color=['green' if val >= 0 else 'red' for val in df['MACD_Hist']]), row=4, col=1)

            fig.update_layout(height=800, xaxis_rangeslider_visible=False)

            st.plotly_chart(fig, use_container_width=True)

        else:
            st.warning("No data available for this stock.")
else:
    st.info("No historical data cached yet.")
