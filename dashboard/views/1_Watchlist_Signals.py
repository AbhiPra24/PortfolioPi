import pandas as pd
import streamlit as st
from db import execute_db, query_db

st.header("Watchlist & Signals")

st.subheader("Current Watchlist")
w_data = query_db("SELECT stock_code FROM watchlist")
watchlist = [r[0] for r in w_data] if w_data else []

if watchlist:
    st.write(", ".join(watchlist))
else:
    st.write("Watchlist is empty.")

st.divider()

col1, col2 = st.columns(2)
with col1:
    st.subheader("Add to Watchlist")
    with st.form("add_watchlist_form"):
        new_stock = st.text_input("Stock Code (e.g. RELIANCE, TCS)")
        if st.form_submit_button("Add"):
            if new_stock:
                new_stock = new_stock.strip().upper()
                if new_stock not in watchlist:
                    execute_db("INSERT INTO watchlist (stock_code) VALUES (?)", (new_stock,))
                    execute_db("INSERT INTO backfill_requests (stock_code) VALUES (?)", (new_stock,))
                    st.success(f"Added {new_stock} to watchlist!")
                    st.rerun()
                else:
                    st.warning(f"{new_stock} is already in the watchlist.")

with col2:
    st.subheader("Remove from Watchlist")
    with st.form("remove_watchlist_form"):
        if watchlist:
            remove_stock = st.selectbox("Select Stock", watchlist)
            if st.form_submit_button("Remove"):
                if remove_stock:
                    execute_db("DELETE FROM watchlist WHERE stock_code = ?", (remove_stock,))
                    st.success(f"Removed {remove_stock} from watchlist!")
                    st.rerun()
        else:
            st.info("Watchlist is empty.")
            st.form_submit_button("Remove", disabled=True)

st.divider()

st.subheader("Latest Technical Signals")
signals = query_db("""
    SELECT s.stock_code, s.rsi14, s.macd_line, s.macd_signal, s.sma50, s.sma200,
           s.pct_from_52w_high, s.volume_ratio_20d, s.composite_score, s.timestamp,
           sa.action, sa.rationale
    FROM signals s
    LEFT JOIN stock_actions sa ON s.stock_code = sa.stock_code
    WHERE s.composite_score IS NOT NULL
    ORDER BY s.timestamp DESC, s.composite_score DESC LIMIT 50
""")

if signals:
    df = pd.DataFrame(signals, columns=[
        'Stock', 'RSI (14)', 'MACD Line', 'MACD Signal', 'SMA 50', 'SMA 200', 'Dist 52w High %', 'Vol Ratio', 'Score', 'Timestamp', 'Action', 'Rationale'
    ])
    for col in ['RSI (14)', 'MACD Line', 'MACD Signal', 'SMA 50', 'SMA 200', 'Dist 52w High %', 'Vol Ratio', 'Score']:
        df[col] = pd.to_numeric(df[col], errors='coerce').round(2)
    st.dataframe(df, use_container_width=True)
