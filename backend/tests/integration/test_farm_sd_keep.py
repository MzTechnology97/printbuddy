"""Printfarm (fork): Bambu printers that keep their files on the SD after a print (reprint from the display).

No printer is contacted: FTP deletes, the printer manager and the idle check are stubbed. Data is made up.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select, update

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


def _session_factory(db_session):
    class _Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


async def run_cleanup(db_session, *, on_printer, delete=None, idle=lambda *a, **k: True):
    """``cleanup`` with the printers' file lists and deletes stubbed; returns the paths it deleted."""
    deleted: list[str] = []

    async def names(printer_id, provider, ip, code, model):
        if on_printer is None:
            raise OSError("printer offline")
        return set(on_printer)

    async def fake_delete(ip, code, remote_path, **kwargs):
        if delete is not None:
            return await delete(remote_path)
        deleted.append(remote_path)
        return True

    with (
        patch.object(printer_files, "_list_names", names),
        patch("backend.app.services.bambu_ftp.delete_file_async", fake_delete),
        patch("backend.app.services.print_scheduler.scheduler._is_printer_idle", idle),
    ):
        count = await printer_files.cleanup(db_session)
    return count, deleted


async def left(db_session):
    return sorted(r.remote_path for r in (await db_session.execute(select(FarmPrinterFile))).scalars())


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
    on_printer = {"vecchio__11111111.3mf", "ristampa.3mf", "stampa__33333333.3mf", "invio__44444444.3mf"}
    # the idle check alone would let all three through: the queue must hold the busy ones back
    count, deleted = await run_cleanup(db_session, on_printer=on_printer)
    assert count == 2
    assert sorted(deleted) == ["/ristampa.3mf", "/vecchio__11111111.3mf"]
    assert await left(db_session) == ["/invio__44444444.3mf", "/recente__22222222.3mf", "/stampa__33333333.3mf"]


