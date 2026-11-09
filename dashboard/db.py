import os
import sqlite3

import streamlit as st

from core.database import configure_sqlite3_connection

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "portfoliopi.db"))


@st.cache_resource
def get_db_connection():
    # URI mode=ro requires uri=True
    uri = f"file:{DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    # Apply read-safe PRAGMAs (busy_timeout, cache_size, temp_store)
    cur = conn.cursor()
    cur.execute("PRAGMA busy_timeout=5000;")
    cur.execute("PRAGMA temp_store=MEMORY;")
    cur.execute("PRAGMA cache_size=-64000;")
    cur.close()
    return conn


def get_writable_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    return configure_sqlite3_connection(conn)


def query_db(query, args=(), one=False):
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, args)
        rv = cur.fetchall()
        return (rv[0] if rv else None) if one else rv
    except sqlite3.OperationalError as e:
        st.error(f"DB Error: {e}")
        return None


def execute_db(query, args=()):
    conn = get_writable_db_connection()
    cur = conn.cursor()
    try:
        cur.execute(query, args)
        conn.commit()
    except sqlite3.Error as e:
        st.error(f"DB Error: {e}")
    finally:
        conn.close()
