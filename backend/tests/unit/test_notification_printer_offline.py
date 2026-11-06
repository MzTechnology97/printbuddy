"""The "printer offline" notification fires once after a grace, not on a reconnect blip (upstream #1752).

No printer is contacted: the printer manager and the notification send are stubbed. Data is made up.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app import main
from backend.app.services.printer_manager import PrinterManager


@pytest.fixture
def offline(monkeypatch):
    """Grace of 50 ms; printer 1 known and disconnected unless a test says otherwise."""
    monkeypatch.setattr(main, "PRINTER_OFFLINE_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(main, "_printer_last_connected", {})
    monkeypatch.setattr(main, "_printer_offline_tasks", {})
    pm = MagicMock()
    pm.get_client.return_value = object()
    pm.is_connected.return_value = False
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = AsyncMock(return_value=SimpleNamespace(name="X1C", is_active=True))
    notify = MagicMock(on_printer_offline=AsyncMock())
    monkeypatch.setattr(main, "printer_manager", pm)
    monkeypatch.setattr(main, "async_session", MagicMock(return_value=session))
    monkeypatch.setattr(main, "notification_service", notify)
    return SimpleNamespace(pm=pm, notify=notify.on_printer_offline, session=session)


async def settle():
    await asyncio.sleep(0.15)


@pytest.mark.asyncio
async def test_offline_past_the_grace_notifies_once(offline):
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    main._track_printer_offline(1, False)  # the next polls of the same outage
    await settle()
    main._track_printer_offline(1, False)
    await settle()
    offline.notify.assert_awaited_once_with(1, "X1C", offline.session)


@pytest.mark.asyncio
async def test_short_blip_sends_nothing(offline):
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    main._track_printer_offline(1, True)  # back within the grace
    await settle()
    offline.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_notification_without_a_connected_edge_or_for_a_removed_printer(offline):
    main._track_printer_offline(1, False)  # offline since the start: never seen connected
    await settle()
    offline.notify.assert_not_awaited()

    offline.pm.get_client.return_value = None  # deleted or disabled meanwhile
    main._track_printer_offline(2, True)
    main._track_printer_offline(2, False)
    await settle()
    offline.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_status_change_handler_feeds_the_edge(offline):
    with patch.object(main, "_track_printer_offline") as track:
        try:
            await main.on_printer_status_change(1, SimpleNamespace(connected=False))
        except Exception:
            pass  # the rest of the handler needs a full state; only the edge is checked here
    track.assert_called_once_with(1, False)


def test_klipper_poll_reports_the_disconnect_edge():
    """HTTP providers (Moonraker) drop ``connected`` silently on a failed poll: the manager calls back."""
    manager = PrinterManager()
    calls = []
    manager._on_status_change = lambda pid, state: calls.append((pid, state.connected))
    manager._schedule_async = lambda result: None
    client = SimpleNamespace(state=SimpleNamespace(connected=True))

    def failed_poll():
        client.state.connected = False
        return False

    client.check_staleness = failed_poll
    assert manager._poll_provider(5, client) is False
    assert manager._poll_provider(5, client) is False  # still down: no second edge
    assert calls == [(5, False)]


@pytest.mark.asyncio
async def test_offline_flag_off_sends_nothing(db_session):
    from backend.app.models.notification import NotificationProvider
    from backend.app.services.notification_service import notification_service

    db_session.add(
        NotificationProvider(name="hook", provider_type="webhook", config="{}", enabled=True, on_printer_offline=False)
    )
    await db_session.commit()
    with patch.object(notification_service, "_send_to_providers", AsyncMock()) as send:
        await notification_service.on_printer_offline(1, "X1C", db_session)
    send.assert_not_awaited()

    provider = NotificationProvider(
        name="hook2", provider_type="webhook", config="{}", enabled=True, on_printer_offline=True
    )
    db_session.add(provider)
    await db_session.commit()
    with patch.object(notification_service, "_send_to_providers", AsyncMock()) as send:
        await notification_service.on_printer_offline(1, "X1C", db_session)
    assert [p.name for p in send.await_args.args[0]] == ["hook2"]
