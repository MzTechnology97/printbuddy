"""Printfarm (fork): a Klipper error or a filament runout is notified once, as a printer error. Data is made up."""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from backend.app import main


def _state(klippy="ready", message=None, runout=False):
    return SimpleNamespace(klipper={"state": klippy, "message": message, "filament_runout": runout})


def test_klipper_alert_text():
    assert main._klipper_alert(SimpleNamespace()) is None  # not a Klipper
    assert main._klipper_alert(_state()) is None
    assert main._klipper_alert(_state("startup")) is None  # a restart in progress
    assert main._klipper_alert(_state("shutdown", "MCU 'mcu' shutdown: Timer too close\nOnce fixed...")) == (
        "Klipper shutdown",
        "MCU 'mcu' shutdown: Timer too close",
    )
    assert main._klipper_alert(_state("error"))[1] == "Klipper is not ready: restart the firmware"
    assert main._klipper_alert(_state(runout=True))[0] == "Filament runout"


@pytest.mark.asyncio
async def test_klipper_alert_is_notified_once_until_it_ends(monkeypatch):
    sent = []

    class _Db:
        async def execute(self, query):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(name="K1"))

    @asynccontextmanager
    async def session():
        yield _Db()

    async def on_printer_error(printer_id, name, error_type, db, detail, image_data=None):
        sent.append((printer_id, name, error_type, detail, image_data))

    async def snapshot(printer_id, printer, logger):
        return b"jpg"

    monkeypatch.setattr(main, "async_session", session)
    monkeypatch.setattr(main, "_capture_snapshot_for_notification", snapshot)
    monkeypatch.setattr(main.notification_service, "on_printer_error", on_printer_error)
    monkeypatch.setattr(main, "_notified_klipper_alert", {})
    monkeypatch.setattr(main, "_klipper_down_since", {})
    clock = [1000.0]
    monkeypatch.setattr(main, "_now", lambda: clock[0])

    runout = _state(runout=True)
    await main._notify_klipper_alert(7, runout)
    await main._notify_klipper_alert(7, runout)  # the next polls of the same pause
    assert sent == [(7, "K1", "Filament runout", "The print is paused: load the filament, then resume", b"jpg")]

    await main._notify_klipper_alert(7, _state())  # resumed
    await main._notify_klipper_alert(7, runout)  # a second runout later is notified again
    shutdown = _state("shutdown", "Lost communication with MCU 'mcu'")
    await main._notify_klipper_alert(7, shutdown)
    assert len(sent) == 2  # Klipper down: notified only if still down after the grace period
    clock[0] += 61
    await main._notify_klipper_alert(7, shutdown)
    await main._notify_klipper_alert(7, shutdown)
    assert [s[2] for s in sent] == ["Filament runout", "Filament runout", "Klipper shutdown"]

    # A firmware restart passes through disconnected and startup, then ready within the grace period: no alert.
    sent.clear()
    await main._notify_klipper_alert(7, _state())
    for klippy in ("disconnected", "shutdown", "startup", "ready"):
        clock[0] += 10
        await main._notify_klipper_alert(7, _state(klippy))
    clock[0] += 120
    await main._notify_klipper_alert(7, _state())
    assert sent == []