@pytest.mark.asyncio
async def test_a_file_picked_up_while_the_cleanup_runs_is_not_deleted(db_session, printer_factory):
    """The race: the clean-up read its list, then the queue reuses an old file (or starts sending) meanwhile."""
    a = await printer_factory(name="A1 uno", model="A1")
    b = await printer_factory(name="X1C", model="X1C")
    c = await printer_factory(name="H2D", model="H2D")
    old = printer_files._now() - timedelta(days=30)
    rows = [
        FarmPrinterFile(printer_id=a.id, remote_path="/primo__11111111.3mf", last_used_at=old),
        FarmPrinterFile(printer_id=b.id, remote_path="/riusato__22222222.3mf", last_used_at=old),
        FarmPrinterFile(printer_id=c.id, remote_path="/inviato__33333333.3mf", last_used_at=old),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    deleted: list[str] = []

    async def delete(remote_path):
        deleted.append(remote_path)
        if remote_path == "/primo__11111111.3mf":  # while this delete runs, the scheduler works on b and c
            await db_session.execute(
                update(FarmPrinterFile)
                .where(FarmPrinterFile.printer_id == b.id)
                .values(last_used_at=printer_files._now())
            )
            db_session.add(
                PrintQueueItem(printer_id=c.id, position=1, status="pending", dispatching_at=printer_files._now())
            )
            await db_session.commit()
        return True

    names = {"primo__11111111.3mf", "riusato__22222222.3mf", "inviato__33333333.3mf"}
    count, _ = await run_cleanup(db_session, on_printer=names, delete=delete)
    assert (count, deleted) == (1, ["/primo__11111111.3mf"])
    assert await left(db_session) == ["/inviato__33333333.3mf", "/riusato__22222222.3mf"]


@pytest.mark.asyncio
async def test_reuse_marks_the_file_used_so_the_cleanup_leaves_it(db_session, printer_factory):
    x1c = await printer_factory(name="X1C", model="X1C")
    row = FarmPrinterFile(
        printer_id=x1c.id, remote_path="/Sopra__1a2b3c4d.3mf", last_used_at=printer_files._now() - timedelta(days=30)
    )
    db_session.add(row)
    await db_session.commit()

    async def names(*args):
        return {"Sopra__1a2b3c4d.3mf"}

    with (
        patch.object(printer_files, "_list_names", names),
        patch("backend.app.core.database.async_session", _session_factory(db_session)),
    ):
        assert await printer_files.check_reusable(x1c.id, "bambu", "ip", "code", "/Sopra__1a2b3c4d.3mf", "X1C")
    await db_session.refresh(row)
    assert row.last_used_at > printer_files._now() - timedelta(minutes=1)
    printer_files._reusable.clear()


@pytest.mark.asyncio
async def test_a_failed_delete_keeps_the_record_and_a_missing_file_is_forgotten(db_session, printer_factory):
    x1c = await printer_factory(name="X1C", model="X1C")
    old = printer_files._now() - timedelta(days=30)
    db_session.add_all(
        [
            FarmPrinterFile(printer_id=x1c.id, remote_path="/bloccato__11111111.3mf", last_used_at=old),
            FarmPrinterFile(printer_id=x1c.id, remote_path="/sparito__22222222.3mf", last_used_at=old),
        ]
    )
    await db_session.commit()

    async def refuse(remote_path):
        return False  # what delete_file_async returns when the FTP delete fails

    count, _ = await run_cleanup(db_session, on_printer={"bloccato__11111111.3mf"}, delete=refuse)
    # The file still on the SD stays recorded (tried again next time); the one already gone is forgotten.
    assert count == 0
    assert await left(db_session) == ["/bloccato__11111111.3mf"]


@pytest.mark.asyncio
async def test_an_offline_printer_keeps_its_records(db_session, printer_factory):
    x1c = await printer_factory(name="X1C", model="X1C")
    db_session.add(
        FarmPrinterFile(
            printer_id=x1c.id,
            remote_path="/vecchio__11111111.3mf",
            last_used_at=printer_files._now() - timedelta(days=30),
        )
    )
    await db_session.commit()
    assert await run_cleanup(db_session, on_printer=None) == (0, [])  # the list fails: nothing deleted or forgotten
    assert await run_cleanup(db_session, on_printer=set(), idle=lambda *a, **k: False) == (0, [])  # not idle
    assert await left(db_session) == ["/vecchio__11111111.3mf"]


@pytest.mark.asyncio
async def test_plain_names_are_recorded_only_on_bambu_printers_that_keep_files(db_session, printer_factory):
    """Settings off (and Klipper): only fingerprinted queue files are recorded, exactly as before this change."""
    kept = await printer_factory(name="X1C", model="X1C")
    not_kept = await printer_factory(name="H2D", model="H2D")
    klipper = await printer_factory(name="K1", model="K1", provider="klipper")
    await printer_files.save_settings(db_session, 14, {kept.id})
    with patch("backend.app.core.database.async_session", _session_factory(db_session)):
        for printer in (kept, not_kept, klipper):
            assert await printer_files.before_upload(printer.id, "/Ristampa.3mf") is False  # never reused
            await printer_files.before_upload(printer.id, "/Coda__1a2b3c4d.gcode")
    rows = (await db_session.execute(select(FarmPrinterFile))).scalars().all()
    assert sorted((r.printer_id, r.remote_path) for r in rows) == sorted(
        [
            (kept.id, "/Ristampa.3mf"),
            (kept.id, "/Coda__1a2b3c4d.gcode"),
            (not_kept.id, "/Coda__1a2b3c4d.gcode"),
            (klipper.id, "/Coda__1a2b3c4d.gcode"),
        ]
    )


@pytest.mark.asyncio
async def test_corrupt_settings_fall_back_to_nothing_kept(async_client, db_session, printer_factory):
    from backend.app.api.routes.settings import set_setting

    p1s = await printer_factory(name="P1S", model="P1S")
    await set_setting(db_session, printer_files.KEEP_PRINTERS_KEY, "{non json")
    await set_setting(db_session, printer_files.KEEP_DAYS_KEY, "tanti")
    await db_session.commit()
    assert await printer_files.kept_printers(db_session) == set()
    assert await printer_files.keep_days(db_session) == printer_files.KEEP_DAYS
    got = (await async_client.get("/api/v1/farm/sd-files")).json()
    assert got["keep_days"] == 14
    assert [(p["id"], p["keep"]) for p in got["printers"]] == [(p1s.id, False)]
