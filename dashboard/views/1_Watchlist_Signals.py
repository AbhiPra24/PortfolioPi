import streamlit as st
import pandas as pd
from db import query_db

st.header("Watchlist & Signals")

st.subheader("Current Watchlist")
w_data = query_db("SELECT stock_code FROM watchlist")
if w_data:
    st.write(", ".join([r[0] for r in w_data]))
else:
    st.write("Watchlist is empty. Add via direct DB insert or bot commands (feature to be added).")

st.divider()

st.subheader("Latest Technical Signals")
signals = query_db("""
    SELECT stock_code, rsi14, macd_line, macd_signal, sma50, sma200, pct_from_52w_high, volume_ratio_20d, composite_score, timestamp 
    FROM signals 
    ORDER BY timestamp DESC, composite_score DESC LIMIT 50
""")

if signals:
    df = pd.DataFrame(signals, columns=[
        'Stock', 'RSI (14)', 'MACD Line', 'MACD Signal', 'SMA 50', 'SMA 200', 'Dist 52w High %', 'Vol Ratio', 'Score', 'Timestamp'
    ])
    st.dataframe(df.style.background_gradient(subset=['Score'], cmap='Greens'), use_container_width=True)
else:
    st.info("No signals generated yet.")
