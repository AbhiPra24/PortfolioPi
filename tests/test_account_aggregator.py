"""Unit tests for Account Aggregator Expansion Gateway (`core/account_aggregator.py`)."""

import os
import tempfile
import pytest
import aiosqlite

from core.account_aggregator import (
    AccountAggregatorGateway,
    AccountAggregatorNormalizer,
    AssetClass,
    ConsentHandle,
    ConsentStatus,
    ExternalHolding,
    FIType,
    MockAAClient,
)


@pytest.fixture
def temp_db_path():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.mark.anyio
async def test_consent_request_creation(temp_db_path):
    client = MockAAClient()
    gateway = AccountAggregatorGateway(client=client, db_path=temp_db_path)

    handle = await gateway.create_consent_request(
        customer_id="cust_9876543210@onemoney",
        fi_types=[FIType.EQUITIES.value, FIType.MUTUAL_FUNDS.value, FIType.NPS.value],
    )

    assert handle.consent_id.startswith("consent-")
    assert handle.customer_id == "cust_9876543210@onemoney"
    assert handle.status == ConsentStatus.PENDING
    assert len(handle.fi_types) == 3

    # Check that status polling works and returns PENDING initially
    status = await gateway.check_consent_status(handle.consent_handle)
    assert status == ConsentStatus.PENDING


@pytest.mark.anyio
async def test_consent_status_update_and_webhook(temp_db_path):
    client = MockAAClient()
    gateway = AccountAggregatorGateway(client=client, db_path=temp_db_path)

    handle = await gateway.create_consent_request(
        customer_id="user@finvu",
        fi_types=[FIType.MUTUAL_FUNDS.value],
    )

    # Simulate webhook callback from Sahamati / Setu
    webhook_payload = {
        "ConsentStatusNotification": {
            "consentId": handle.consent_id,
            "consentStatus": "ACTIVE",
        }
    }
    updated_status = await gateway.handle_consent_webhook(webhook_payload)
    assert updated_status == ConsentStatus.ACTIVE

    # Check status via gateway
    status = await gateway.check_consent_status(handle.consent_id)
    assert status == ConsentStatus.ACTIVE


@pytest.mark.anyio
async def test_fetch_fi_data_and_persistence(temp_db_path):
    client = MockAAClient()
    gateway = AccountAggregatorGateway(client=client, db_path=temp_db_path)

    handle = await gateway.create_consent_request(
        customer_id="test_investor@camsonline",
        fi_types=[FIType.EQUITIES.value, FIType.MUTUAL_FUNDS.value, FIType.NPS.value],
    )

    # Approve consent
    await client.approve_consent(handle.consent_handle)

    # Fetch and persist FI data
    profile = await gateway.fetch_and_persist_fi_data(handle.consent_id)

    assert profile.customer_id == "test_investor@camsonline"
    assert len(profile.holdings) >= 4  # 2 equities, 1 MF, 2 NPS schemes in default mock
    assert profile.total_value > 0

    # Verify database persistence
    equities = await gateway.get_external_holdings(fi_type=FIType.EQUITIES.value)
    assert len(equities) == 2
    eq_symbols = {e.symbol for e in equities}
    assert "RELIANCE" in eq_symbols
    assert "INFY" in eq_symbols

    mfs = await gateway.get_external_holdings(fi_type=FIType.MUTUAL_FUNDS.value)
    assert len(mfs) == 1
    assert mfs[0].asset_class == AssetClass.MUTUAL_FUND.value
    assert mfs[0].folio_number == "FOLIO-102938475"
    assert mfs[0].quantity == 1500.45

    nps = await gateway.get_external_holdings(fi_type=FIType.NPS.value)
    assert len(nps) == 2
    nps_schemes = {n.symbol for n in nps}
    assert "SM001001" in nps_schemes
    assert nps[0].pran == "PRAN-110099887766"


