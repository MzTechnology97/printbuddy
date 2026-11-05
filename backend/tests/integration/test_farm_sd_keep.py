"""Printfarm (fork): Bambu printers that keep their files on the SD after a print (reprint from the display).

No printer is contacted: FTP deletes, the printer manager and the idle check are stubbed. Data is made up.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from backend.app.farm import printer_files
from backend.app.farm.printer_files import FarmPrinterFile
from backend.app.models.print_queue import PrintQueueItem

# ---------------------------------------------------------------- settings


@pytest.mark.asyncio
async def test_settings_list_bambu_printers_with_the_ghost_print_warning(async_client, printer_factory):
    p1s = await printer_factory(name="P1S", model="P1S")
    x1c = await printer_factory(name="X1C", model="X1C")
    k2 = await printer_factory(name="K2", model="Creality K2 Pro", provider="klipper")

    got = (await async_client.get("/api/v1/farm/sd-files")).json()
    assert got["keep_days"] == 14
    assert [(p["name"], p["keep"], p["ghost_print_risk"]) for p in got["printers"]] == [
        ("P1S", False, True),
        ("X1C", False, False),
    ]

    # Klipper printers are not Bambu: never kept (their files are not deleted after a print anyway).
    put = await async_client.put(
        "/api/v1/farm/sd-files", json={"keep_days": 7, "keep_printer_ids": [x1c.id, k2.id, 999]}
    )
    assert put.status_code == 200
    assert put.json()["keep_days"] == 7
    assert {p["id"]: p["keep"] for p in put.json()["printers"]} == {p1s.id: False, x1c.id: True}
    assert (await async_client.get("/api/v1/farm/sd-files")).json()["printers"][1]["keep"] is True

    assert (await async_client.put("/api/v1/farm/sd-files", json={"keep_days": 0})).status_code == 422


@pytest.mark.parametrize(
    ("model", "risk"),
    [
        ("P1S", True),
        ("A1 mini", True),
        ("A1", True),
        ("X1C", False),
        ("H2D", False),
        ("X2D", False),
        ("P2S", False),
        (None, False),
    ],
)
def test_ghost_print_risk_by_model(model, risk):
    assert printer_files.ghost_print_risk(model) is risk


# ---------------------------------------------------------------- end of a print


async def complete(printer, data: dict, *, keep: bool, bambu: bool = True) -> list[str]:
    """``on_print_complete`` with the DB and everything around it stubbed; returns the SD paths deleted."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = printer
    result.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    deleted: list[str] = []

    async def fake_delete(ip, code, remote_path, **kwargs):
        deleted.append(remote_path)
        return True

    async def keeps(printer_id):
        return keep

    before = set(asyncio.all_tasks())
    with (
        patch("backend.app.main.async_session", return_value=session),
        patch("backend.app.core.database.async_session", return_value=session),
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.notification_service") as notify,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main._should_attempt_bambu_ftp_archive", lambda p: bambu),
        patch("backend.app.services.bambu_ftp.delete_file_async", fake_delete),
        patch.object(printer_files, "keeps_files", keeps),
    ):
        ws.send_print_complete = AsyncMock()
        ws.broadcast = AsyncMock()
        relay.on_print_complete = AsyncMock()
        relay.on_queue_job_completed = AsyncMock()
        notify.on_print_complete = AsyncMock()
        plug.on_print_complete = AsyncMock()
        pm.get_printer.return_value = None
        from backend.app.main import on_print_complete

        await on_print_complete(printer.id, data)
        for task in asyncio.all_tasks() - before:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    return deleted


def _printer(**kw):
    p = MagicMock(id=4, ip_address="192.0.2.4", access_code="00000000", model="A1", provider="bambu")
    p.name = "A1 prova"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


PRINT = {"status": "completed", "filename": "Sopra__1a2b3c4d.3mf", "subtask_name": "Sopra__1a2b3c4d"}


@pytest.mark.asyncio
async def test_a_kept_file_is_not_deleted_at_the_end_of_the_print():
    assert await complete(_printer(), PRINT, keep=True) == []


@pytest.mark.asyncio
async def test_without_the_setting_the_file_is_deleted_as_before():
    assert await complete(_printer(), PRINT, keep=False) == ["/Sopra__1a2b3c4d.3mf", "/Sopra__1a2b3c4d.gcode"]


@pytest.mark.asyncio
async def test_klipper_is_unchanged():
    """No FTP for Klipper, with or without the setting."""
    klipper = _printer(model="K1", provider="klipper")
    assert await complete(klipper, PRINT, keep=False, bambu=False) == []
    assert await complete(klipper, PRINT, keep=True, bambu=False) == []


# ---------------------------------------------------------------- clean-up


@pytest.mark.asyncio
async def test_cleanup_uses_the_days_setting_and_never_touches_files_in_use(db_session, printer_factory):
    idle = await printer_factory(name="A1 uno", model="A1")
    printing = await printer_factory(name="A1 due", model="A1")
    sending = await printer_factory(name="X1C", model="X1C")
    await printer_files.save_settings(db_session, 10, {idle.id, printing.id, sending.id})
    now = printer_files._now()
    old, older = now - timedelta(days=11), now - timedelta(days=40)
    db_session.add_all(
        [
            FarmPrinterFile(printer_id=idle.id, remote_path="/vecchio__11111111.3mf", last_used_at=old),
            FarmPrinterFile(printer_id=idle.id, remote_path="/ristampa.3mf", last_used_at=old),
            FarmPrinterFile(printer_id=idle.id, remote_path="/recente__22222222.3mf", last_used_at=now),
            FarmPrinterFile(printer_id=printing.id, remote_path="/stampa__33333333.3mf", last_used_at=older),
            FarmPrinterFile(printer_id=sending.id, remote_path="/invio__44444444.3mf", last_used_at=older),
            PrintQueueItem(printer_id=printing.id, position=1, status="printing"),
            PrintQueueItem(printer_id=sending.id, position=2, status="pending", dispatching_at=now),
        ]
    )
    await db_session.commit()
    deleted = []

    async def fake_delete(ip, code, remote_path, **kwargs):
        deleted.append(remote_path)
        return True

    with (
        patch("backend.app.services.bambu_ftp.delete_file_async", fake_delete),
        # the idle check alone would let all three through: the queue must hold the busy ones back
        patch("backend.app.services.print_scheduler.scheduler._is_printer_idle", lambda *a, **k: True),
    ):
        assert await printer_files.cleanup(db_session) == 2
    assert sorted(deleted) == ["/ristampa.3mf", "/vecchio__11111111.3mf"]
    left = sorted(r.remote_path for r in (await db_session.execute(select(FarmPrinterFile))).scalars())
    assert left == ["/invio__44444444.3mf", "/recente__22222222.3mf", "/stampa__33333333.3mf"]


def _session_factory(db_session):
    class _Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


@pytest.mark.asyncio
async def test_reprints_and_prints_outside_the_queue_are_recorded_but_never_reused(db_session):
    """A plain name (archive reprint, print from the library) goes to the clean-up; reuse stays fingerprinted."""
    with patch("backend.app.core.database.async_session", _session_factory(db_session)):
        assert await printer_files.before_upload(1, "/Ristampa.3mf") is False
        assert await printer_files.before_upload(1, "/Ristampa.3mf") is False
    rows = (await db_session.execute(select(FarmPrinterFile))).scalars().all()
    assert [(r.printer_id, r.remote_path) for r in rows] == [(1, "/Ristampa.3mf")]
