import pandas as pd
import streamlit as st
from db import query_db

st.header("System & Session Status")

st.subheader("Breeze API Session")
session = query_db("SELECT token, timestamp FROM session_tokens ORDER BY timestamp DESC LIMIT 1", one=True)
if session:
    st.success(f"Session token active. Last updated: {session[1]}")
    # Show token masked
    token = session[0]
    masked = token[:4] + "*"*10 + token[-4:] if len(token) > 8 else "****"
    st.code(masked)
else:
    st.error("No active session token found. Please use the Telegram bot /refresh_session command.")

breeze_sync = query_db("SELECT timestamp FROM job_heartbeats WHERE job_name = 'run_breeze_sync' ORDER BY timestamp DESC LIMIT 1", one=True)
if breeze_sync:
    from datetime import datetime, timezone
    sync_dt = breeze_sync[0]
    days_ago = (datetime.now(timezone.utc) - sync_dt).days
    st.info(f"**Last Breeze Sync (Holdings & Quantities):** {breeze_sync[0]} ({days_ago} days ago)")
else:
    st.warning("**Last Breeze Sync:** Never")

st.subheader("Job Heartbeats")
heartbeats = query_db("SELECT job_name, timestamp FROM job_heartbeats ORDER BY timestamp DESC LIMIT 10")
if heartbeats:
    df = pd.DataFrame(heartbeats, columns=['Job Name', 'Last Execution Time'])
    st.table(df)
else:
    st.info("No jobs have run yet.")

st.subheader("Data Health (recent failures)")
health = query_db("SELECT stock_code, source, status, message, timestamp FROM data_health ORDER BY timestamp DESC LIMIT 20")
if health:
    st.dataframe(pd.DataFrame(health, columns=['Stock', 'Source', 'Status', 'Message', 'Timestamp']), use_container_width=True)
else:
    st.success("No recent data-fetch failures.")
