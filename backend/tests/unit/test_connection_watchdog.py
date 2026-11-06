"""Dead-MQTT-session watchdog (port of upstream #2732).

It must rebuild a Bambu session paho has stopped recovering, and leave alone
sessions that are healthy, still recovering on their own, switched off, or not
Bambu at all (Klipper reconnects over its own WebSocket).
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.main import (
    CONNECTION_WATCHDOG_OFFLINE_GRACE,
    CONNECTION_WATCHDOG_RETRY_INTERVAL,
    _connection_watchdog_last_attempt,
    _recover_dead_printer_sessions,
)
from backend.app.services.bambu_mqtt import BambuMQTTClient


def _client(*, connected: bool, last_message_age: float | None):
    client = MagicMock(spec=BambuMQTTClient)
    client.state = SimpleNamespace(connected=connected)
    client._last_message_time = 0.0 if last_message_age is None else time.time() - last_message_age
    client.ip_address = "printer.test"
    return client


async def _sweep(clients: dict, *, port_open: bool = True):
    with (
        patch("backend.app.main.printer_manager._clients", clients),
        patch("backend.app.services.printer_diagnostic.check_port", AsyncMock(return_value=port_open)),
    ):
        return await _recover_dead_printer_sessions()


@pytest.fixture(autouse=True)
def _clear_cooldowns():
    _connection_watchdog_last_attempt.clear()
    yield
    _connection_watchdog_last_attempt.clear()


@pytest.mark.asyncio
async def test_rebuilds_long_dead_session_that_still_answers():
    client = _client(connected=False, last_message_age=32718.0)
    assert await _sweep({1: client}) == 1
    client.force_reconnect_stale_session.assert_called_once()
    assert "32718" in client.force_reconnect_stale_session.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("connected", "age"),
    [
        (True, 99999.0),  # healthy
        (False, CONNECTION_WATCHDOG_OFFLINE_GRACE - 30),  # paho may still be recovering
        (False, None),  # never connected: initial connect is paho's job
    ],
)
async def test_leaves_healthy_recovering_and_new_sessions_alone(connected, age):
    client = _client(connected=connected, last_message_age=age)
    assert await _sweep({1: client}) == 0
    client.force_reconnect_stale_session.assert_not_called()


@pytest.mark.asyncio
async def test_switched_off_printer_is_left_to_paho():
    client = _client(connected=False, last_message_age=9999.0)
    assert await _sweep({1: client}, port_open=False) == 0
    client.force_reconnect_stale_session.assert_not_called()


@pytest.mark.asyncio
async def test_non_bambu_clients_are_skipped():
    klipper = SimpleNamespace(
        state=SimpleNamespace(connected=False),
        _last_message_time=time.time() - 9999,
        ip_address="klipper.test",
        force_reconnect_stale_session=MagicMock(),
    )
    assert await _sweep({1: klipper}) == 0
    klipper.force_reconnect_stale_session.assert_not_called()


@pytest.mark.asyncio
async def test_rate_limited_per_printer_and_cleared_on_reconnect():
    client = _client(connected=False, last_message_age=9999.0)
    assert await _sweep({1: client}) == 1
    assert await _sweep({1: client}) == 0  # inside the retry interval

    _connection_watchdog_last_attempt[1] -= CONNECTION_WATCHDOG_RETRY_INTERVAL + 1
    assert await _sweep({1: client}) == 1

    client.state.connected = True
    await _sweep({1: client})
    assert 1 not in _connection_watchdog_last_attempt


@pytest.mark.asyncio
async def test_one_failing_printer_does_not_stop_the_sweep():
    bad = _client(connected=False, last_message_age=9999.0)
    bad.force_reconnect_stale_session.side_effect = RuntimeError("boom")
    good = _client(connected=False, last_message_age=9999.0)
    await _sweep({1: bad, 2: good})
    good.force_reconnect_stale_session.assert_called_once()
