import streamlit as st
import pandas as pd
from db import query_db

st.header("Historical Charts")

stocks = query_db("SELECT DISTINCT stock_code FROM ohlcv_cache ORDER BY stock_code")
if stocks:
    stock = st.selectbox("Select Stock", [s[0] for s in stocks])
    if stock:
        data = query_db("SELECT date, open, high, low, close, volume FROM ohlcv_cache WHERE stock_code = ? ORDER BY date ASC", (stock,))
        if data:
            df = pd.DataFrame(data, columns=['Date', 'Open', 'High', 'Low', 'Close', 'Volume'])
            df['Date'] = pd.to_datetime(df['Date'])
            st.line_chart(df, x='Date', y='Close')
            st.bar_chart(df, x='Date', y='Volume')
        else:
            st.warning("No data available for this stock.")
else:
    st.info("No historical data cached yet.")
