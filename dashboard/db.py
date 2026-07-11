import os

import psycopg2
import psycopg2.pool
import streamlit as st

DATABASE_URL = os.getenv("DATABASE_URL")


@st.cache_resource
def get_connection_pool():
    return psycopg2.pool.ThreadedConnectionPool(1, 5, dsn=DATABASE_URL)


def query_db(query, args=(), one=False):
    pool = get_connection_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        return (rv[0] if rv else None) if one else rv
    except psycopg2.Error as e:
        conn.rollback()
        st.error(f"DB Error: {e}")
        return None
    finally:
        pool.putconn(conn)


def execute_db(query, args=()):
    pool = get_connection_pool()
    conn = pool.getconn()
    try:
        cur = conn.cursor()
        cur.execute(query, args)
        conn.commit()
    except psycopg2.Error as e:
        conn.rollback()
        st.error(f"DB Error: {e}")
    finally:
        pool.putconn(conn)
