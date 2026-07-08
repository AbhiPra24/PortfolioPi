import os

import streamlit as st

st.set_page_config(page_title="PortfolioPi", layout="wide")

password = os.getenv("DASHBOARD_PASSWORD")
if not password:
    raise ValueError("DASHBOARD_PASSWORD environment variable is not set")

def check_password():
    if "authenticated" not in st.session_state:
        st.session_state["authenticated"] = False

    if not st.session_state["authenticated"]:
        st.title("Login")
        pwd = st.text_input("Password", type="password")
        if st.button("Login"):
            if pwd == password:
                st.session_state["authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password")
        return False
    return True

if check_password():
    st.title("PortfolioPi")

    pages = {
        "Menu": [
            st.Page("views/0_Portfolio.py", title="Portfolio"),
            st.Page("views/1_Watchlist_Signals.py", title="Watchlist & Signals"),
            st.Page("views/2_Historical_Charts.py", title="Historical Charts"),
            st.Page("views/3_Session_Status.py", title="Session Status"),
        ]
    }
    pg = st.navigation(pages)
    pg.run()
