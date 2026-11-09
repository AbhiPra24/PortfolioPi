"""Account Aggregator Expansion Gateway interface for PortfolioPi.

Implements secure abstractions for the Sahamati Account Aggregator (AA) framework
(Setu / Finvu FIU API client abstraction) supporting:
- Consent management (creation, status check/webhook processing)
- Financial Information (FI) data fetching
- Normalization into unified format (external demat equities, mutual fund folios, NPS tier-1 holdings)
- Local persistence in SQLite (`external_holdings` table) strictly respecting user privacy.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from app_config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and Types
# ---------------------------------------------------------------------------

class FIType(str, Enum):
    EQUITIES = "EQUITIES"
    MUTUAL_FUNDS = "MUTUAL_FUNDS"
    NPS = "NPS"
    ETF = "ETF"
    TERM_DEPOSIT = "TERM_DEPOSIT"
    RECURRING_DEPOSIT = "RECURRING_DEPOSIT"
    GOVT_SECURITIES = "GOVT_SECURITIES"


class ConsentStatus(str, Enum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    PAUSED = "PAUSED"


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    MUTUAL_FUND = "MUTUAL_FUND"
    NPS = "NPS"
    OTHER = "OTHER"


class AAPublisherProvider(str, Enum):
    SETU = "SETU"
    FINVU = "FINVU"
    MOCK = "MOCK"


# ---------------------------------------------------------------------------
# Domain Models (Pydantic / Dataclasses)
# ---------------------------------------------------------------------------

@dataclass
class ConsentHandle:
    consent_id: str
    consent_handle: str
    customer_id: str
    fi_types: List[str]
    status: ConsentStatus
    created_at: str
    redirect_url: Optional[str] = None
    account_aggregator_id: Optional[str] = None


@dataclass
class ExternalHolding:
    source_fiu: str
    account_id: str  # Demat ID, Folio Number, PRAN
    fi_type: str  # EQUITIES, MUTUAL_FUNDS, NPS
    asset_class: str  # EQUITY, MUTUAL_FUND, NPS
    symbol: str  # NSE symbol, Scheme code, Pension Fund Scheme
    isin: Optional[str]
    name: str
    quantity: float
    average_price: float
    current_price: float
    current_value: float
    cost_value: float
    unrealized_pnl: float
    folio_number: Optional[str] = None
    pran: Optional[str] = None
    dp_id: Optional[str] = None
    last_nav_date: Optional[str] = None
    raw_payload_hash: Optional[str] = None
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AggregatedFinancialProfile:
    customer_id: str
    consent_id: str
    session_id: str
    holdings: List[ExternalHolding] = field(default_factory=list)
    raw_fi_data: Optional[Dict[str, Any]] = None
    total_value: float = 0.0
    total_cost: float = 0.0
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def calculate_totals(self) -> None:
        self.total_value = sum(h.current_value for h in self.holdings)
        self.total_cost = sum(h.cost_value for h in self.holdings)


# ---------------------------------------------------------------------------
# FI Normalizers (Equities, Mutual Funds, NPS)
# ---------------------------------------------------------------------------

class AccountAggregatorNormalizer:
    """Normalizes raw Sahamati/Setu/Finvu FI data payloads (JSON or parsed XML)

    into PortfolioPi unified ExternalHolding models.
    """

    @staticmethod
    def _parse_xml_or_json(raw_data: Union[str, bytes, dict, list]) -> Dict[str, Any]:
        """Convert input data into a standardized dictionary. Supports raw JSON string,

        dict, or XML string.
        """
        if isinstance(raw_data, (bytes, bytearray)):
            raw_data = raw_data.decode("utf-8")

        if isinstance(raw_data, str):
            trimmed = raw_data.strip()
            if trimmed.startswith("{") or trimmed.startswith("["):
                parsed = json.loads(trimmed)
                return {"root": parsed} if isinstance(parsed, list) else parsed
            elif trimmed.startswith("<"):
                return AccountAggregatorNormalizer._xml_to_dict(trimmed)
            else:
                try:
                    parsed = json.loads(trimmed)
                    return parsed if isinstance(parsed, dict) else {"root": parsed}
                except json.JSONDecodeError:
                    raise ValueError(f"Unrecognized payload format: {trimmed[:50]}...")
        elif isinstance(raw_data, dict):
            return raw_data
        elif isinstance(raw_data, list):
            return {"accounts": raw_data}
        else:
            raise TypeError(f"Unsupported raw data type: {type(raw_data)}")

    @staticmethod
    def _xml_to_dict(xml_str: str) -> Dict[str, Any]:
        """Converts an XML string to a Python dictionary without requiring third-party xmltodict."""
        import xml.etree.ElementTree as ET

        def _element_to_dict(element: ET.Element) -> Any:
            node: Dict[str, Any] = {}
            # Include XML attributes
            for key, val in element.attrib.items():
                node[f"@{key}"] = val

            children = list(element)
            text = (element.text or "").strip()

            if not children:
                if element.attrib:
                    if text:
                        node["#text"] = text
                    return node
                return text

            child_dict: Dict[str, Any] = {}
            for child in children:
                child_data = _element_to_dict(child)
                child_tag = child.tag.split("}")[-1]  # strip xml namespace if present
                if child_tag in child_dict:
                    if isinstance(child_dict[child_tag], list):
                        child_dict[child_tag].append(child_data)
                    else:
                        child_dict[child_tag] = [child_dict[child_tag], child_data]
                else:
                    child_dict[child_tag] = child_data

            node.update(child_dict)
            if text:
                node["#text"] = text
            return node

        root = ET.fromstring(xml_str)
        root_tag = root.tag.split("}")[-1]
        return {root_tag: _element_to_dict(root)}

    @classmethod
    def normalize_profile(
        cls,
        raw_data: Union[str, bytes, dict, list],
        fiu_provider: str = "SETU",
    ) -> List[ExternalHolding]:
        """Normalize an entire FI response which may contain accounts for multiple FI types."""
        data = cls._parse_xml_or_json(raw_data)
        holdings: List[ExternalHolding] = []

        # Find account list inside common top-level structures
        # e.g., Setu: { "Payload": [ { "fipId": ..., "data": [ ... ] } ] }
        # or Finvu: { "Account": [ ... ] } or { "accounts": [ ... ] }
        accounts = cls._extract_accounts(data)

        for account in accounts:
            fi_type = cls._detect_fi_type(account)
            if fi_type == FIType.EQUITIES.value:
                holdings.extend(cls.normalize_equities(account, fiu_provider=fiu_provider))
            elif fi_type == FIType.MUTUAL_FUNDS.value:
                holdings.extend(cls.normalize_mutual_funds(account, fiu_provider=fiu_provider))
            elif fi_type == FIType.NPS.value:
                holdings.extend(cls.normalize_nps(account, fiu_provider=fiu_provider))
            else:
                logger.debug(f"Skipping or unknown FI type '{fi_type}' in account payload")

        return holdings

    @classmethod
    def _extract_accounts(cls, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Walk dictionary to locate list of account records."""
        # Top-level checks
        if "accounts" in data and isinstance(data["accounts"], list):
            return data["accounts"]
        if "Account" in data and isinstance(data["Account"], list):
            return data["Account"]
        if "Account" in data and isinstance(data["Account"], dict):
            return [data["Account"]]

        # Setu FI fetch format: {"Payload": [{"fipId": "...", "data": [...]}]}
        if "Payload" in data and isinstance(data["Payload"], list):
            results = []
            for item in data["Payload"]:
                if isinstance(item, dict):
                    if "data" in item and isinstance(item["data"], list):
                        results.extend(item["data"])
                    elif "data" in item and isinstance(item["data"], dict):
                        results.append(item["data"])
                    elif "Account" in item:
                        acc = item["Account"]
                        if isinstance(acc, list):
                            results.extend(acc)
                        else:
                            results.append(acc)
                    else:
                        results.append(item)
            return results

        # Root key wrapper check
        for key in ("FI", "FinancialInformation", "body", "data"):
            if key in data and isinstance(data[key], dict):
                extracted = cls._extract_accounts(data[key])
                if extracted:
                    return extracted
            elif key in data and isinstance(data[key], list):
                return data[key]

        # If data itself contains holding/summary keys, treat as single account
        if any(k in data for k in ("Holdings", "holdings", "Summary", "summary", "Investment", "investment")):
            return [data]

        return []

    @classmethod
    def _detect_fi_type(cls, account: Dict[str, Any]) -> str:
        """Determines if the account represents EQUITIES, MUTUAL_FUNDS, or NPS."""
        # Direct fiType attribute
        for field in ("fiType", "@fiType", "type", "asset_type"):
            val = str(account.get(field, "")).upper()
            if "EQUITY" in val or "EQUITIES" in val:
                return FIType.EQUITIES.value
            if "MUTUAL" in val or "MF" in val:
                return FIType.MUTUAL_FUNDS.value
            if "NPS" in val or "PENSION" in val:
                return FIType.NPS.value

        # Inspect profile / summary / tags
        profile = account.get("Profile") or account.get("profile") or {}
        if isinstance(profile, dict):
            for field in ("type", "category"):
                val = str(profile.get(field, "")).upper()
                if "EQUITY" in val or "EQUITIES" in val:
                    return FIType.EQUITIES.value
                if "MUTUAL" in val:
                    return FIType.MUTUAL_FUNDS.value
                if "NPS" in val:
                    return FIType.NPS.value

        # Inspect holdings keys
        holdings = account.get("Holdings") or account.get("holdings") or {}
        if isinstance(holdings, dict):
            if "Holding" in holdings:
                h_item = holdings["Holding"]
                first = h_item[0] if isinstance(h_item, list) and h_item else h_item
                if isinstance(first, dict):
                    if "isin" in first or "isinCode" in first or "companyName" in first or "@isin" in first:
                        # Check whether it's mutual fund (INF prefix) or equity (INE prefix)
                        isin = str(first.get("isin") or first.get("isinCode") or first.get("@isin") or "").upper()
                        if isin.startswith("INF"):
                            return FIType.MUTUAL_FUNDS.value
                        return FIType.EQUITIES.value
            if "Scheme" in holdings or "scheme" in holdings:
                return FIType.NPS.value

        # Check account summary or masks
        summary = account.get("Summary") or account.get("summary") or {}
        if isinstance(summary, dict):
            if "pran" in summary or "@pran" in summary:
                return FIType.NPS.value
            if "folioNo" in summary or "@folioNo" in summary:
                return FIType.MUTUAL_FUNDS.value
            if "dematId" in summary or "@dematId" in summary:
                return FIType.EQUITIES.value

        return FIType.EQUITIES.value

    @classmethod
    def normalize_equities(cls, account: Dict[str, Any], fiu_provider: str = "SETU") -> List[ExternalHolding]:
        """Normalize Demat / Equity holdings into ExternalHolding objects."""
        holdings_list: List[ExternalHolding] = []
        account_id = str(
            account.get("linkedAccRef")
            or account.get("maskedAccNumber")
            or account.get("dematId")
            or account.get("@dematId")
            or account.get("account_id")
            or "DEMAT_UNKNOWN"
        )
        dp_id = str(account.get("dpId") or account.get("@dpId") or "")

        holdings_container = account.get("Holdings") or account.get("holdings") or {}
        raw_items: List[Dict[str, Any]] = []

        if isinstance(holdings_container, list):
            raw_items = holdings_container
        elif isinstance(holdings_container, dict):
            h_obj = holdings_container.get("Holding") or holdings_container.get("holding") or []
            if isinstance(h_obj, list):
                raw_items = h_obj
            elif isinstance(h_obj, dict):
                raw_items = [h_obj]

        for item in raw_items:
            isin = str(item.get("isin") or item.get("isinCode") or item.get("@isin") or "").strip()
            symbol = str(
                item.get("symbol")
                or item.get("@symbol")
                or item.get("tradingSymbol")
                or item.get("companyName")
                or isin
            ).strip().upper()
            name = str(item.get("companyName") or item.get("description") or symbol).strip()
            
            # Clean symbols like 'NSE:RELIANCE' or 'BSE:500325'
            clean_symbol = symbol.split(":")[-1]

            qty = float(item.get("units") or item.get("quantity") or item.get("@units") or 0.0)
            avg_price = float(
                item.get("averagePrice")
                or item.get("costPrice")
                or item.get("@averagePrice")
                or item.get("rate")
                or 0.0
            )
            cur_price = float(
                item.get("currentPrice")
                or item.get("lastTradedPrice")
                or item.get("@currentPrice")
                or item.get("nav")
                or avg_price
            )

            current_val = float(item.get("currentValue") or (qty * cur_price))
            cost_val = float(item.get("totalCost") or (qty * avg_price))
            unrealized_pnl = current_val - cost_val

            holding = ExternalHolding(
                source_fiu=fiu_provider,
                account_id=account_id,
                fi_type=FIType.EQUITIES.value,
                asset_class=AssetClass.EQUITY.value,
                symbol=clean_symbol,
                isin=isin or None,
                name=name,
                quantity=qty,
                average_price=round(avg_price, 2),
                current_price=round(cur_price, 2),
                current_value=round(current_val, 2),
                cost_value=round(cost_val, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                dp_id=dp_id or None,
                last_nav_date=str(item.get("asOn") or item.get("date") or "") or None,
            )
            holdings_list.append(holding)

        return holdings_list

    @classmethod
    def normalize_mutual_funds(cls, account: Dict[str, Any], fiu_provider: str = "SETU") -> List[ExternalHolding]:
        """Normalize Mutual Fund Folios into ExternalHolding objects."""
        holdings_list: List[ExternalHolding] = []
        account_id = str(
            account.get("linkedAccRef")
            or account.get("maskedAccNumber")
            or account.get("folioNo")
            or account.get("@folioNo")
            or "FOLIO_UNKNOWN"
        )
        folio_no = str(account.get("folioNo") or account.get("@folioNo") or account_id)

        holdings_container = account.get("Holdings") or account.get("holdings") or {}
        raw_items: List[Dict[str, Any]] = []

        if isinstance(holdings_container, list):
            raw_items = holdings_container
        elif isinstance(holdings_container, dict):
            h_obj = holdings_container.get("Holding") or holdings_container.get("holding") or []
            if isinstance(h_obj, list):
                raw_items = h_obj
            elif isinstance(h_obj, dict):
                raw_items = [h_obj]

        for item in raw_items:
            isin = str(item.get("isin") or item.get("isinCode") or item.get("@isin") or "").strip()
            scheme_code = str(item.get("amfiCode") or item.get("schemeCode") or isin).strip()
            name = str(
                item.get("schemeName")
                or item.get("description")
                or item.get("name")
                or scheme_code
            ).strip()

            qty = float(item.get("units") or item.get("closingUnits") or item.get("@units") or 0.0)
            cur_price = float(
                item.get("nav")
                or item.get("currentPrice")
                or item.get("@nav")
                or 0.0
            )
            cost_val = float(item.get("totalCost") or item.get("investedValue") or 0.0)
            avg_price = float(item.get("averagePrice") or (cost_val / qty if qty > 0 else 0.0))

            current_val = float(item.get("currentValue") or (qty * cur_price))
            if cost_val == 0.0 and avg_price > 0:
                cost_val = qty * avg_price

            unrealized_pnl = current_val - cost_val

            holding = ExternalHolding(
                source_fiu=fiu_provider,
                account_id=account_id,
                fi_type=FIType.MUTUAL_FUNDS.value,
                asset_class=AssetClass.MUTUAL_FUND.value,
                symbol=scheme_code or isin or name,
                isin=isin or None,
                name=name,
                quantity=round(qty, 4),
                average_price=round(avg_price, 4),
                current_price=round(cur_price, 4),
                current_value=round(current_val, 2),
                cost_value=round(cost_val, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                folio_number=folio_no,
                last_nav_date=str(item.get("navDate") or item.get("asOn") or "") or None,
            )
            holdings_list.append(holding)

        return holdings_list

    @classmethod
    def normalize_nps(cls, account: Dict[str, Any], fiu_provider: str = "SETU") -> List[ExternalHolding]:
        """Normalize NPS (National Pension System Tier-1 / Tier-2) into ExternalHolding objects."""
        holdings_list: List[ExternalHolding] = []
        account_id = str(
            account.get("linkedAccRef")
            or account.get("maskedAccNumber")
            or account.get("pran")
            or account.get("@pran")
            or "NPS_PRAN_UNKNOWN"
        )
        pran = str(account.get("pran") or account.get("@pran") or account_id)

        holdings_container = account.get("Holdings") or account.get("holdings") or {}
        raw_items: List[Dict[str, Any]] = []

        if isinstance(holdings_container, list):
            raw_items = holdings_container
        elif isinstance(holdings_container, dict):
            # In NPS schemas, schemes can be under 'Scheme' or 'holding'
            h_obj = holdings_container.get("Scheme") or holdings_container.get("Holding") or []
            if isinstance(h_obj, list):
                raw_items = h_obj
            elif isinstance(h_obj, dict):
                raw_items = [h_obj]

        for item in raw_items:
            scheme_id = str(
                item.get("schemeId")
                or item.get("schemeCode")
                or item.get("@schemeId")
                or item.get("pensionFundManager")
                or "NPS_SCHEME"
            ).strip()
            name = str(
                item.get("schemeName")
                or item.get("description")
                or item.get("pensionFundManager")
                or scheme_id
            ).strip()

            qty = float(item.get("units") or item.get("@units") or 0.0)
            cur_price = float(item.get("nav") or item.get("@nav") or item.get("currentPrice") or 0.0)
            cost_val = float(item.get("totalCost") or item.get("contributedAmount") or 0.0)
            avg_price = float(item.get("averagePrice") or (cost_val / qty if qty > 0 else 0.0))

            current_val = float(item.get("currentValue") or (qty * cur_price))
            if cost_val == 0.0 and avg_price > 0:
                cost_val = qty * avg_price

            unrealized_pnl = current_val - cost_val

            holding = ExternalHolding(
                source_fiu=fiu_provider,
                account_id=account_id,
                fi_type=FIType.NPS.value,
                asset_class=AssetClass.NPS.value,
                symbol=scheme_id,
                isin=None,
                name=name,
                quantity=round(qty, 4),
                average_price=round(avg_price, 4),
                current_price=round(cur_price, 4),
                current_value=round(current_val, 2),
                cost_value=round(cost_val, 2),
                unrealized_pnl=round(unrealized_pnl, 2),
                pran=pran,
                last_nav_date=str(item.get("navDate") or item.get("asOn") or "") or None,
            )
            holdings_list.append(holding)

        return holdings_list


# ---------------------------------------------------------------------------
# Account Aggregator Client Abstraction (Base + Setu / Finvu Mock Clients)
# ---------------------------------------------------------------------------

class BaseAAClient:
    """Abstract interface defining required AA operations for an FIU client."""

    async def create_consent_request(
        self,
        customer_id: str,
        fi_types: Optional[List[str]] = None,
        redirect_url: Optional[str] = None,
    ) -> ConsentHandle:
        raise NotImplementedError

    async def check_consent_status(self, consent_handle: str) -> ConsentStatus:
        raise NotImplementedError

    async def fetch_fi_data(
        self,
        consent_id: str,
        session_id: Optional[str] = None,
    ) -> AggregatedFinancialProfile:
        raise NotImplementedError


class MockAAClient(BaseAAClient):
    """Mock implementation of Sahamati Account Aggregator client for testing and simulation.

    Supports custom fixture injections and simulating state transitions (PENDING -> ACTIVE).
    """

    def __init__(self, provider_name: str = "MOCK"):
        self.provider_name = provider_name
        self._consents: Dict[str, ConsentHandle] = {}
        self._mock_fi_fixtures: Dict[str, Dict[str, Any]] = {}

    def set_fixture(self, consent_id: str, fixture_data: Dict[str, Any]) -> None:
        self._mock_fi_fixtures[consent_id] = fixture_data

    async def create_consent_request(
        self,
        customer_id: str,
        fi_types: Optional[List[str]] = None,
        redirect_url: Optional[str] = None,
    ) -> ConsentHandle:
        if fi_types is None:
            fi_types = [FIType.EQUITIES.value, FIType.MUTUAL_FUNDS.value, FIType.NPS.value]

        consent_id = f"consent-{uuid.uuid4().hex[:12]}"
        handle_id = f"handle-{uuid.uuid4().hex[:12]}"

        handle = ConsentHandle(
            consent_id=consent_id,
            consent_handle=handle_id,
            customer_id=customer_id,
            fi_types=fi_types,
            status=ConsentStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(),
            redirect_url=redirect_url or f"https://aa.example.com/consent/{handle_id}",
            account_aggregator_id="finvu@mock",
        )
        self._consents[handle_id] = handle
        self._consents[consent_id] = handle
        return handle

    async def approve_consent(self, consent_handle: str) -> None:
        """Helper for unit tests to simulate customer consenting in mobile app/webview."""
        if consent_handle in self._consents:
            self._consents[consent_handle].status = ConsentStatus.ACTIVE

    async def check_consent_status(self, consent_handle: str) -> ConsentStatus:
        if consent_handle not in self._consents:
            return ConsentStatus.EXPIRED
        return self._consents[consent_handle].status

    async def fetch_fi_data(
        self,
        consent_id: str,
        session_id: Optional[str] = None,
    ) -> AggregatedFinancialProfile:
        consent = self._consents.get(consent_id)
        if not consent:
            raise ValueError(f"Unknown consent_id: {consent_id}")

        if consent.status != ConsentStatus.ACTIVE:
            raise PermissionError(f"Consent {consent_id} is in state {consent.status.value}, not ACTIVE.")

        session = session_id or f"session-{uuid.uuid4().hex[:8]}"

        # Look up injected fixture or default fixture
        raw_payload = self._mock_fi_fixtures.get(consent_id)
        if raw_payload is None:
            raw_payload = self._generate_sample_fi_data(consent.fi_types)

        holdings = AccountAggregatorNormalizer.normalize_profile(
            raw_payload,
            fiu_provider=self.provider_name,
        )

        profile = AggregatedFinancialProfile(
            customer_id=consent.customer_id,
            consent_id=consent_id,
            session_id=session,
            holdings=holdings,
            raw_fi_data=raw_payload,
        )
        profile.calculate_totals()
        return profile

    def _generate_sample_fi_data(self, fi_types: List[str]) -> Dict[str, Any]:
        """Generates realistic Sahamati multi-asset payload."""
        accounts = []

        if FIType.EQUITIES.value in fi_types:
            accounts.append({
                "fiType": "EQUITIES",
                "linkedAccRef": "1208160012345678",
                "maskedAccNumber": "XXXXXX5678",
                "dpId": "IN300123",
                "Holdings": {
                    "Holding": [
                        {
                            "companyName": "Reliance Industries Limited",
                            "symbol": "RELIANCE",
                            "isin": "INE002A01018",
                            "units": 25,
                            "averagePrice": 2450.50,
                            "currentPrice": 2890.00,
                            "currentValue": 72250.00,
                            "totalCost": 61262.50,
                        },
                        {
                            "companyName": "Infosys Limited",
                            "symbol": "INFY",
                            "isin": "INE009A01021",
                            "units": 50,
                            "averagePrice": 1420.00,
                            "currentPrice": 1640.00,
                            "currentValue": 82000.00,
                            "totalCost": 71000.00,
                        },
                    ]
                },
            })

        if FIType.MUTUAL_FUNDS.value in fi_types:
            accounts.append({
                "fiType": "MUTUAL_FUNDS",
                "linkedAccRef": "FOLIO-102938475",
                "folioNo": "FOLIO-102938475",
                "Holdings": {
                    "Holding": [
                        {
                            "schemeName": "Parag Parikh Flexi Cap Fund Direct Growth",
                            "amfiCode": "122639",
                            "isin": "INF879O01019",
                            "units": 1500.450,
                            "nav": 74.85,
                            "averagePrice": 52.30,
                            "currentValue": 112308.68,
                            "totalCost": 78473.54,
                            "navDate": "2026-11-08",
                        }
                    ]
                },
            })

        if FIType.NPS.value in fi_types:
            accounts.append({
                "fiType": "NPS",
                "linkedAccRef": "PRAN-110099887766",
                "pran": "PRAN-110099887766",
                "Holdings": {
                    "Scheme": [
                        {
                            "schemeId": "SM001001",
                            "pensionFundManager": "HDFC Pension Management - Scheme E Tier I",
                            "units": 2340.125,
                            "nav": 48.25,
                            "averagePrice": 35.10,
                            "currentValue": 112911.03,
                            "totalCost": 82138.39,
                            "navDate": "2026-11-08",
                        },
                        {
                            "schemeId": "SM001002",
                            "pensionFundManager": "HDFC Pension Management - Scheme C Tier I",
                            "units": 1120.500,
                            "nav": 32.10,
                            "averagePrice": 25.00,
                            "currentValue": 35968.05,
                            "totalCost": 28012.50,
                            "navDate": "2026-11-08",
                        },
                    ]
                },
            })

        return {"accounts": accounts}


class SetuFIUClient(BaseAAClient):
    """Setu Account Aggregator FIU API client implementation.

    Integrates with Setu FIU standard REST APIs:
    - POST /consents (create consent request)
    - GET /consents/{id}/status (check consent status)
    - POST /sessions (fetch financial data)
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: str = "https://fiu-sandbox.setu.co",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.client_id:
            headers["x-client-id"] = self.client_id
        if self.client_secret:
            headers["x-client-secret"] = self.client_secret
        return headers

    async def create_consent_request(
        self,
        customer_id: str,
        fi_types: Optional[List[str]] = None,
        redirect_url: Optional[str] = None,
    ) -> ConsentHandle:
        import httpx

        if fi_types is None:
            fi_types = [FIType.EQUITIES.value, FIType.MUTUAL_FUNDS.value, FIType.NPS.value]

        payload = {
            "vpa": customer_id,
            "fiTypes": fi_types,
            "redirectUrl": redirect_url or "https://portfoliopi.local/aa/callback",
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/consents",
                json=payload,
                headers=self._get_headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

        consent_id = data.get("id") or data.get("consentId") or ""
        handle_id = data.get("handle") or data.get("consentHandle") or consent_id
        status_str = data.get("status", "PENDING").upper()

        return ConsentHandle(
            consent_id=consent_id,
            consent_handle=handle_id,
            customer_id=customer_id,
            fi_types=fi_types,
            status=ConsentStatus(status_str) if status_str in ConsentStatus.__members__ else ConsentStatus.PENDING,
            created_at=data.get("createdAt", datetime.now(timezone.utc).isoformat()),
            redirect_url=data.get("url") or data.get("redirectUrl"),
            account_aggregator_id=data.get("aaId", "setu"),
        )

    async def check_consent_status(self, consent_handle: str) -> ConsentStatus:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/consents/{consent_handle}/status",
                headers=self._get_headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

        status_str = (data.get("status") or data.get("consentStatus") or "PENDING").upper()
        return ConsentStatus(status_str) if status_str in ConsentStatus.__members__ else ConsentStatus.PENDING

    async def fetch_fi_data(
        self,
        consent_id: str,
        session_id: Optional[str] = None,
    ) -> AggregatedFinancialProfile:
        import httpx

        async with httpx.AsyncClient() as client:
            # Step 1: Create FI data session if not provided
            if not session_id:
                sess_resp = await client.post(
                    f"{self.base_url}/sessions",
                    json={"consentId": consent_id},
                    headers=self._get_headers(),
                    timeout=10.0,
                )
                sess_resp.raise_for_status()
                session_id = sess_resp.json().get("id") or f"sess-{uuid.uuid4().hex[:8]}"

            # Step 2: Fetch session data
            data_resp = await client.get(
                f"{self.base_url}/sessions/{session_id}",
                headers=self._get_headers(),
                timeout=15.0,
            )
            data_resp.raise_for_status()
            raw_data = data_resp.json()

        holdings = AccountAggregatorNormalizer.normalize_profile(
            raw_data,
            fiu_provider=AAPublisherProvider.SETU.value,
        )

        profile = AggregatedFinancialProfile(
            customer_id="",
            consent_id=consent_id,
            session_id=session_id or "",
            holdings=holdings,
            raw_fi_data=raw_data,
        )
        profile.calculate_totals()
        return profile


class FinvuFIUClient(BaseAAClient):
    """Finvu Account Aggregator FIU API client implementation."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        base_url: str = "https://finvu.net/fiu",
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.base_url = base_url.rstrip("/")

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
        }
        if self.client_id:
            headers["client-id"] = self.client_id
        if self.client_secret:
            headers["client-secret"] = self.client_secret
        return headers

    async def create_consent_request(
        self,
        customer_id: str,
        fi_types: Optional[List[str]] = None,
        redirect_url: Optional[str] = None,
    ) -> ConsentHandle:
        import httpx

        if fi_types is None:
            fi_types = [FIType.EQUITIES.value, FIType.MUTUAL_FUNDS.value, FIType.NPS.value]

        payload = {
            "customerId": customer_id,
            "fiTypes": fi_types,
            "redirectUrl": redirect_url,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/Consent",
                json=payload,
                headers=self._get_headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

        consent_id = data.get("ConsentHandle") or data.get("consentId") or ""
        return ConsentHandle(
            consent_id=consent_id,
            consent_handle=consent_id,
            customer_id=customer_id,
            fi_types=fi_types,
            status=ConsentStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(),
            redirect_url=data.get("redirectUrl"),
            account_aggregator_id="finvu",
        )

    async def check_consent_status(self, consent_handle: str) -> ConsentStatus:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/Consent/handle/{consent_handle}",
                headers=self._get_headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()

        status_str = (data.get("status") or data.get("consentStatus") or "PENDING").upper()
        return ConsentStatus(status_str) if status_str in ConsentStatus.__members__ else ConsentStatus.PENDING

    async def fetch_fi_data(
        self,
        consent_id: str,
        session_id: Optional[str] = None,
    ) -> AggregatedFinancialProfile:
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/FI/request",
                json={"consentId": consent_id, "sessionId": session_id},
                headers=self._get_headers(),
                timeout=15.0,
            )
            resp.raise_for_status()
            raw_data = resp.json()

        holdings = AccountAggregatorNormalizer.normalize_profile(
            raw_data,
            fiu_provider=AAPublisherProvider.FINVU.value,
        )

        profile = AggregatedFinancialProfile(
            customer_id="",
            consent_id=consent_id,
            session_id=session_id or f"sess-{uuid.uuid4().hex[:8]}",
            holdings=holdings,
            raw_fi_data=raw_data,
        )
        profile.calculate_totals()
        return profile


# ---------------------------------------------------------------------------
# Account Aggregator Expansion Gateway (Persistence & Orchestrator)
# ---------------------------------------------------------------------------

class AccountAggregatorGateway:
    """Expansion Gateway for Sahamati Account Aggregator (AA) framework.

    Handles consent workflows, FI data fetch, format normalization, and
    local encrypted/sanitized SQLite persistence in `external_holdings`.
    """

    def __init__(
        self,
        client: Optional[BaseAAClient] = None,
        db_path: Optional[str] = None,
    ):
        self.client = client or MockAAClient()
        self.db_path = db_path or settings.db_path

    async def init_schema(self) -> None:
        """Create the `external_holdings` and `aa_consents` tables if they do not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")

            await db.execute("""
                CREATE TABLE IF NOT EXISTS external_holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_fiu TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    fi_type TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    isin TEXT,
                    name TEXT,
                    quantity REAL NOT NULL,
                    average_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    current_value REAL NOT NULL,
                    cost_value REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    folio_number TEXT,
                    pran TEXT,
                    dp_id TEXT,
                    last_nav_date TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_fiu, account_id, symbol)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS aa_consents (
                    consent_id TEXT PRIMARY KEY,
                    consent_handle TEXT UNIQUE,
                    customer_id TEXT NOT NULL,
                    fi_types TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    redirect_url TEXT,
                    account_aggregator_id TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_ext_holdings_pk ON external_holdings(source_fiu, account_id, symbol);"
            )
            await db.commit()

    async def create_consent_request(
        self,
        customer_id: str,
        fi_types: Optional[List[str]] = None,
        redirect_url: Optional[str] = None,
    ) -> ConsentHandle:
        """Initiate consent artifact request via FIU provider."""
        await self.init_schema()

        if fi_types is None:
            fi_types = [FIType.EQUITIES.value, FIType.MUTUAL_FUNDS.value, FIType.NPS.value]

        handle = await self.client.create_consent_request(
            customer_id=customer_id,
            fi_types=fi_types,
            redirect_url=redirect_url,
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO aa_consents (
                    consent_id, consent_handle, customer_id, fi_types, status, created_at, redirect_url, account_aggregator_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    handle.consent_id,
                    handle.consent_handle,
                    handle.customer_id,
                    json.dumps(handle.fi_types),
                    handle.status.value,
                    handle.created_at,
                    handle.redirect_url,
                    handle.account_aggregator_id,
                ),
            )
            await db.commit()

        logger.info(f"Created AA consent request {handle.consent_id} for {customer_id}")
        return handle

    async def check_consent_status(self, consent_handle_or_id: str) -> ConsentStatus:
        """Poll or verify status of a consent request and update local DB state."""
        await self.init_schema()

        # Query local consent record first
        local_record: Optional[Tuple] = None
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT consent_id, consent_handle, status FROM aa_consents WHERE consent_handle = ? OR consent_id = ?",
                (consent_handle_or_id, consent_handle_or_id),
            ) as cursor:
                local_record = await cursor.fetchone()

        handle = consent_handle_or_id
        if local_record and local_record[1]:
            handle = local_record[1]

        remote_status = await self.client.check_consent_status(handle)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE aa_consents 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE consent_handle = ? OR consent_id = ?
                """,
                (remote_status.value, consent_handle_or_id, consent_handle_or_id),
            )
            await db.commit()

        return remote_status

    async def handle_consent_webhook(self, webhook_payload: Dict[str, Any]) -> ConsentStatus:
        """Processes asynchronous webhook notifications emitted by AA / FIU."""
        await self.init_schema()

        # Common webhook shapes: {"ConsentStatusNotification": {"consentId": "...", "consentStatus": "ACTIVE"}}
        # or Setu: {"consentId": "...", "status": "ACTIVE"}
        consent_id = None
        status_val = None

        if "ConsentStatusNotification" in webhook_payload:
            notif = webhook_payload["ConsentStatusNotification"]
            consent_id = notif.get("consentId")
            status_val = notif.get("consentStatus")
        else:
            consent_id = webhook_payload.get("consentId") or webhook_payload.get("consent_id")
            status_val = webhook_payload.get("status") or webhook_payload.get("consentStatus")

        if not consent_id or not status_val:
            raise ValueError(f"Malformed webhook payload: {webhook_payload}")

        status = ConsentStatus(status_val.upper())

        # If client has simulated state, also update client
        if hasattr(self.client, "_consents"):
            for k in (consent_id,):
                if k in self.client._consents:
                    self.client._consents[k].status = status

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE aa_consents
                SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE consent_id = ? OR consent_handle = ?
                """,
                (status.value, consent_id, consent_id),
            )
            await db.commit()

        logger.info(f"Handled AA webhook: Consent {consent_id} updated to {status.value}")
        return status

    async def fetch_and_persist_fi_data(
        self,
        consent_id: str,
        session_id: Optional[str] = None,
    ) -> AggregatedFinancialProfile:
        """Fetches FI data from the AA client, normalizes holdings, and commits them to SQLite."""
        await self.init_schema()

        profile = await self.client.fetch_fi_data(consent_id, session_id=session_id)

        async with aiosqlite.connect(self.db_path) as db:
            for h in profile.holdings:
                await db.execute(
                    """
                    INSERT INTO external_holdings (
                        source_fiu, account_id, fi_type, asset_class, symbol, isin, name,
                        quantity, average_price, current_price, current_value, cost_value,
                        unrealized_pnl, folio_number, pran, dp_id, last_nav_date, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(source_fiu, account_id, symbol) DO UPDATE SET
                        quantity = excluded.quantity,
                        average_price = excluded.average_price,
                        current_price = excluded.current_price,
                        current_value = excluded.current_value,
                        cost_value = excluded.cost_value,
                        unrealized_pnl = excluded.unrealized_pnl,
                        last_nav_date = excluded.last_nav_date,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        h.source_fiu,
                        h.account_id,
                        h.fi_type,
                        h.asset_class,
                        h.symbol,
                        h.isin,
                        h.name,
                        h.quantity,
                        h.average_price,
                        h.current_price,
                        h.current_value,
                        h.cost_value,
                        h.unrealized_pnl,
                        h.folio_number,
                        h.pran,
                        h.dp_id,
                        h.last_nav_date,
                    ),
                )
            await db.commit()

        logger.info(f"Persisted {len(profile.holdings)} external holdings for consent {consent_id}")
        return profile

    async def get_external_holdings(
        self,
        fi_type: Optional[str] = None,
        asset_class: Optional[str] = None,
    ) -> List[ExternalHolding]:
        """Query persisted external holdings with optional filtering."""
        await self.init_schema()

        query = "SELECT source_fiu, account_id, fi_type, asset_class, symbol, isin, name, quantity, average_price, current_price, current_value, cost_value, unrealized_pnl, folio_number, pran, dp_id, last_nav_date, updated_at FROM external_holdings WHERE 1=1"
        params: List[Any] = []

        if fi_type:
            query += " AND fi_type = ?"
            params.append(fi_type)
        if asset_class:
            query += " AND asset_class = ?"
            params.append(asset_class)

        query += " ORDER BY current_value DESC"

        results: List[ExternalHolding] = []
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    results.append(
                        ExternalHolding(
                            source_fiu=row[0],
                            account_id=row[1],
                            fi_type=row[2],
                            asset_class=row[3],
                            symbol=row[4],
                            isin=row[5],
                            name=row[6],
                            quantity=row[7],
                            average_price=row[8],
                            current_price=row[9],
                            current_value=row[10],
                            cost_value=row[11],
                            unrealized_pnl=row[12],
                            folio_number=row[13],
                            pran=row[14],
                            dp_id=row[15],
                            last_nav_date=row[16],
                            updated_at=row[17],
                        )
                    )
        return results

    async def get_unified_portfolio_summary(self) -> Dict[str, Any]:
        """Provides a consolidated view combining primary `holdings_snapshot`

        with `external_holdings` across all asset classes (Equities, MFs, NPS).
        """
        await self.init_schema()

        summary: Dict[str, Any] = {
            "primary_equities_value": 0.0,
            "external_equities_value": 0.0,
            "mutual_funds_value": 0.0,
            "nps_value": 0.0,
            "total_unified_value": 0.0,
            "holdings_count": 0,
        }

        async with aiosqlite.connect(self.db_path) as db:
            # Check if holdings_snapshot table exists
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='holdings_snapshot'"
            ) as cur:
                has_snapshot = await cur.fetchone()

            if has_snapshot:
                async with db.execute(
                    "SELECT COALESCE(SUM(quantity * current_price), 0.0), COUNT(*) FROM holdings_snapshot"
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        summary["primary_equities_value"] = float(row[0])
                        summary["holdings_count"] += int(row[1])

            # Query external holdings breakdown
            async with db.execute(
                """
                SELECT asset_class, COALESCE(SUM(current_value), 0.0), COUNT(*) 
                FROM external_holdings 
                GROUP BY asset_class
                """
            ) as cur:
                rows = await cur.fetchall()
                for ac, val, count in rows:
                    val_float = float(val)
                    summary["holdings_count"] += int(count)
                    if ac == AssetClass.EQUITY.value:
                        summary["external_equities_value"] = val_float
                    elif ac == AssetClass.MUTUAL_FUND.value:
                        summary["mutual_funds_value"] = val_float
                    elif ac == AssetClass.NPS.value:
                        summary["nps_value"] = val_float

        summary["total_unified_value"] = (
            summary["primary_equities_value"]
            + summary["external_equities_value"]
            + summary["mutual_funds_value"]
            + summary["nps_value"]
        )

        return summary
