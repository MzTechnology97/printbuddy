"""Printfarm (fork): the queue page's Jobs board and the per-job event log. No printer or slicer is contacted."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from backend.app.farm import job_events
from backend.app.farm.job_events import FarmJobEvent
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.models import FarmLibraryFile  # noqa: F401  (its table in the test database)
from backend.app.farm.plates.models import FarmPlate
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def events_in_test_db(db_session):
    job_events._pending.clear()
    with patch("backend.app.core.database.async_session", lambda: _Session(db_session)):
        yield
    job_events._pending.clear()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- the event log


@pytest.mark.asyncio
async def test_events_are_written_in_the_background_and_never_block(db_session):
    job_events.record("created", "C-0001: 4 pieces", job_id=1, detail={"error": "x" * 9000, "empty": None})
    assert await job_events.flush() == 1
    event = (await db_session.execute(select(FarmJobEvent))).scalar_one()
    assert (event.phase, event.level, event.job_id) == ("created", "info", 1)
    assert len(event.detail["error"]) == job_events.TEXT_MAX + 1 and "empty" not in event.detail

    # The database can't be written: nothing raises, the event waits for the next try.
    with patch("backend.app.core.database.async_session", side_effect=RuntimeError("database is locked")):
        job_events.record("upload_failed", "FTP 550", queue_item_id=7)
        assert await job_events.flush() == 0
    assert len(job_events._pending) == 1
    assert await job_events.flush() == 1
    written = (await db_session.execute(select(FarmJobEvent).where(FarmJobEvent.queue_item_id == 7))).scalar_one()
    assert written.level == "error"


@pytest.mark.asyncio
async def test_a_queue_item_event_finds_its_job(db_session):
    item = PrintQueueItem(position=1, status="pending")
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        FarmPrintJob(component_code="C-0001", plate_ids=[], expected_pieces=4, status="queued", queue_item_id=item.id)
    )
    await db_session.commit()
    job_events.record("upload_started", "Sending", queue_item_id=item.id)
    await job_events.flush()
    event = (await db_session.execute(select(FarmJobEvent))).scalar_one()
    job = (await db_session.execute(select(FarmPrintJob))).scalar_one()
    assert event.job_id == job.id


@pytest.mark.asyncio
async def test_old_events_go_with_the_archive_days(db_session):
    db_session.add_all(
        [
            FarmJobEvent(at=now() - timedelta(days=100), phase="created", message="old"),
            FarmJobEvent(at=now() - timedelta(days=10), phase="created", message="recent"),
        ]
    )
    await db_session.commit()
    assert await job_events.cleanup(db_session) == 1
    assert [e.message for e in (await db_session.execute(select(FarmJobEvent))).scalars()] == ["recent"]


# ---------------------------------------------------------------- the board


async def make(db_session, printer_factory):
    printer = await printer_factory(name="A1 uno", model="A1")
    project = LibraryFile(filename="Maniglia.3mf", file_path="/nas/Maniglia.3mf", file_type="3mf", file_size=10)
    sliced = LibraryFile(
        filename="Maniglia.gcode.3mf", file_path="/x/Maniglia.gcode.3mf", file_type="3mf", file_size=10
    )
    db_session.add_all([project, sliced])
    await db_session.flush()
    plate = FarmPlate(library_file_id=project.id, plate_index=2, status="confirmed")
    db_session.add(plate)
    await db_session.flush()

    def job(**kw):
        return FarmPrintJob(component_code="C-0085", color="Nero", plate_ids=[plate.id], expected_pieces=8, **kw)

    printing = PrintQueueItem(printer_id=printer.id, library_file_id=sliced.id, position=1, status="printing")
    failed = PrintQueueItem(
        printer_id=printer.id,
        position=2,
        status="failed",
        error_message="FTP 550",
        failure_detail={"phase": "upload", "code": "ftp"},
        completed_at=now() - timedelta(hours=1),
    )
    old_failed = PrintQueueItem(
        printer_id=printer.id, position=3, status="failed", completed_at=now() - timedelta(days=3)
    )
    plain = PrintQueueItem(
        printer_id=printer.id, library_file_id=sliced.id, position=4, status="pending", manual_start=True
    )
    done = PrintQueueItem(
        printer_id=printer.id, position=5, status="completed", completed_at=now() - timedelta(hours=2)
    )
    db_session.add_all([printing, failed, old_failed, plain, done])
    await db_session.flush()
    jobs = {
        "slice_failed": job(
            status="waiting", note="plate 2 of file 1: slicing failed (empty plate), trying again at 21:30 UTC"
        ),
        "waiting": job(status="waiting", note="No free printer of a matching model"),
        "printing": job(status="queued", queue_item_id=printing.id, printer_id=printer.id),
        "failed": job(status="queued", queue_item_id=failed.id, printer_id=printer.id),
        "assign": FarmPrintJob(
            component_code="",
            plate_ids=[],
            expected_pieces=0,
            status="to_assign",
            source="bambu",
            outside_name="Regalo.3mf",
            printer_id=printer.id,
        ),
    }
    db_session.add_all(jobs.values())
    await db_session.commit()
    return printer, plate, jobs, {"plain": plain, "old_failed": old_failed, "done": done, "failed": failed}


@pytest.mark.asyncio
async def test_one_row_per_print_in_its_group(async_client, db_session, printer_factory):
    printer, _, jobs, items = await make(db_session, printer_factory)
    board = (await async_client.get("/api/v1/farm/board")).json()
    keys = {g: [r["key"] for r in board[g]] for g in ("problems", "printing", "waiting", "history")}
    assert set(keys["problems"]) == {
        f"job:{jobs['slice_failed'].id}",
        f"job:{jobs['failed'].id}",
        f"job:{jobs['assign'].id}",
    }
    assert keys["printing"] == [f"job:{jobs['printing'].id}"]
    assert set(keys["waiting"]) == {f"job:{jobs['waiting'].id}", f"item:{items['plain'].id}"}
    assert set(keys["history"]) == {f"item:{items['old_failed'].id}", f"item:{items['done'].id}"}
    row = next(r for r in board["problems"] if r["job_id"] == jobs["slice_failed"].id)
    assert (row["phase"], row["actions"][:2]) == ("slice_failed", ["retry_slice", "reslice"])
    assert next(r for r in board["problems"] if r["job_id"] == jobs["failed"].id)["failure_phase"] == "upload"
    plain = next(r for r in board["waiting"] if r["queue_item_id"] == items["plain"].id)
    assert "start" in plain["actions"] and plain["source"] == "queue" and plain["file"] == "Maniglia.gcode.3mf"
    # Filtered by printer, and the history paged.
    assert (await async_client.get(f"/api/v1/farm/board?printer_id={printer.id + 1}")).json()["printing"] == []
    page = (await async_client.get("/api/v1/farm/board?history_limit=1")).json()
    assert (len(page["history"]), page["history_total"]) == (1, 2)


@pytest.mark.asyncio
async def test_an_open_row_has_its_timeline_details_and_log(
    async_client, db_session, printer_factory, tmp_path, monkeypatch
):
    _, plate, jobs, _ = await make(db_session, printer_factory)
    job = jobs["failed"]
    job.created_at = now() - timedelta(hours=3)
    await db_session.commit()
    job_events.record("created", "C-0085: 8 pieces", job_id=job.id)
    job_events.record(
        "slice_failed",
        "OrcaSlicer failed",
        file_id=plate.library_file_id,
        plate=2,
        colour="nero",
        detail={"slicer": "orcaslicer", "error": "Plate 2 has no objects"},
    )
    job_events.record("slice_done", "Another colour", file_id=plate.library_file_id, plate=2, colour="rosso")
    job_events.record("upload_failed", "FTP 550", queue_item_id=job.queue_item_id, detail={"log": ["ERROR: 550"]})
    log = tmp_path / "printbuddy.log"
    log.write_text(
        f"10:00 INFO Queue item {job.queue_item_id}: uploading\n"
        "10:00 INFO Queue item 999: someone else\n"
        f"10:01 ERROR Queue item {job.queue_item_id}: access_code=12345678 refused\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.app.farm.board.settings.log_dir", tmp_path)
    got = (await async_client.get(f"/api/v1/farm/board/job:{job.id}")).json()
    assert [e["phase"] for e in got["events"]] == ["created", "slice_failed", "upload_failed"]
    assert got["events"][1]["detail"]["error"] == "Plate 2 has no objects"
    assert got["technical"]["job"]["component"] == "C-0085"
    assert got["technical"]["queue_item"]["error_message"] == "FTP 550"
    assert got["technical"]["plate"]["plate"] == 2
    assert len(got["log"]) == 2 and "12345678" not in got["log"][1] and "access_code=***" in got["log"][1]
    assert (await async_client.get("/api/v1/farm/board/job:99999")).status_code == 404


# ---------------------------------------------------------------- actions


@pytest.mark.asyncio
async def test_requeue_and_reslice(async_client, db_session, printer_factory):
    _, _, jobs, _ = await make(db_session, printer_factory)
    asked = AsyncMock(return_value=True)
    with (
        patch("backend.app.farm.slicing.ask_variant_slicing", asked),
        patch("backend.app.farm.slicing.variant_slice", AsyncMock(return_value=None)),
    ):
        # A print that failed goes back to waiting, the dispatcher places it again.
        assert (await async_client.post(f"/api/v1/farm/board/job:{jobs['failed'].id}/requeue")).status_code == 200
        await db_session.refresh(jobs["failed"])
        assert (jobs["failed"].status, jobs["failed"].queue_item_id) == ("waiting", None)
        # Only a failed or cancelled one.
        assert (await async_client.post(f"/api/v1/farm/board/job:{jobs['waiting'].id}/requeue")).status_code == 409
        # A slice failed: asked again now.
        r = await async_client.post(f"/api/v1/farm/board/job:{jobs['slice_failed'].id}/retry-slice")
        assert (r.status_code, r.json()) == (200, {"asked": 1})
        # Printing: no new slice under it.
        assert (await async_client.post(f"/api/v1/farm/board/job:{jobs['printing'].id}/reslice")).status_code == 409
        # Forced even when a slice exists; a plain retry then has nothing to ask.
        with patch("backend.app.farm.slicing.variant_slice", AsyncMock(return_value=object())):
            key = f"job:{jobs['waiting'].id}"
            assert (await async_client.post(f"/api/v1/farm/board/{key}/reslice")).json() == {"asked": 1}
            assert (await async_client.post(f"/api/v1/farm/board/{key}/retry-slice")).json() == {"asked": 0}
    await job_events.flush()
    actions = (await db_session.execute(select(FarmJobEvent).where(FarmJobEvent.phase == "action"))).scalars().all()
    assert len(actions) == 4
