"""A busy printer gets no start, and a printer refusing the upload keeps the queue (upstream #2598, #3210, #3409).

No printer is contacted: the printer manager, uploads and notifications are stubbed. Data is made up.
"""

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services import print_scheduler as ps
from backend.app.services.print_scheduler import PrintScheduler


async def _same_name(name, path):
    return name


async def run_queue(test_engine, scheduler, *, refusing=(), states=None, started=True):
    """One real ``check_queue`` and ``_start_print``: printers idle and connected, uploads to ``refusing`` fail.

    Returns (printer ids the upload was tried on, the notification stub).
    """
    tried: list[int] = []

    async def upload(**kwargs):
        tried.append(kwargs["printer_id"])
        return kwargs["printer_id"] not in refusing

    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch.object(ps, "async_session", sessions),
        patch.object(ps, "printer_manager") as pm,
        patch.object(ps, "notification_service") as notify,
        patch.object(ps, "get_ftp_retry_settings", AsyncMock(return_value=(False, 1, 1, 10))),
        patch.object(ps, "delete_file_async", AsyncMock()),
        patch.object(ps.farm_printer_files, "fingerprint", _same_name),
        patch("backend.app.services.archive.ArchiveService.archive_print", AsyncMock(return_value=None)),
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[3])),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_delete_remote_file_for_provider", AsyncMock()),
        patch.object(scheduler, "_upload_file_for_provider", upload),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_mark_printer_dispatched", MagicMock()),
        patch.object(scheduler, "_watchdog_print_start", AsyncMock()),
    ):
        pm.is_connected.return_value = True
        pm.get_status.side_effect = lambda pid: SimpleNamespace(
            state=(states or {}).get(pid, "IDLE"), subtask_id=None, gcode_file=None
        )
        pm.start_print.return_value = started
        for name in ("on_queue_job_failed", "on_queue_job_started", "on_queue_job_waiting", "on_queue_job_assigned"):
            setattr(notify, name, AsyncMock())
        await scheduler.check_queue()
    return tried, notify


@pytest.fixture
async def queue(db_session, printer_factory, tmp_path):
    """Printer A with three queued items, printer B with one: local library files."""
    a = await printer_factory(name="A1 uno", model="A1")
    b = await printer_factory(name="P1S", model="P1S")
    items = []
    for n, printer in enumerate((a, a, a, b), start=1):
        path = tmp_path / f"part{n}.gcode.3mf"
        path.write_bytes(b"3mf")
        lib = LibraryFile(filename=path.name, file_path=str(path), file_type="3mf", file_size=3)
        db_session.add(lib)
        await db_session.flush()
        item = PrintQueueItem(printer_id=printer.id, library_file_id=lib.id, position=n, status="pending")
        db_session.add(item)
        items.append(item)
    await db_session.commit()
    return SimpleNamespace(a=a, b=b, items=items)


async def statuses(db_session, items):
    for item in items:
        await db_session.refresh(item)
    return [item.status for item in items]


# ---------------------------------------------------------------- refused upload (#3210, #3409)


@pytest.mark.asyncio
async def test_refused_upload_keeps_the_queue_of_that_printer_and_others(db_session, test_engine, queue):
    scheduler = PrintScheduler()
    tried, notify = await run_queue(test_engine, scheduler, refusing={queue.a.id})
    assert tried == [queue.a.id, queue.b.id]  # A's first item, then B's
    first = queue.items[0]
    assert await statuses(db_session, queue.items) == ["pending", "pending", "pending", "printing"]
    assert first.error_message is None and first.dispatching_at is None
    assert first.waiting_reason.startswith("A1 uno did not take the file: ")
    assert ps.UPLOAD_REFUSED_MARK in first.waiting_reason
    assert [i.waiting_reason for i in queue.items[1:3]] == [None, None]  # the others wait behind, untouched
    notify.on_queue_job_failed.assert_not_awaited()
    notify.on_queue_job_waiting.assert_awaited_once()

    # During the backoff A is not tried again, and A's next item is not sent instead.
    tried, notify = await run_queue(test_engine, scheduler, refusing={queue.a.id})
    assert tried == []
    assert await statuses(db_session, queue.items[:3]) == ["pending"] * 3

    # Backoff over, still refusing: the same first item is tried, the wait doubles, no second notification.
    scheduler._upload_backoff[queue.a.id] = 0
    tried, notify = await run_queue(test_engine, scheduler, refusing={queue.a.id})
    assert tried == [queue.a.id]
    assert scheduler._upload_refusals[queue.a.id] == 2
    assert scheduler._upload_backoff[queue.a.id] - time.monotonic() > ps.UPLOAD_REFUSED_BACKOFF_SECONDS
    notify.on_queue_job_waiting.assert_not_awaited()
    assert await statuses(db_session, queue.items[:3]) == ["pending"] * 3

    # The card is back: the first item is sent, its reason goes, the run of refusals is over.
    scheduler._upload_backoff[queue.a.id] = 0
    tried, _ = await run_queue(test_engine, scheduler)
    assert tried == [queue.a.id]
    assert await statuses(db_session, queue.items[:3]) == ["printing", "pending", "pending"]
    assert first.waiting_reason is None
    assert queue.a.id not in scheduler._upload_refusals and not scheduler.printers_refusing_uploads()


