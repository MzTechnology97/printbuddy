"""MQTT relay printer offline/online and low filament events (upstream 1.3.0), off unless switched on.

No broker, printer or Spoolman is contacted: the relay's publish is a mock. Data is made up.
"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app import main
from backend.app.schemas.settings import AppSettings
from backend.app.services.mqtt_relay import MQTTRelayService


def ready_relay() -> MQTTRelayService:
    relay = MQTTRelayService()
    relay.enabled = relay.connected = relay.printer_events = True
    relay.topic_prefix = "pb"
    relay._publish = MagicMock()
    return relay


def published(relay) -> list[tuple[str, dict]]:
    return [(c.args[0], c.args[1]) for c in relay._publish.call_args_list]


# --- off by default ---------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_off_by_default():
    assert AppSettings().mqtt_printer_events is False
    relay = MQTTRelayService()
    assert relay.printer_events is False
    relay.enabled = relay.connected = True  # the relay itself on: its other events go out, these don't
    relay._publish = MagicMock()
    await relay.on_printer_offline(1, "X1C", "SER-A")
    await relay.on_printer_online(1, "X1C", "SER-A")
    await relay.on_filament_low(1, "X1C", "SER-A", "A1", 10, None, 3)
    await relay.check_filament_low()
    relay._publish.assert_not_called()


@pytest.mark.asyncio
async def test_configure_reads_the_toggle(monkeypatch):
    relay = MQTTRelayService()
    monkeypatch.setattr(relay, "_connect", AsyncMock(return_value=True))
    monkeypatch.setattr(relay, "_configure_smart_plug_service", AsyncMock())
    await relay.configure({"mqtt_enabled": True, "mqtt_broker": "broker.example", "mqtt_printer_events": True})
    assert relay.printer_events is True
    await relay.configure({"mqtt_enabled": True, "mqtt_broker": "broker.example"})
    assert relay.printer_events is False


# --- offline / online, on the offline tracking of the notification ----------------------------------------------


@pytest.fixture
def offline(monkeypatch):
    """Grace of 50 ms; printer 1 known and disconnected unless a test says otherwise; relay events on."""
    monkeypatch.setattr(main, "PRINTER_OFFLINE_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(main, "_printer_last_connected", {})
    monkeypatch.setattr(main, "_printer_offline_tasks", {})
    monkeypatch.setattr(main, "_printer_offline_published", set())
    pm = MagicMock()
    pm.get_client.return_value = object()
    pm.is_connected.return_value = False
    pm.offline_expected.return_value = False
    pm.get_printer.return_value = SimpleNamespace(name="X1C", serial_number="SER-A")
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.get = AsyncMock(return_value=SimpleNamespace(name="X1C", serial_number="SER-A", is_active=True))
    notify = MagicMock(on_printer_offline=AsyncMock())
    relay = ready_relay()
    monkeypatch.setattr(main, "printer_manager", pm)
    monkeypatch.setattr(main, "async_session", MagicMock(return_value=session))
    monkeypatch.setattr(main, "notification_service", notify)
    monkeypatch.setattr(main, "mqtt_relay", relay)
    monkeypatch.setattr(main.print_scheduler, "clear_upload_backoff", MagicMock())
    return SimpleNamespace(pm=pm, notify=notify.on_printer_offline, relay=relay)


async def settle():
    await asyncio.sleep(0.15)


def topics(relay) -> list[str]:
    return [t for t, _ in published(relay)]


@pytest.mark.asyncio
async def test_offline_after_the_grace_then_online_on_reconnect(offline):
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    main._track_printer_offline(1, False)
    await settle()
    assert topics(offline.relay) == ["pb/printers/SER-A/offline"]
    _, payload = published(offline.relay)[0]
    assert payload["printer_id"] == 1 and payload["printer_name"] == "X1C" and payload["printer_serial"] == "SER-A"
    offline.notify.assert_awaited_once()  # the notification still goes out as before

    offline.pm.is_connected.return_value = True
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, True)  # later status updates: no second online
    await settle()
    assert topics(offline.relay) == ["pb/printers/SER-A/offline", "pb/printers/SER-A/online"]


@pytest.mark.asyncio
async def test_blip_publishes_neither(offline):
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    main._track_printer_offline(1, True)  # back within the grace
    await settle()
    offline.relay._publish.assert_not_called()


@pytest.mark.asyncio
async def test_our_own_power_off_publishes_neither(offline):
    offline.pm.offline_expected.return_value = True  # smart plug auto-off after a print
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    await settle()
    offline.pm.offline_expected.side_effect = [False, True]  # powered off by us after the edge was seen
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    await settle()
    main._track_printer_offline(1, True)  # it comes back: no online either, no offline went out
    await settle()
    offline.relay._publish.assert_not_called()


@pytest.mark.asyncio
async def test_events_off_publishes_nothing_and_no_online_later(offline):
    offline.relay.printer_events = False
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    await settle()
    offline.notify.assert_awaited_once()  # the notification does not depend on the relay
    offline.relay.printer_events = True
    main._track_printer_offline(1, True)
    await settle()
    offline.relay._publish.assert_not_called()


@pytest.mark.asyncio
async def test_online_waits_for_the_broker(offline):
    main._track_printer_offline(1, True)
    main._track_printer_offline(1, False)
    await settle()
    offline.relay.connected = False  # broker down when the printer comes back
    main._track_printer_offline(1, True)
    await settle()
    offline.relay.connected = True
    main._track_printer_offline(1, True)  # next status update
    await settle()
    assert topics(offline.relay) == ["pb/printers/SER-A/offline", "pb/printers/SER-A/online"]


# --- low filament -------------------------------------------------------------------------------------------------


@pytest.fixture
def relay_db(monkeypatch, db_session):
    relay = ready_relay()

    @asynccontextmanager
    async def session():
        yield db_session

    monkeypatch.setattr("backend.app.core.database.async_session", session)
    return relay


async def assigned_spool(db_session, printer_factory, **spool):
    from backend.app.models.spool import Spool
    from backend.app.models.spool_assignment import SpoolAssignment

    printer = await printer_factory(name="P1S", serial_number="SER-B", ip_address="printer.example")
    s = Spool(material="PLA", color_name="Jade White", label_weight=1000, **spool)
    db_session.add(s)
    await db_session.commit()
    db_session.add(SpoolAssignment(spool_id=s.id, printer_id=printer.id, ams_id=0, tray_id=1))
    await db_session.commit()
    return printer, s


@pytest.mark.asyncio
async def test_low_filament_once_per_crossing(relay_db, db_session, printer_factory):
    printer, spool = await assigned_spool(db_session, printer_factory, weight_used=850)  # 15 % < default 20 %

    async def check_at(used):
        spool.weight_used = used
        await db_session.commit()
        await relay_db.check_filament_low()

    await relay_db.check_filament_low()
    assert topics(relay_db) == ["pb/printers/SER-B/filament/low"]
    _, payload = published(relay_db)[0]
    assert payload["printer_id"] == printer.id and payload["slot"] == "A2" and payload["remaining_percent"] == 15
    assert payload["color"] == "Jade White" and payload["spool_id"] == spool.id

    await check_at(860)  # still low
    await check_at(790)  # 21 %: just above the threshold, inside the re-arm margin
    await check_at(850)  # below again: the same crossing, quiet
    assert len(topics(relay_db)) == 1

    await check_at(700)  # 30 %: clearly back above, re-armed
    await check_at(700)
    assert len(topics(relay_db)) == 1
    await check_at(900)  # crosses again
    assert len(topics(relay_db)) == 2


@pytest.mark.asyncio
async def test_low_filament_uses_the_spool_threshold_and_skips_archived(relay_db, db_session, printer_factory):
    from datetime import datetime, timezone

    _, spool = await assigned_spool(db_session, printer_factory, weight_used=700, low_stock_threshold_pct=40)
    await relay_db.check_filament_low()  # 30 % under its own 40 %
    assert len(topics(relay_db)) == 1

    relay_db._relayed_filament_low.clear()
    spool.archived_at = datetime.now(timezone.utc)
    await db_session.commit()
    await relay_db.check_filament_low()
    assert len(topics(relay_db)) == 1


@pytest.mark.asyncio
async def test_low_filament_record_kept_over_broker_drop_and_cleared_when_switched_off(
    relay_db, db_session, printer_factory
):
    await assigned_spool(db_session, printer_factory, weight_used=900)
    await relay_db.check_filament_low()
    relay_db.connected = False
    await relay_db.check_filament_low()
    relay_db.connected = True
    await relay_db.check_filament_low()  # reconnect: not re-sent
    assert len(topics(relay_db)) == 1

    relay_db.printer_events = False
    await relay_db.check_filament_low()
    relay_db.printer_events = True
    await relay_db.check_filament_low()  # switched back on: told what is low now
    assert len(topics(relay_db)) == 2


@pytest.mark.asyncio
async def test_low_filament_spoolman_mode(relay_db, db_session, printer_factory, monkeypatch):
    from backend.app.models.settings import Settings
    from backend.app.models.spoolman_slot_assignment import SpoolmanSlotAssignment

    printer = await printer_factory(name="K1", serial_number="SER-K", ip_address="printer.example")
    db_session.add(Settings(key="spoolman_enabled", value="true"))
    db_session.add(SpoolmanSlotAssignment(printer_id=printer.id, ams_id=255, tray_id=0, spoolman_spool_id=7))
    await db_session.commit()
    client = MagicMock(
        get_all_spools=AsyncMock(
            return_value=[{"id": 7, "remaining_weight": 100, "filament": {"weight": 1000, "name": "PLA"}}]
        )
    )
    monkeypatch.setattr("backend.app.services.spoolman.get_spoolman_client", AsyncMock(return_value=client))
    await relay_db.check_filament_low()
    assert topics(relay_db) == ["pb/printers/SER-K/filament/low"]
    _, payload = published(relay_db)[0]
    assert payload["spool_id"] == 7 and payload["remaining_percent"] == 10 and payload["slot"] == "Ext-L"
