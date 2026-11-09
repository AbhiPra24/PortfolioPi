#!/usr/bin/env python3
"""Comprehensive Breeze Read-Only Analysis Script.

Fetches and analyzes:
1. Customer details & allowed trading segments
2. Margin & Funds breakdown (equity, F&O, unallocated cash)
3. Recent trades (last 7 days) and total brokerage/taxes/cashflow
4. Recent orders (status breakdown: Executed, Ordered, Cancelled)
5. GTT Order Book status
6. Demat holdings vs portfolio positions check
"""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from tabulate import tabulate
    HAS_TABULATE = True
except ImportError:
    HAS_TABULATE = False

from core.breeze_client import BreezeClient, SessionExpiredError
from core.session_store import get_session


async def run_breeze_analysis():
    print("=" * 70)
    print(" 🔍 PORTFOLIOPI BREEZE ACCOUNT & TRANSACTION ANALYZER")
    print("=" * 70)

    token = await get_session()
    if not token:
        print("❌ No Breeze session token found in DB. Run /refresh_session first.")
        return

    try:
        breeze = BreezeClient(token)
    except SessionExpiredError as e:
        print(f"❌ Session expired or invalid: {e}")
        return
    except Exception as e:
        print(f"❌ Error initializing BreezeClient: {e}")
        return

    # 1. Customer & Account Profile
    cust = breeze.get_customer_details(api_session=token)
    if cust.get("Success"):
        c = cust["Success"]
        print(f"\n👤 Account: {c.get('idirect_user_name', 'N/A')} (User ID: {c.get('idirect_userid', 'N/A')})")
        print(f"   Last Login: {c.get('idirect_lastlogin_time', 'N/A')}")
        print(f"   Trade Date (NSE): {c.get('exg_trade_date', {}).get('NSE', 'N/A')}")
        segments = [k for k, v in c.get('segments_allowed', {}).items() if v == 'Y']
        print(f"   Active Segments: {', '.join(segments)}")
    else:
        print(f"⚠️ Could not fetch customer details: {cust.get('Error')}")

    # 2. Funds & Margin
    funds = breeze.get_funds()
    margin = breeze.get_margin(exchange_code="NSE")
    print("\n💰 FUNDS & MARGIN:")
    if funds.get("Success"):
        f = funds["Success"]
        print(f"   Bank Account: {f.get('bank_account', 'N/A')}")
        print(f"   Unallocated Cash: ₹{float(f.get('unallocated_balance', 0)):,.2f}")
        print(f"   Allocated Equity: ₹{float(f.get('allocated_equity', 0)):,.2f}")
        print(f"   Allocated F&O:    ₹{float(f.get('allocated_fno', 0)):,.2f}")
    if margin.get("Success"):
        m = margin["Success"]
        print(f"   Cash Limit:       ₹{float(m.get('cash_limit', 0)):,.2f}")
        print(f"   Block by Trade:   ₹{float(m.get('block_by_trade', 0)):,.2f}")

    # 3. Trades Analysis (Past 7 Days)
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=7)).strftime("%Y-%m-%dT06:00:00.000Z")
    to_date = now.strftime("%Y-%m-%dT06:00:00.000Z")
    trades_resp = breeze.get_trade_list(from_date=from_date, to_date=to_date, exchange_code="NSE")

    print(f"\n📊 RECENT TRADES (Last 7 Days):")
    if trades_resp.get("Success"):
        trades = trades_resp["Success"]
        table_rows = []
        total_brokerage = 0.0
        total_taxes = 0.0
        total_turnover = 0.0

        for t in trades:
            stock = t.get("stock_code")
            action = t.get("action")
            qty = int(t.get("quantity", 0))
            cost = float(t.get("average_cost", 0))
            ltp = float(t.get("ltp", 0))
            brok = float(t.get("brokerage_amount", 0))
            taxes = float(t.get("total_taxes", 0))
            trade_date = t.get("trade_date")

            total_brokerage += brok
            total_taxes += taxes
            total_turnover += cost

            table_rows.append([trade_date, stock, action, qty, f"₹{cost:,.2f}", f"₹{brok:.2f}", f"₹{taxes:.2f}"])

        if HAS_TABULATE:
            print(tabulate(table_rows, headers=["Date", "Stock", "Action", "Qty", "Cost/Turnover", "Brokerage", "Taxes"], tablefmt="fancy_grid"))
        else:
            for r in table_rows:
                print(f"   {r[0]} | {r[1]:<8} | {r[2]:<4} | Qty: {r[3]:<4} | {r[4]} | Brok: {r[5]} | Taxes: {r[6]}")

        print(f"   👉 Total Turnover: ₹{total_turnover:,.2f}")
        print(f"   👉 Total Brokerage: ₹{total_brokerage:,.2f} | Total Taxes: ₹{total_taxes:,.2f}")
    else:
        print(f"   No trades found or info: {trades_resp.get('Error', 'Empty')}")

    # 4. Orders Analysis (Past 5 Days due to market hour limit)
    order_from = (now - timedelta(days=5)).strftime("%Y-%m-%dT06:00:00.000Z")
    orders_resp = breeze.get_order_list(from_date=order_from, to_date=to_date, exchange_code="NSE")

    print(f"\n📋 RECENT ORDERS (Last 5 Days):")
    if orders_resp.get("Success"):
        orders = orders_resp["Success"]
        order_rows = []
        for o in orders:
            order_rows.append([
                o.get("order_datetime"),
                o.get("stock_code"),
                o.get("action"),
                o.get("order_type"),
                o.get("quantity"),
                o.get("price"),
                o.get("status")
            ])
        if HAS_TABULATE:
            print(tabulate(order_rows, headers=["Time", "Stock", "Action", "Type", "Qty", "Price", "Status"], tablefmt="fancy_grid"))
        else:
            for r in order_rows:
                print(f"   {r[0]} | {r[1]} | {r[2]} | {r[3]} | Qty: {r[4]} | Price: {r[5]} | Status: {r[6]}")
    else:
        print(f"   Orders status: {orders_resp.get('Error', 'No data')}")

    # 5. GTT Orders Book
    gtt_resp = breeze.gtt_order_book(exchange_code="NFO", from_date=order_from, to_date=to_date)
    print(f"\n🎯 GTT ORDER BOOK:")
    if gtt_resp.get("Success"):
        print(f"   Active GTTs: {gtt_resp['Success']}")
    else:
        print(f"   Status: {gtt_resp.get('Error', 'No GTT orders found')}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    asyncio.run(run_breeze_analysis())
