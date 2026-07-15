import asyncio

from core.breeze_client import BreezeClient
from core.session_store import get_session


async def main():
    token = await get_session()
    b = BreezeClient(token)
    demat = b.get_demat_holdings()
    port = b.get_portfolio_holding(exchange_code="NSE", from_date="", to_date="")

    dq = [item for item in demat.get("Success", []) if item.get("stock_code") == "GOLDEX"]
    pq = [item for item in port.get("Success", []) if item.get("stock_code") == "GOLDEX"]
    print("Demat GOLDEX:", dq)
    print("Port GOLDEX:", pq)
asyncio.run(main())
