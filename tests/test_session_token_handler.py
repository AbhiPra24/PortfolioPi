"""Unit tests for Telegram session token extraction, validation, and Breeze sync."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app_config import settings
from bot.handlers import (
    extract_session_token,
    session_message_handler,
    handle_session_token_submission,
)
from core.database import init_db
from core.session_store import get_session


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    test_db_path = str(tmp_path / "test_magic_login.db")
    monkeypatch.setattr(settings, "db_path", test_db_path)
    return test_db_path


class DummyUser:
    def __init__(self, user_id: int):
        self.id = user_id


class DummyMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies = []

    async def reply_text(self, text: str, **kwargs):
        self.replies.append({"text": text, "kwargs": kwargs})
        return self


class DummyUpdate:
    def __init__(self, text: str, user_id: int):
        self.effective_user = DummyUser(user_id)
        self.message = DummyMessage(text)


class DummyApplication:
    def __init__(self):
        self.bot = MagicMock()


class DummyContext:
    def __init__(self):
        self.application = DummyApplication()
        self.args = []


# ============================================================================
# 1. Regex and Token Extraction Tests
# ============================================================================

def test_extract_session_token_redirect_url():
    url = "https://127.0.0.1/?apisession=42893847"
    assert extract_session_token(url) == "42893847"

    url_with_params = "https://api.icicidirect.com/redirect?code=xyz&apisession=abc123XYZ90&state=ok"
    assert extract_session_token(url_with_params) == "abc123XYZ90"


def test_extract_session_token_plain_query_or_message():
    assert extract_session_token("apisession=987654321") == "987654321"
    assert extract_session_token("Here is my token: apisession=TokenVal12345 please use it") == "TokenVal12345"


def test_extract_session_token_raw_alphanumeric():
    raw_token = "43829104"
    assert extract_session_token(raw_token) == "43829104"

    raw_token_alphanumeric = "a1b2c3d4e5f67890"
    assert extract_session_token(raw_token_alphanumeric) == "a1b2c3d4e5f67890"


def test_extract_session_token_invalid():
    assert extract_session_token("") is None
    assert extract_session_token("   ") is None
    assert extract_session_token("hello world how are you") is None
    assert extract_session_token("short") is None  # < 8 chars


# ============================================================================
# 2. Session Token Submission & Validation Tests
# ============================================================================

@pytest.mark.anyio
async def test_handle_valid_token_submission(temp_db, monkeypatch):
    await init_db()
    owner_id = settings.owner_ids_list[0] if settings.owner_ids_list else 12345
    monkeypatch.setattr(settings, "telegram_owner_ids", str(owner_id))

    token = "ValidSessionToken12345"
    mock_customer_details = {
        "Success": {
            "idirect_user_name": "Abhinav Prakash",
            "idirect_userid": "ABHI123",
            "idirect_lastlogin_time": "09-Nov-2026 09:15:00",
        }
    }

    update = DummyUpdate(text=f"https://127.0.0.1/?apisession={token}", user_id=owner_id)
    context = DummyContext()

    with patch("bot.handlers.BreezeClient") as MockBreezeClient, \
         patch("core.data_refresh.run_breeze_sync", new_callable=AsyncMock) as mock_sync:

        mock_instance = MagicMock()
        mock_instance.get_customer_details.return_value = mock_customer_details
        MockBreezeClient.return_value = mock_instance

        # Call the message handler
        await session_message_handler(update, context)

        # Allow background asyncio task to execute
        await asyncio.sleep(0.05)

        MockBreezeClient.assert_called_once_with(token)
        mock_instance.get_customer_details.assert_called_once_with(api_session=token)
        mock_sync.assert_called_once_with(context.application, silent=False)

    # Verify session persisted to SQLite
    stored_token = await get_session()
    assert stored_token == token

    # Check Telegram HTML response
    assert len(update.message.replies) == 1
    reply_text = update.message.replies[0]["text"]
    assert "Breeze Session Authenticated Successfully!" in reply_text
    assert "Abhinav Prakash" in reply_text
    assert "ABHI123" in reply_text
    assert "09-Nov-2026 09:15:00" in reply_text


@pytest.mark.anyio
async def test_handle_invalid_token_submission(temp_db, monkeypatch):
    await init_db()
    owner_id = settings.owner_ids_list[0] if settings.owner_ids_list else 12345
    monkeypatch.setattr(settings, "telegram_owner_ids", str(owner_id))

    invalid_token = "BadSessionToken9999"
    update = DummyUpdate(text=invalid_token, user_id=owner_id)
    context = DummyContext()

    with patch("bot.handlers.BreezeClient") as MockBreezeClient, \
         patch("core.data_refresh.run_breeze_sync", new_callable=AsyncMock) as mock_sync:

        mock_instance = MagicMock()
        mock_instance.get_customer_details.return_value = {
            "Error": "Session Token is invalid or expired"
        }
        MockBreezeClient.return_value = mock_instance

        await session_message_handler(update, context)
        await asyncio.sleep(0.05)

        mock_sync.assert_not_called()

    # Session must NOT be saved
    stored_token = await get_session()
    assert stored_token is None

    # Error reply sent
    assert len(update.message.replies) == 1
    reply_text = update.message.replies[0]["text"]
    assert "Breeze Validation Failed" in reply_text
    assert "Session Token is invalid or expired" in reply_text


@pytest.mark.anyio
async def test_handle_breeze_client_exception(temp_db, monkeypatch):
    await init_db()
    owner_id = settings.owner_ids_list[0] if settings.owner_ids_list else 12345
    monkeypatch.setattr(settings, "telegram_owner_ids", str(owner_id))

    token = "CrashSessionToken8888"
    update = DummyUpdate(text=f"apisession={token}", user_id=owner_id)
    context = DummyContext()

    with patch("bot.handlers.BreezeClient") as MockBreezeClient:
        MockBreezeClient.side_effect = Exception("Network timeout connecting to ICICI Breeze API")

        await session_message_handler(update, context)

    stored_token = await get_session()
    assert stored_token is None

    assert len(update.message.replies) == 1
    reply_text = update.message.replies[0]["text"]
    assert "Error Validating Token" in reply_text
    assert "Network timeout connecting to ICICI Breeze API" in reply_text


@pytest.mark.anyio
async def test_session_message_handler_ignores_non_token_messages(temp_db, monkeypatch):
    await init_db()
    owner_id = settings.owner_ids_list[0] if settings.owner_ids_list else 12345
    monkeypatch.setattr(settings, "telegram_owner_ids", str(owner_id))

    update = DummyUpdate(text="Hello bot! Just a random greeting.", user_id=owner_id)
    context = DummyContext()

    with patch("bot.handlers.BreezeClient") as MockBreezeClient:
        await session_message_handler(update, context)
        MockBreezeClient.assert_not_called()

    assert len(update.message.replies) == 0
