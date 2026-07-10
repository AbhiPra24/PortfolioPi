import asyncio
import os
import sys

import pandas as pd
import plotly.express as px
import streamlit as st
from db import query_db, execute_db

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

st.header("Portfolio Snapshot")

col1, col2 = st.columns([0.8, 0.2])
with col2:
    if st.button("🔄 Refresh Now"):
        execute_db("INSERT INTO refresh_requests (requested_at) VALUES (CURRENT_TIMESTAMP)")
        st.success("Refresh requested — will run within ~60s")

breeze_sync_ts = query_db("SELECT DATETIME(timestamp, '+5 hours', '+30 minutes') FROM job_heartbeats WHERE job_name = 'run_breeze_sync' ORDER BY timestamp DESC LIMIT 1")
market_data_ts = query_db("SELECT DATETIME(timestamp, '+5 hours', '+30 minutes') FROM job_heartbeats WHERE job_name = 'run_market_data_refresh' ORDER BY timestamp DESC LIMIT 1")

st.caption(f"**Holdings (Quantity/Avg Price) as of:** {breeze_sync_ts[0][0] + ' IST' if breeze_sync_ts and breeze_sync_ts[0][0] else 'N/A (Pending Breeze Sync)'}")
st.caption(f"**Prices & Signals as of:** {market_data_ts[0][0] + ' IST' if market_data_ts and market_data_ts[0][0] else 'N/A (Pending Market Data Refresh)'}")

refresh_interval = st.selectbox("Auto-refresh", ["Off", "15s", "30s", "60s"], index=3)
interval_map = {"Off": None, "15s": "15s", "30s": "30s", "60s": "60s"}

@st.fragment(run_every=interval_map[refresh_interval])
def render_portfolio():
    data = query_db("SELECT stock_code, quantity, average_price, current_price, timestamp FROM holdings_snapshot")
    if not data:
        st.info("No portfolio data found. Await the first daily scheduler run.")
        return
    df = pd.DataFrame(data, columns=['Stock Code', 'Quantity', 'Avg Price', 'Current Price', 'Timestamp'])
    df['Invested'] = df['Quantity'] * df['Avg Price']
    df['Current Value'] = df['Quantity'] * df['Current Price']
    df['P&L'] = df['Current Value'] - df['Invested']
    df['P&L %'] = (df['P&L'] / df['Invested']) * 100

    sort_choice = st.selectbox("Sort by", [
        "Invested (High → Low)", "Current Value (High → Low)", "P&L % (High → Low)", "Stock Code (A→Z)"
    ], index=0)
    sort_map = {
        "Invested (High → Low)": ("Invested", False),
        "Current Value (High → Low)": ("Current Value", False),
        "P&L % (High → Low)": ("P&L %", False),
        "Stock Code (A→Z)": ("Stock Code", True),
    }
    col, asc = sort_map[sort_choice]
    df = df.sort_values(col, ascending=asc)

    styled = df.style.format({
        'Avg Price': '₹{:.2f}', 'Current Price': '₹{:.2f}', 'Invested': '₹{:.2f}',
        'Current Value': '₹{:.2f}', 'P&L': '₹{:.2f}', 'P&L %': '{:.2f}%'
    }).map(lambda v: 'color: #16a34a' if isinstance(v, (int, float)) and v > 0 else ('color: #dc2626' if isinstance(v, (int, float)) and v < 0 else ''), subset=['P&L', 'P&L %'])
    st.dataframe(styled, use_container_width=True)

    m_col1, m_col2, m_col3 = st.columns(3)
    m_col1.metric("Total Invested", f"₹{df['Invested'].sum():,.2f}")
    m_col2.metric("Current Value", f"₹{df['Current Value'].sum():,.2f}")
    m_col3.metric("Unrealized P&L", f"₹{df['P&L'].sum():,.2f}", f"{(df['P&L'].sum() / df['Invested'].sum() * 100):.2f}%")

    st.subheader("Portfolio Allocation")
    total_val = df['Current Value'].sum()
    threshold = total_val * 0.015
    pie_df = df.copy()
    pie_df.loc[pie_df['Current Value'] < threshold, 'Stock Code'] = 'Others'
    pie_df = pie_df.groupby('Stock Code', as_index=False)['Current Value'].sum()

    tab1, tab2 = st.tabs(["Pie", "Treemap"])
    with tab1:
        st.plotly_chart(px.pie(pie_df, values='Current Value', names='Stock Code', hole=0.4), use_container_width=True)
    with tab2:
        st.plotly_chart(px.treemap(pie_df, path=['Stock Code'], values='Current Value'), use_container_width=True)

render_portfolio()