def test_backoff_doubles_up_to_an_hour():
    scheduler = PrintScheduler()
    printer = SimpleNamespace(id=7, name="X1C", model="X1C")
    waits = []
    for n in range(6):  # one item each: the same item would fail at its third refusal
        with patch.object(ps, "farm_job_events"), patch.object(ps, "notification_service"):
            item = SimpleNamespace(id=n, waiting_reason=None, dispatching_at=None, target_model=None)
            db = MagicMock(commit=AsyncMock())
            asyncio.run(scheduler._hold_after_refused_upload(db, item, printer, "552", technical=[], job_name="j"))
        waits.append(round(scheduler._upload_backoff[7] - time.monotonic()))
    assert waits == [300, 600, 1200, 2400, 3600, 3600]


@pytest.mark.asyncio
async def test_refused_model_job_goes_back_to_the_pool(db_session, test_engine, printer_factory, tmp_path):
    a = await printer_factory(name="A1 uno", model="A1")
    path = tmp_path / "part.gcode.3mf"
    path.write_bytes(b"3mf")
    lib = LibraryFile(filename=path.name, file_path=str(path), file_type="3mf", file_size=3)
    db_session.add(lib)
    await db_session.flush()
    item = PrintQueueItem(target_model="A1", library_file_id=lib.id, position=1, status="pending")
    db_session.add(item)
    await db_session.commit()

    scheduler = PrintScheduler()
    with patch.object(scheduler, "_find_idle_printer_for_model", AsyncMock(return_value=(a.id, None))):
        tried, _ = await run_queue(test_engine, scheduler, refusing={a.id})
    assert tried == [a.id]
    await db_session.refresh(item)
    # Pending, unassigned, without the slots worked out for A: another A1 can take it.
    assert (item.status, item.printer_id, item.ams_mapping) == ("pending", None, None)
    assert a.id in scheduler.printers_refusing_uploads()
    refused = item.waiting_reason
    assert ps.UPLOAD_REFUSED_MARK in refused

    # No free A1 now: the pool's "Busy" does not hide why the job is waiting.
    with patch.object(scheduler, "_find_idle_printer_for_model", AsyncMock(return_value=(None, "Busy: A1 uno"))):
        await run_queue(test_engine, scheduler, refusing={a.id})
    await db_session.refresh(item)
    assert item.waiting_reason == refused

    # Assigned again after the backoff and refused again: "assigned" is not notified a second time.
    scheduler._upload_backoff.clear()
    with patch.object(scheduler, "_find_idle_printer_for_model", AsyncMock(return_value=(a.id, None))):
        tried, notify = await run_queue(test_engine, scheduler, refusing={a.id})
    assert tried == [a.id]
    notify.on_queue_job_assigned.assert_not_awaited()
    await db_session.refresh(item)
    assert (item.status, item.printer_id) == ("pending", None)


async def refuse(test_engine, scheduler, printer_id, times):
    """``times`` checks in a row with the printer refusing, each after its backoff ended."""
    tried, failed = [], 0
    for _ in range(times):
        scheduler._upload_backoff.pop(printer_id, None)
        t, notify = await run_queue(test_engine, scheduler, refusing={printer_id})
        tried += t
        failed += notify.on_queue_job_failed.await_count
    return tried, failed


@pytest.mark.asyncio
async def test_item_refused_three_times_fails_and_the_printer_goes_on(db_session, test_engine, queue):
    scheduler = PrintScheduler()
    tried, failed = await refuse(test_engine, scheduler, queue.a.id, ps.UPLOAD_REFUSED_MAX_PER_ITEM)
    assert tried == [queue.a.id, queue.b.id, queue.a.id, queue.a.id]  # always A's first item
    assert failed == 1  # notified once, when it failed
    first = queue.items[0]
    assert await statuses(db_session, queue.items) == ["failed", "pending", "pending", "printing"]
    assert first.error_message.startswith("A1 uno refused this file 3 times in a row: ")
    assert (first.failure_detail["phase"], first.waiting_reason) == ("upload", None)
    assert not scheduler.printers_refusing_uploads()  # no backoff after the failure

    # The next check sends A's next item at once.
    tried, _ = await run_queue(test_engine, scheduler)
    assert tried == [queue.a.id]
    assert await statuses(db_session, queue.items[:3]) == ["failed", "printing", "pending"]


