import streamlit as st
import pandas as pd
from db import query_db

st.header("Portfolio Snapshot")

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
    
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Invested", f"₹{df['Invested'].sum():,.2f}")
    col2.metric("Current Value", f"₹{df['Current Value'].sum():,.2f}")
    col3.metric("Unrealized P&L", f"₹{df['P&L'].sum():,.2f}", f"{(df['P&L'].sum() / df['Invested'].sum() * 100):.2f}%")
else:
    st.info("No portfolio data found. Await the first daily scheduler run.")
