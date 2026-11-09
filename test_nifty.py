import asyncio
from datetime import datetime, timedelta

import aiosqlite

from core.breeze_client import BreezeClient
from core.session_store import get_session


async def run():
    token = await get_session()
    breeze = BreezeClient(token)
    to_date_str = datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")
    from_date_str = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00.000Z")
    print("Fetching NIFTY...")
    res = breeze.get_historical_data_v2(interval="1day", from_date=from_date_str, to_date=to_date_str, stock_code="NIFTY", exchange_code="NSE", product_type="cash")
    print(len(res.get("Success", [])))
    records = []
    for row in res.get("Success", []):
        records.append((
            "_NIFTY50",
            row.get("datetime"),
            float(row.get("open", 0)),
            float(row.get("high", 0)),
            float(row.get("low", 0)),
            float(row.get("close", 0)),
            int(row.get("volume", 0))
        ))
    async with aiosqlite.connect("data/portfoliopi.db") as db:
        await db.executemany("INSERT OR IGNORE INTO ohlcv_cache (stock_code, date, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?, ?)", records)
        await db.commit()
    print("Done")

asyncio.run(run())
