import os
import sys

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from db import query_db

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
from algo.portfolio_analytics import (
    check_guardrails,
    compute_beta,
    compute_concentration,
    compute_drawdown_series,
    compute_risk_metrics,
    optimize_portfolio_weights,
    rollup_stage_health,
)

st.header("📊 Portfolio Analyser & Quantitative Risk Engine")
st.caption("Institutional-grade portfolio analytics, fail-closed guardrails, and optimization models (inspired by Finance-Guru & EigenLedger).")

data = query_db("SELECT stock_code, quantity, average_price, current_price FROM holdings_snapshot")
if not data:
    st.info("No holdings data available yet.")
else:
    df = pd.DataFrame(data, columns=['Stock Code', 'Quantity', 'Avg Price', 'Current Price'])
    df['Current Value'] = df['Quantity'] * df['Current Price']
    total_val = df['Current Value'].sum()
    df['Weight (%)'] = (df['Current Value'] / total_val * 100.0) if total_val else 0.0

    # ==============================================================================
    # 1. FAIL-CLOSED RISK GUARDRAILS
    # ==============================================================================
    st.subheader("🛡️ Risk Guardrails (Fail-Closed)")
    alerts = check_guardrails(df, max_position_cap_pct=12.0, max_top5_cap_pct=50.0, min_holdings_count=5)
    
    if not alerts:
        st.success("✅ All risk guardrails satisfied: No concentration or diversification breaches.")
    else:
        for alert in alerts:
            lvl = alert.get("level", "WARNING")
            msg = alert.get("message", "")
            if lvl == "DANGER":
                st.error(f"🚨 **Concentration Alert**: {msg}")
            elif lvl == "WARNING":
                st.warning(f"⚠️ **Risk Advisory**: {msg}")
            else:
                st.info(msg)

    # ==============================================================================
    # 2. CONCENTRATION & ALLOCATION
    # ==============================================================================
    st.divider()
    col_c1, col_c2 = st.columns([1, 2])
    with col_c1:
        st.subheader("Concentration Summary")
        conc = compute_concentration(df, top_n=5)
        st.metric("Top 5 Holdings %", f"{conc['top_n_pct']:.1f}%")
        st.metric("Total Holdings", f"{len(df)} assets")
        st.dataframe(pd.DataFrame(conc['top_holdings']), use_container_width=True)

    with col_c2:
        st.subheader("Holdings Distribution")
        fig_pie = px.pie(
            df.nlargest(12, 'Current Value'),
            values='Current Value',
            names='Stock Code',
            hole=0.45,
            title="Top 12 Holdings by Market Value"
        )
        fig_pie.update_traces(textposition='inside', textinfo='percent+label')
        st.plotly_chart(fig_pie, use_container_width=True)

    # ==============================================================================
    # 3. INSTITUTIONAL RISK & RETURN METRICS
    # ==============================================================================
    st.divider()
    st.subheader("📈 Institutional Risk & Performance Profile")
    
    # Query portfolio value history
    hist_rows = query_db("SELECT timestamp, total_current_value FROM portfolio_value_history ORDER BY timestamp ASC")
    nifty_rows = query_db("SELECT date, close FROM ohlcv_cache WHERE stock_code = '_NIFTY50' ORDER BY date ASC")
    
    bench_returns = None
    if nifty_rows and len(nifty_rows) > 10:
        nifty_df = pd.DataFrame(nifty_rows, columns=['date', 'close'])
        nifty_df['date'] = pd.to_datetime(nifty_df['date'], format='mixed')
        nifty_df = nifty_df.drop_duplicates('date').set_index('date').sort_index()
        bench_returns = nifty_df['close'].pct_change().dropna()

    if hist_rows and len(hist_rows) > 5:
        p_hist = pd.DataFrame(hist_rows, columns=['timestamp', 'total_value'])
        p_hist['timestamp'] = pd.to_datetime(p_hist['timestamp'], format='mixed')
        p_hist = p_hist.drop_duplicates('timestamp').set_index('timestamp').sort_index()
        port_returns = p_hist['total_value'].pct_change().dropna()

        metrics = compute_risk_metrics(port_returns, bench_returns, risk_free_rate=0.065)

        m_col1, m_col2, m_col3, m_col4 = st.columns(4)
        m_col1.metric("Sharpe Ratio", f"{metrics['sharpe_ratio']:.2f}")
        m_col2.metric("Sortino Ratio", f"{metrics['sortino_ratio']:.2f}")
        m_col3.metric("Max Drawdown", f"{metrics['max_drawdown']:.2f}%")
        m_col4.metric("Annualized Volatility", f"{metrics['annualized_vol']:.2f}%")

        m_col5, m_col6, m_col7, m_col8 = st.columns(4)
        m_col5.metric("VaR (95%, 1-day)", f"{metrics['var_95']:.2f}%")
        m_col6.metric("CVaR / Expected Shortfall", f"{metrics['cvar_95']:.2f}%")
        m_col7.metric("Portfolio Beta vs Nifty", f"{metrics['beta']:.2f}")
        m_col8.metric("Alpha (Annualized)", f"{metrics['alpha']:.2f}%")

        # Drawdown Chart
        st.write("##### Underwater Drawdown Curve")
        dd_series = compute_drawdown_series(p_hist['total_value'])
        fig_dd = px.area(
            x=dd_series.index,
            y=dd_series.values,
            labels={'x': 'Date', 'y': 'Drawdown (%)'},
            title="Portfolio Drawdown (%)"
        )
        fig_dd.update_traces(line_color="#e74c3c")
        st.plotly_chart(fig_dd, use_container_width=True)
    else:
        st.info("Portfolio value history is accumulating. Risk ratios will activate with additional daily snapshots.")

    # ==============================================================================
    # 4. WEINSTEIN STAGE HEALTH & ASSET BETAS
    # ==============================================================================
    st.divider()
    col_s1, col_s2 = st.columns([1, 1])
    with col_s1:
        st.subheader("Stage Health Rollup (Weinstein)")
        stage_rows = query_db("SELECT stage FROM stage_history WHERE date = (SELECT MAX(date) FROM stage_history)")
        if stage_rows:
            counts = rollup_stage_health(stage_rows)
            fig_bar = px.bar(
                x=[f"Stage {k}" for k in sorted(counts.keys())],
                y=[counts[k] for k in sorted(counts.keys())],
                color=[f"Stage {k}" for k in sorted(counts.keys())],
                labels={'x': 'Weinstein Stage', 'y': 'Number of Holdings'},
                title="Holdings by Stage Classification"
            )
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("No stage classification records found.")

    with col_s2:
        st.subheader("Stock Beta vs Nifty 50")
        if bench_returns is not None and not bench_returns.empty:
            betas = []
            for stock in df['Stock Code'].head(15):
                rows = query_db("SELECT date, close FROM ohlcv_cache WHERE stock_code = %s ORDER BY date ASC", (stock,))
                if rows and len(rows) > 30:
                    s_df = pd.DataFrame(rows, columns=['date', 'close'])
                    s_df['date'] = pd.to_datetime(s_df['date'], format='mixed')
                    s_df = s_df.drop_duplicates('date').set_index('date').sort_index()
                    aligned = pd.concat([s_df['close'], nifty_df['close']], axis=1, join='inner').dropna()
                    if len(aligned) > 30:
                        b = compute_beta(aligned.iloc[:, 0], aligned.iloc[:, 1])
                        betas.append({"Stock Code": stock, "Beta": round(b, 2)})
            if betas:
                st.dataframe(pd.DataFrame(betas), use_container_width=True)
            else:
                st.info("Collecting price cache for top stocks.")
        else:
            st.info("Benchmark OHLCV (_NIFTY50) not available for individual beta calculations.")

    # ==============================================================================
    # 5. PORTFOLIO OPTIMIZATION & REBALANCING (EigenLedger Style)
    # ==============================================================================
    st.divider()
    st.subheader("🎯 Quantitative Portfolio Optimization")
    st.caption("Computes mathematically optimal asset weights across your active holdings based on historical volatility and returns.")

    # Collect OHLCV for top holdings
    top_stocks = df.nlargest(10, 'Current Value')['Stock Code'].tolist()
    price_dict = {}
    for s in top_stocks:
        s_rows = query_db("SELECT date, close FROM ohlcv_cache WHERE stock_code = %s ORDER BY date ASC", (s,))
        if s_rows and len(s_rows) >= 30:
            s_s = pd.DataFrame(s_rows, columns=['date', 'close']).drop_duplicates('date').set_index('date')['close']
            price_dict[s] = s_s

    if len(price_dict) >= 3:
        price_matrix = pd.DataFrame(price_dict).dropna()
        if len(price_matrix) >= 20:
            opt_mode = st.radio(
                "Optimization Model",
                options=["max_sharpe", "min_volatility", "inverse_volatility", "equal_weight"],
                format_func=lambda x: {
                    "max_sharpe": "Maximum Sharpe Ratio (Mean-Variance Optimal)",
                    "min_volatility": "Minimum Volatility (Capital Preservation)",
                    "inverse_volatility": "Risk Parity (Inverse Volatility)",
                    "equal_weight": "Equal Weighting (1/N Baseline)"
                }[x],
                horizontal=True
            )

            opt_weights = optimize_portfolio_weights(price_matrix, objective=opt_mode)

            # Build comparison table
            comp_rows = []
            for s in price_matrix.columns:
                curr_w = float(df[df['Stock Code'] == s]['Weight (%)'].values[0]) if len(df[df['Stock Code'] == s]) else 0.0
                target_w = opt_weights.get(s, 0.0)
                diff = target_w - curr_w
                comp_rows.append({
                    "Stock Code": s,
                    "Current Weight (%)": round(curr_w, 2),
                    "Target Weight (%)": round(target_w, 2),
                    "Rebalance Delta (%)": round(diff, 2),
                    "Action": "BUY MORE" if diff > 1.0 else ("TRIM" if diff < -1.0 else "HOLD / IN-LINE")
                })
            comp_df = pd.DataFrame(comp_rows)
            st.dataframe(comp_df, use_container_width=True)

            # Target vs Current Bar Chart
            fig_comp = go.Figure(data=[
                go.Bar(name='Current Weight (%)', x=comp_df['Stock Code'], y=comp_df['Current Weight (%)'], marker_color='#3498db'),
                go.Bar(name='Target Weight (%)', x=comp_df['Stock Code'], y=comp_df['Target Weight (%)'], marker_color='#2ecc71')
            ])
            fig_comp.update_layout(barmode='group', title="Current vs. Optimized Target Weights")
            st.plotly_chart(fig_comp, use_container_width=True)
        else:
            st.info("Insufficient overlapping price history to run optimization matrix.")
    else:
        st.info("Price data for top holdings is currently building up in the database cache.")
