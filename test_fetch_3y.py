import asyncio
from datetime import datetime, timedelta

from core.breeze_client import BreezeClient
from core.session_store import get_session


async def run():
    token = await get_session()
    breeze = BreezeClient(token)
    to_date_str = datetime.now().strftime("%Y-%m-%dT00:00:00.000Z")
    from_date_str = (datetime.now() - timedelta(days=1095)).strftime("%Y-%m-%dT00:00:00.000Z")
    res = breeze.get_historical_data_v2(interval="1day", from_date=from_date_str, to_date=to_date_str, stock_code="NIFTY", exchange_code="NSE", product_type="cash")
    print(len(res.get("Success", [])))

asyncio.run(run())