@pytest.mark.asyncio
async def test_cancelled_refused_item_ends_the_printer_backoff(db_session, test_engine, queue):
    scheduler = PrintScheduler()
    await run_queue(test_engine, scheduler, refusing={queue.a.id})
    assert queue.a.id in scheduler.printers_refusing_uploads()
    queue.items[0].status = "cancelled"
    await db_session.commit()

    tried, _ = await run_queue(test_engine, scheduler)
    assert tried == [queue.a.id]  # A's next item, without waiting out the backoff
    assert await statuses(db_session, queue.items[:3]) == ["cancelled", "printing", "pending"]
    assert not scheduler._item_refusals


@pytest.mark.asyncio
async def test_start_by_hand_retries_a_held_item_now(async_client, db_session, queue, monkeypatch):
    from backend.app.services.print_scheduler import scheduler

    first = queue.items[0]
    monkeypatch.setitem(scheduler._upload_backoff, queue.a.id, time.monotonic() + 300)
    monkeypatch.setitem(scheduler._backoff_item, queue.a.id, first.id)
    monkeypatch.setitem(scheduler._item_refusals, first.id, 2)
    r = await async_client.post(f"/api/v1/queue/{first.id}/start", params={"skip_filament_check": True})
    assert r.status_code == 200, r.text
    assert queue.a.id not in scheduler.printers_refusing_uploads() and first.id not in scheduler._item_refusals


@pytest.mark.asyncio
async def test_printer_back_online_ends_its_backoff(monkeypatch):
    from backend.app import main
    from backend.app.services.print_scheduler import scheduler

    monkeypatch.setattr(main, "_printer_last_connected", {})
    monkeypatch.setattr(main, "_printer_offline_tasks", {})
    monkeypatch.setattr(main.printer_manager, "offline_expected", lambda pid: True)  # no alert task here
    monkeypatch.setitem(scheduler._upload_backoff, 9, time.monotonic() + 300)
    main._track_printer_offline(9, True)
    main._track_printer_offline(9, True)  # still connected: nothing changes
    assert 9 in scheduler.printers_refusing_uploads()
    main._track_printer_offline(9, False)
    main._track_printer_offline(9, True)  # back after a disconnect
    assert 9 not in scheduler.printers_refusing_uploads()


# ---------------------------------------------------------------- busy printer (#2598)


@pytest.mark.asyncio
async def test_busy_printer_gets_no_upload_and_its_item_stays_queued(db_session, test_engine, queue):
    scheduler = PrintScheduler()
    tried, notify = await run_queue(test_engine, scheduler, states={queue.a.id: "RUNNING"})
    assert tried == [queue.b.id]  # nothing uploaded to (nor deleted from) the busy printer
    assert await statuses(db_session, queue.items) == ["pending", "pending", "pending", "printing"]
    assert queue.items[0].dispatching_at is None
    notify.on_queue_job_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_printer_busy_by_the_start_defers_and_keeps_the_file(db_session, test_engine, queue):
    """Idle at the upload, busy at the start: start_print refuses, the item goes back to pending, not failed."""
    scheduler = PrintScheduler()
    states = {queue.a.id: "IDLE"}

    async def upload(**kwargs):
        states[kwargs["printer_id"]] = "PREPARE"  # it started something while the file was going over
        return True

    async def run():
        sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        with (
            patch.object(ps, "async_session", sessions),
            patch.object(ps, "printer_manager") as pm,
            patch.object(ps, "notification_service") as notify,
            patch.object(ps, "get_ftp_retry_settings", AsyncMock(return_value=(False, 1, 1, 10))),
            patch.object(ps, "delete_file_async", AsyncMock()) as delete_file,
            patch.object(ps.farm_printer_files, "fingerprint", _same_name),
            patch("backend.app.services.archive.ArchiveService.archive_print", AsyncMock(return_value=None)),
            patch.object(scheduler, "_delete_remote_file_for_provider", AsyncMock()),
            patch.object(scheduler, "_upload_file_for_provider", upload),
            patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
            patch.object(scheduler, "_mark_printer_dispatched", MagicMock()),
        ):
            pm.is_connected.return_value = True
            pm.get_status.side_effect = lambda pid: SimpleNamespace(state=states.get(pid), subtask_id=None)
            pm.start_print.return_value = False  # what the MQTT client's busy guard answers
            notify.on_queue_job_failed = AsyncMock()
            async with sessions() as db:
                item = await db.get(PrintQueueItem, queue.items[0].id)
                await scheduler._start_print(db, item)
            return pm, notify, delete_file

    pm, notify, delete_file = await run()
    assert pm.start_print.called
    await db_session.refresh(queue.items[0])
    item = queue.items[0]
    assert (item.status, item.started_at, item.dispatching_at, item.error_message) == ("pending", None, None, None)
    delete_file.assert_not_awaited()  # the printer may be printing that very file
    notify.on_queue_job_failed.assert_not_awaited()


