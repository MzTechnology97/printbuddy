"""Network errors of a provider: a precise message (never the token) and retries in the background.

No request leaves the test: the HTTP client, the sleeps and the database are mocked.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.app.services import notification_service as ns
from backend.app.services.notification_service import NotificationNetworkError, NotificationService

TOKEN = "123456:SECRET-test-token"


def _telegram_provider():
    provider = MagicMock()
    provider.id = 7
    provider.name = "Telegram"
    provider.provider_type = "telegram"
    provider.enabled = True
    provider.quiet_hours_enabled = False
    provider.daily_digest_enabled = False
    provider.config = {"bot_token": TOKEN, "chat_id": "42"}
    return provider


def _timeout(url: str) -> httpx.ReadTimeout:
    return httpx.ReadTimeout("", request=httpx.Request("POST", url))


def test_endpoint_names_the_telegram_method_but_never_the_token():
    assert ns._endpoint(_timeout(f"https://api.telegram.org/bot{TOKEN}/sendPhoto")) == "api.telegram.org/sendPhoto"
    # Other providers carry secrets in the path too (Discord webhooks): only the host.
    assert ns._endpoint(_timeout("https://discord.com/api/webhooks/1/abc")) == "discord.com"
    assert ns._endpoint(httpx.ConnectError("refused")) == "unknown host"


@pytest.mark.asyncio
async def test_timeout_is_reported_with_its_type_endpoint_and_duration():
    service = NotificationService()
    client = MagicMock()
    client.post = AsyncMock(side_effect=_timeout(f"https://api.telegram.org/bot{TOKEN}/sendMessage"))
    with patch.object(service, "_get_client", AsyncMock(return_value=client)):
        success, error = await service._send_to_provider(_telegram_provider(), "Title", "Body")
        assert not success
        assert error.startswith("ReadTimeout from api.telegram.org/sendMessage after ")
        assert TOKEN not in error

        with pytest.raises(NotificationNetworkError):
            await service._send_to_provider(_telegram_provider(), "Title", "Body", raise_network=True)


@pytest.mark.asyncio
async def test_network_error_is_logged_and_retried_in_the_background():
    service = NotificationService()
    provider = _telegram_provider()
    with (
        patch.object(service, "_send_to_provider", AsyncMock(side_effect=NotificationNetworkError("ReadTimeout x"))),
        patch.object(service, "_retry_later") as retry_later,
        patch.object(service, "_update_provider_status", AsyncMock()) as status,
        patch.object(service, "_log_notification", AsyncMock()) as log,
    ):
        await service._send_to_providers([provider], "Title", "Body", AsyncMock(), event_type="print_start")

    retry_later.assert_called_once()
    assert retry_later.call_args.args[0] == provider.id
    error = status.call_args.args[3]
    assert error == f"ReadTimeout x (attempt 1/3); retrying in {ns.RETRY_DELAYS[0]}s"
    assert log.call_args.kwargs["error_message"] == error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcomes", "success", "sleeps"),
    [
        ([(True, "sent")], True, [5]),  # second try works
        ([NotificationNetworkError("ReadTimeout x"), (True, "sent")], True, [5, 20]),
        ([NotificationNetworkError("ReadTimeout x")] * 2, False, [5, 20]),  # gives up after the third
    ],
)
async def test_retry_waits_longer_each_time_and_logs_the_outcome_once(outcomes, success, sleeps):
    service = NotificationService()
    provider = _telegram_provider()
    db = AsyncMock()
    db.get = AsyncMock(return_value=provider)

    @asynccontextmanager
    async def session():
        yield db

    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    with (
        patch("backend.app.core.database.async_session", session),
        patch.object(ns.asyncio, "sleep", fake_sleep),
        patch.object(service, "_send_to_provider", AsyncMock(side_effect=outcomes)),
        patch.object(service, "_update_provider_status", AsyncMock()) as status,
        patch.object(service, "_log_notification", AsyncMock()) as log,
    ):
        await service._retry(provider.id, "Title", "Body", "print_start", 1, "X1C", None, None)

    assert slept == sleeps
    status.assert_called_once()
    assert status.call_args.args[2] is success
    log.assert_called_once()
    if not success:
        assert log.call_args.kwargs["error_message"] == "ReadTimeout x (attempt 3/3)"
