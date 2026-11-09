import asyncio
import os
import sys

import pandas as pd
import plotly.express as px
import streamlit as st

# Setup sys.path for top-level imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from db import query_db

from core.breeze_client import BreezeClient, SessionExpiredError
from core.data_refresh import sync_breeze_trades_and_orders

st.header("Trades & Execution Ledger")
st.caption("Breeze API trades, recent order book, transaction friction analytics & execution audit trail.")

# Check for active session token
session_row = query_db("SELECT token, timestamp FROM session_tokens ORDER BY timestamp DESC LIMIT 1", one=True)
has_active_session = bool(session_row and session_row[0])
active_token = session_row[0].strip("'") if (session_row and session_row[0]) else None

col_hdr1, col_hdr2 = st.columns([0.7, 0.3])
with col_hdr2:
    if has_active_session:
        if st.button("🔄 Live Fetch from Breeze", type="primary"):
            with st.spinner("Fetching latest trades & orders from Breeze API..."):
                try:
                    breeze = BreezeClient(active_token)
                    stats = asyncio.run(sync_breeze_trades_and_orders(breeze))
                    st.success(f"Synced {stats['trades_synced']} trades & {stats['orders_synced']} orders!")
                    st.rerun()
                except SessionExpiredError:
                    st.error("Breeze session expired. Please update token in Telegram bot via /refresh_session.")
                except Exception as e:
                    st.error(f"Failed to fetch live from Breeze: {e}")
    else:
        st.info("No active Breeze token found. Live sync disabled.")


# Function to load trade data
def load_trades():
    rows = query_db("""
        SELECT
            trade_date, stock_code, action, quantity, average_cost, ltp,
            brokerage_amount, total_taxes, product_type, order_id, exchange_code
        FROM trades_history
        ORDER BY id DESC
    """)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=[
            "Date",
            "Stock Code",
            "Action",
            "Quantity",
            "Turnover",
            "LTP",
            "Brokerage",
            "Taxes",
            "Product",
            "Order ID",
            "Exchange",
        ],
    )
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype(int)
    df["Turnover"] = pd.to_numeric(df["Turnover"], errors="coerce").fillna(0.0)
    df["LTP"] = pd.to_numeric(df["LTP"], errors="coerce").fillna(0.0)
    df["Brokerage"] = pd.to_numeric(df["Brokerage"], errors="coerce").fillna(0.0)
    df["Taxes"] = pd.to_numeric(df["Taxes"], errors="coerce").fillna(0.0)
    return df


# Function to load orders data
def load_orders():
    rows = query_db("""
        SELECT
            order_datetime, stock_code, action, order_type, quantity, price,
            average_price, status, order_id, exchange_code
        FROM orders_history
        ORDER BY id DESC
    """)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=[
            "Date/Time",
            "Stock Code",
            "Action",
            "Type",
            "Quantity",
            "Order Price",
            "Avg Fill Price",
            "Status",
            "Order ID",
            "Exchange",
        ],
    )
    df["Quantity"] = pd.to_numeric(df["Quantity"], errors="coerce").fillna(0).astype(int)
    df["Order Price"] = pd.to_numeric(df["Order Price"], errors="coerce").fillna(0.0)
    df["Avg Fill Price"] = pd.to_numeric(df["Avg Fill Price"], errors="coerce").fillna(0.0)
    return df


df_trades = load_trades()
df_orders = load_orders()

# If local database is empty but active token is available, attempt an initial auto-sync
if (df_trades.empty and df_orders.empty) and has_active_session:
    with st.spinner("Local trade ledger empty. Fetching initial data from Breeze API..."):
        try:
            breeze = BreezeClient(active_token)
            asyncio.run(sync_breeze_trades_and_orders(breeze))
            df_trades = load_trades()
            df_orders = load_orders()
        except Exception as e:
            st.warning(f"Could not automatically pull from Breeze: {e}")

# Key Metrics Row
st.subheader("Execution & Friction Analytics")
if not df_trades.empty:
    total_turnover = df_trades["Turnover"].sum()
    total_brokerage = df_trades["Brokerage"].sum()
    total_taxes = df_trades["Taxes"].sum()
    total_friction = total_brokerage + total_taxes
    drag_pct = (total_friction / total_turnover * 100.0) if total_turnover > 0 else 0.0

    buy_df = df_trades[df_trades["Action"].str.lower() == "buy"]
    sell_df = df_trades[df_trades["Action"].str.lower() == "sell"]
    buy_count = len(buy_df)
    sell_count = len(sell_df)
    buy_vol = buy_df["Turnover"].sum()
    sell_vol = sell_df["Turnover"].sum()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Turnover", f"₹{total_turnover:,.2f}", f"{len(df_trades)} Trades")
    m2.metric("Brokerage Paid", f"₹{total_brokerage:,.2f}")
    m3.metric("Total Taxes & STT", f"₹{total_taxes:,.2f}", f"Drag: {drag_pct:.3f}%")
    m4.metric("Buys / Sells", f"{buy_count}B / {sell_count}S", f"Buy ₹{buy_vol:,.0f} | Sell ₹{sell_vol:,.0f}")