def test_mqtt_client_never_sends_a_start_to_a_busy_printer():
    from backend.app.services.bambu_mqtt import BambuMQTTClient

    client = BambuMQTTClient(ip_address="192.0.2.1", serial_number="TESTSERIAL", access_code="00000000")
    client._client = MagicMock()
    client.state.connected = True
    for busy in ("PREPARE", "SLICING", "RUNNING", "PAUSE"):
        client.state.state = busy
        assert client.start_print("part.3mf") is False
    client._client.publish.assert_not_called()

    for idle in ("IDLE", "FINISH", "FAILED"):
        client.state.state = idle
        client.start_print("part.3mf")
    assert client._client.publish.call_count == 3


# ---------------------------------------------------------------- farm jobs


@pytest.mark.asyncio
async def test_farm_job_on_a_refusing_printer_stays_queued_and_the_other_starts(
    async_client, db_session, test_engine, farm
):
    from backend.app.farm.jobs import service
    from backend.app.farm.jobs.models import FarmPrintJob
    from backend.tests.integration.test_farm_jobs import accoda

    sliced = await db_session.get(LibraryFile, (await db_session.execute(LibraryFile.active())).scalars().first().id)
    sliced.file_path = str(farm["sliced_path"])  # the scheduler reads it from disk
    await db_session.commit()
    jobs = [await accoda(async_client, farm["code"], farm["plates"]) for _ in range(2)]
    assert await service.dispatch_once(db_session) == 2
    a1_one, a1_two, _ = farm["printers"]
    items = {}
    for job in jobs:
        row = await db_session.get(FarmPrintJob, job["id"])
        items[row.printer_id] = await db_session.get(PrintQueueItem, row.queue_item_id)
    assert set(items) == {a1_one, a1_two}

    scheduler = PrintScheduler()
    tried, notify = await run_queue(test_engine, scheduler, refusing={a1_one})
    assert sorted(tried) == sorted([a1_one, a1_two])
    for item in items.values():
        await db_session.refresh(item)
    assert (items[a1_one].status, items[a1_two].status) == ("pending", "printing")
    assert ps.UPLOAD_REFUSED_MARK in items[a1_one].waiting_reason
    states = {j["id"]: j["state"] for j in (await async_client.get("/api/v1/farm/jobs")).json()}
    assert "failed" not in states.values()
    notify.on_queue_job_failed.assert_not_awaited()

    # Refused twice more: the farm job fails like any failed queue item, and only that one.
    _, failed = await refuse(test_engine, scheduler, a1_one, ps.UPLOAD_REFUSED_MAX_PER_ITEM - 1)
    assert failed == 1
    states = {j["id"]: j["state"] for j in (await async_client.get("/api/v1/farm/jobs")).json()}
    by_printer = {(await db_session.get(FarmPrintJob, job["id"])).printer_id: states[job["id"]] for job in jobs}
    assert by_printer[a1_one] == "failed" and by_printer[a1_two] != "failed"
    assert a1_one not in scheduler.printers_refusing_uploads()


@pytest.mark.asyncio
async def test_farm_does_not_place_a_job_on_a_printer_refusing_uploads(async_client, db_session, farm, monkeypatch):
    from backend.app.farm.jobs import service
    from backend.app.farm.jobs.models import FarmPrintJob
    from backend.tests.integration.test_farm_jobs import accoda

    a1_one, a1_two, _ = farm["printers"]
    monkeypatch.setitem(service.scheduler._upload_backoff, a1_one, time.monotonic() + 300)
    job = await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 1
    assert (await db_session.get(FarmPrintJob, job["id"])).printer_id == a1_two


from backend.tests.integration.test_farm_jobs import farm  # noqa: E402, F401  the farm fixture
