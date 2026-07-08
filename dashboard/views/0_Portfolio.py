import asyncio
import os
import sys

import pandas as pd
import plotly.express as px
import streamlit as st
from db import query_db

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

try:
    from core.data_refresh import run_refresh_pipeline
except ImportError:
    async def run_refresh_pipeline():
        await asyncio.sleep(2)

st.header("Portfolio Snapshot")

col1, col2 = st.columns([0.8, 0.2])
with col2:
    if st.button("🔄 Refresh Now"):
        with st.spinner("Refreshing data..."):
            asyncio.run(run_refresh_pipeline())
            st.success("Data Refreshed!")
            st.rerun()

data = query_db("SELECT stock_code, quantity, average_price, current_price, timestamp FROM holdings_snapshot ORDER BY timestamp DESC")
if data:
    df = pd.DataFrame(data, columns=['Stock Code', 'Quantity', 'Avg Price', 'Current Price', 'Timestamp'])
    df['Invested'] = df['Quantity'] * df['Avg Price']
    df['Current Value'] = df['Quantity'] * df['Current Price']
    df['P&L'] = df['Current Value'] - df['Invested']
    df['P&L %'] = (df['P&L'] / df['Invested']) * 100

    st.dataframe(df.style.format({
        'Avg Price': '₹{:.2f}',
        'Current Price': '₹{:.2f}',
        'Invested': '₹{:.2f}',
        'Current Value': '₹{:.2f}',
        'P&L': '₹{:.2f}',
        'P&L %': '{:.2f}%'
    }), use_container_width=True)

    m_col1, m_col2, m_col3 = st.columns(3)
    m_col1.metric("Total Invested", f"₹{df['Invested'].sum():,.2f}")
    m_col2.metric("Current Value", f"₹{df['Current Value'].sum():,.2f}")
    m_col3.metric("Unrealized P&L", f"₹{df['P&L'].sum():,.2f}", f"{(df['P&L'].sum() / df['Invested'].sum() * 100):.2f}%")

    st.subheader("Portfolio Allocation")
    fig = px.pie(df, values='Current Value', names='Stock Code', hole=0.4, title='Allocation by Current Value')
    st.plotly_chart(fig, use_container_width=True)

else:
    st.info("No portfolio data found. Await the first daily scheduler run.")