@pytest.mark.anyio
async def test_normalizer_xml_and_json_payloads():
    # Test JSON structure
    json_data = {
        "Account": {
            "fiType": "MUTUAL_FUNDS",
            "folioNo": "1234567/89",
            "Holdings": {
                "Holding": [
                    {
                        "amfiCode": "119551",
                        "isin": "INF209K01165",
                        "schemeName": "Mirae Asset Large Cap Fund",
                        "units": "250.50",
                        "nav": "105.20",
                        "totalCost": "20000.00",
                        "navDate": "2026-11-05",
                    }
                ]
            },
        }
    }

    holdings = AccountAggregatorNormalizer.normalize_profile(json_data, fiu_provider="SETU")
    assert len(holdings) == 1
    h = holdings[0]
    assert h.symbol == "119551"
    assert h.isin == "INF209K01165"
    assert h.quantity == 250.50
    assert h.current_price == 105.20
    assert h.current_value == pytest.approx(250.50 * 105.20, 0.01)
    assert h.folio_number == "1234567/89"

    # Test XML structure (Standard Sahamati Equity schema)
    xml_data = """<?xml version="1.0" encoding="UTF-8"?>
    <Account fiType="EQUITIES" dematId="1208160099999999" dpId="IN302927">
        <Holdings>
            <Holding isin="INE009A01021" symbol="INFY" companyName="Infosys Ltd" units="100" averagePrice="1350.00" currentPrice="1650.00" asOn="2026-11-07"/>
            <Holding isin="INE040A01034" symbol="HDFCBANK" companyName="HDFC Bank Ltd" units="60" averagePrice="1500.00" currentPrice="1750.00" asOn="2026-11-07"/>
        </Holdings>
    </Account>
    """

    xml_holdings = AccountAggregatorNormalizer.normalize_profile(xml_data, fiu_provider="FINVU")
    assert len(xml_holdings) == 2
    infy = next(item for item in xml_holdings if item.symbol == "INFY")
    assert infy.quantity == 100.0
    assert infy.current_price == 1650.0
    assert infy.current_value == 165000.0
    assert infy.account_id == "1208160099999999"
    assert infy.dp_id == "IN302927"


@pytest.mark.anyio
async def test_unified_portfolio_summary_merge(temp_db_path):
    client = MockAAClient()
    gateway = AccountAggregatorGateway(client=client, db_path=temp_db_path)

    # 1. Seed holdings_snapshot table (primary broker holdings)
    async with aiosqlite.connect(temp_db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS holdings_snapshot (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stock_code TEXT UNIQUE,
                quantity INTEGER,
                average_price REAL,
                current_price REAL,
                asset_class TEXT DEFAULT 'EQUITY',
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute(
            "INSERT INTO holdings_snapshot (stock_code, quantity, average_price, current_price) VALUES (?, ?, ?, ?)",
            ("TCS", 10, 3500.0, 4000.0),  # Value = 40,000
        )
        await db.commit()

    # 2. Add AA consent & fetch external holdings
    handle = await gateway.create_consent_request(customer_id="cust1@setu")
    await client.approve_consent(handle.consent_handle)
    await gateway.fetch_and_persist_fi_data(handle.consent_id)

    # 3. Compute consolidated summary
    summary = await gateway.get_unified_portfolio_summary()

    assert summary["primary_equities_value"] == 40000.0
    assert summary["external_equities_value"] > 0
    assert summary["mutual_funds_value"] > 0
    assert summary["nps_value"] > 0
    assert summary["total_unified_value"] > summary["primary_equities_value"]
    assert summary["holdings_count"] >= 5


@pytest.mark.anyio
async def test_consent_not_active_raises_error(temp_db_path):
    client = MockAAClient()
    gateway = AccountAggregatorGateway(client=client, db_path=temp_db_path)

    handle = await gateway.create_consent_request(customer_id="pending_user@finvu")

    # Trying to fetch FI data on PENDING consent should raise PermissionError
    with pytest.raises(PermissionError):
        await gateway.fetch_and_persist_fi_data(handle.consent_id)