else:
    st.info("No executed trades data available yet.")

st.divider()

# Tabbed Display for Trades & Orders
tab_trades, tab_orders = st.tabs(["📊 Executed Trades", "📋 Order Book & Status"])

with tab_trades:
    st.subheader("Executed Trades Ledger")
    if df_trades.empty:
        st.info("No executed trades recorded in database.")
    else:
        # Search & Filter controls
        f_col1, f_col2, f_col3 = st.columns([0.4, 0.3, 0.3])
        with f_col1:
            search_stock = st.text_input("🔍 Search Stock Code", "").strip().upper()
        with f_col2:
            action_filter = st.selectbox("Action Filter", ["All", "Buy", "Sell"])
        with f_col3:
            exchange_filter = st.selectbox("Exchange", ["All"] + sorted(list(df_trades["Exchange"].dropna().unique())))

        filtered_trades = df_trades.copy()
        if search_stock:
            filtered_trades = filtered_trades[filtered_trades["Stock Code"].str.contains(search_stock, na=False)]
        if action_filter != "All":
            filtered_trades = filtered_trades[filtered_trades["Action"].str.lower() == action_filter.lower()]
        if exchange_filter != "All":
            filtered_trades = filtered_trades[filtered_trades["Exchange"] == exchange_filter]

        st.caption(f"Showing **{len(filtered_trades)}** of **{len(df_trades)}** executions")

        # Format dataframe
        styled_trades = filtered_trades.style.format(
            {"Turnover": "₹{:,.2f}", "LTP": "₹{:,.2f}", "Brokerage": "₹{:.2f}", "Taxes": "₹{:.2f}", "Quantity": "{:,}"}
        ).map(
            lambda v: (
                "color: #16a34a; font-weight: bold"
                if str(v).lower() == "buy"
                else ("color: #dc2626; font-weight: bold" if str(v).lower() == "sell" else "")
            ),
            subset=["Action"],
        )

        st.dataframe(styled_trades, use_container_width=True, hide_index=True)

        # Turnover breakdown chart
        if not filtered_trades.empty and filtered_trades["Turnover"].sum() > 0:
            st.subheader("Turnover Breakdown by Stock")
            c_df = filtered_trades.groupby(["Stock Code", "Action"], as_index=False)["Turnover"].sum()
            fig = px.bar(
                c_df,
                x="Stock Code",
                y="Turnover",
                color="Action",
                color_discrete_map={"Buy": "#16a34a", "Sell": "#dc2626"},
                barmode="group",
                title="Trade Turnover (₹) per Stock",
            )
            fig.update_layout(yaxis_title="Turnover (₹)", xaxis_title="Stock Code")
            st.plotly_chart(fig, use_container_width=True)

with tab_orders:
    st.subheader("Recent Orders & Status")
    if df_orders.empty:
        st.info("No recent orders recorded.")
    else:
        # Status counts breakdown
        status_counts = df_orders["Status"].value_counts().to_dict()
        badges = []
        for st_name, count in status_counts.items():
            if str(st_name).lower() == "executed":
                badges.append(f"🟢 **Executed**: {count}")
            elif str(st_name).lower() in ["cancelled", "rejected"]:
                badges.append(f"🔴 **{st_name}**: {count}")
            else:
                badges.append(f"🟡 **{st_name}**: {count}")
        st.markdown(" · ".join(badges))

        # Filter controls for orders
        o_col1, o_col2 = st.columns([0.6, 0.4])
        with o_col1:
            search_order_stock = st.text_input("🔍 Search Order Stock", "", key="order_search").strip().upper()
        with o_col2:
            status_filter = st.selectbox("Status Filter", ["All"] + sorted(list(df_orders["Status"].dropna().unique())))

        filtered_orders = df_orders.copy()
        if search_order_stock:
            filtered_orders = filtered_orders[filtered_orders["Stock Code"].str.contains(search_order_stock, na=False)]
        if status_filter != "All":
            filtered_orders = filtered_orders[filtered_orders["Status"] == status_filter]

        # Apply visual badge formatting to status column
        def style_status(val):
            v_low = str(val).lower()
            if v_low == "executed":
                return "background-color: #dcfce7; color: #166534; font-weight: bold; border-radius: 4px; padding: 2px 6px;"
            elif "cancel" in v_low or "reject" in v_low:
                return "background-color: #fee2e2; color: #991b1b; font-weight: bold; border-radius: 4px; padding: 2px 6px;"
            else:
                return "background-color: #fef9c3; color: #854d0e; font-weight: bold; border-radius: 4px; padding: 2px 6px;"

        styled_orders = (
            filtered_orders.style.format({"Order Price": "₹{:,.2f}", "Avg Fill Price": "₹{:,.2f}", "Quantity": "{:,}"})
            .map(
                lambda v: (
                    "color: #16a34a; font-weight: bold"
                    if str(v).lower() == "buy"
                    else ("color: #dc2626; font-weight: bold" if str(v).lower() == "sell" else "")
                ),
                subset=["Action"],
            )
            .map(style_status, subset=["Status"])
        )

        st.dataframe(styled_orders, use_container_width=True, hide_index=True)
