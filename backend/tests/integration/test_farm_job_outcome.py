"""Printfarm (fork): a farm job keeps how its print ended when its queue item is deleted, "clear history" says
how many it touches, and the end of a queue print finds its archive again. All data is made up."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from backend.app.farm.jobs import outcome
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.models import FarmLibraryFile  # noqa: F401  (the board's tables in the test database)
from backend.app.farm.plates.models import FarmPlate  # noqa: F401
from backend.app.farm.printer_files import without_fingerprint
from backend.app.farm.products.models import FarmComponent  # noqa: F401
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem

API = "/api/v1/farm"


def utc(**delta) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**delta)


async def queued_job(db_session, printer_id: int, status: str, archive_id: int | None = None, **item):
    queue_item = PrintQueueItem(printer_id=printer_id, position=1, status=status, archive_id=archive_id, **item)
    db_session.add(queue_item)
    await db_session.flush()
    job = FarmPrintJob(
        component_code="C-0071",
        color="Nero",
        plate_ids=[1],
        expected_pieces=2,
        status="queued",
        queue_item_id=queue_item.id,
        printer_id=printer_id,
        printed_pieces=2,
    )
    db_session.add(job)
    await db_session.commit()
    return job, queue_item


@pytest.mark.asyncio
async def test_a_deleted_queue_item_leaves_its_outcome_on_the_job(
    async_client, db_session, printer_factory, archive_factory
):
    printer = await printer_factory(name="P1S", model="P1S")
    archive = await archive_factory(printer.id)
    ended = utc(hours=1)
    job, item = await queued_job(db_session, printer.id, "completed", archive.id, completed_at=ended)
    failed_job, failed_item = await queued_job(
        db_session, printer.id, "failed", completed_at=utc(minutes=5), error_message="Nozzle clog"
    )
    pending_job, pending_item = await queued_job(db_session, printer.id, "pending")
    before = {j["id"] for j in (await async_client.get(f"{API}/jobs?state=completed")).json()}
    assert job.id in before

    # Deleted one by one, as "clear history" does.
    for queue_item in (item, failed_item, pending_item):
        assert (await async_client.delete(f"/api/v1/queue/{queue_item.id}")).status_code == 200

    for j in (job, failed_job, pending_job):
        await db_session.refresh(j)
    assert (job.outcome, job.archive_id, job.printer_id, job.printed_pieces) == ("completed", archive.id, printer.id, 2)
    assert abs((job.finished_at - ended).total_seconds()) < 2
    assert failed_job.outcome == "failed" and pending_job.outcome is None  # a pending one printed nothing

    # Seller Hub still sees the batch, with when it ended; the jobs list says how each ended.
    batches = (await async_client.get(f"{API}/jobs?state=completed")).json()
    batch = next(b for b in batches if b["id"] == job.id)
    assert (batch["state"], batch["archive_id"], batch["pieces"]) == ("completed", archive.id, 2)
    assert batch["finished_at"] is not None
    since = (ended - timedelta(minutes=1)).isoformat()
    assert job.id in {b["id"] for b in (await async_client.get(f"{API}/jobs?state=completed&since={since}")).json()}
    states = {j["id"]: j["state"] for j in (await async_client.get(f"{API}/jobs")).json()}
    assert (states[job.id], states[failed_job.id], states[pending_job.id]) == ("completed", "failed", "removed")

    # The queue board too: a recent failure is still a problem, the finished one is history.
    board = (await async_client.get(f"{API}/board")).json()
    assert any(r["job_id"] == failed_job.id and r["phase"] == "failed" for r in board["problems"])
    assert any(r["job_id"] == job.id and r["phase"] == "finished" for r in board["history"])


@pytest.mark.asyncio
async def test_a_bulk_delete_keeps_the_outcome_too(db_session, printer_factory):
    printer = await printer_factory(name="A1", model="A1")
    job, item = await queued_job(db_session, printer.id, "completed", completed_at=utc(hours=2))
    await outcome.keep_outcomes(db_session, PrintQueueItem.id == item.id)
    await db_session.execute(delete(PrintQueueItem).where(PrintQueueItem.id == item.id))
    await db_session.commit()
    await db_session.refresh(job)
    assert job.outcome == "completed"


@pytest.mark.asyncio
async def test_clear_history_says_how_many_farm_jobs_it_touches(async_client, db_session, printer_factory):
    a1 = await printer_factory(name="A1", model="A1")
    p1s = await printer_factory(name="P1S", model="P1S")
    await queued_job(db_session, a1.id, "completed", completed_at=utc(hours=1))
    await queued_job(db_session, a1.id, "failed", completed_at=utc(hours=1))
    await queued_job(db_session, p1s.id, "completed", completed_at=utc(hours=1))
    await queued_job(db_session, p1s.id, "printing")  # not history
    db_session.add(PrintQueueItem(printer_id=a1.id, position=9, status="completed"))  # not a farm job
    await db_session.commit()
    assert (await async_client.get(f"{API}/jobs/history-links")).json() == {"jobs": 3, "completed": 2}
    assert (await async_client.get(f"{API}/jobs/history-links?printer_id={a1.id}")).json() == {
        "jobs": 2,
        "completed": 1,
    }
    assert (await async_client.get(f"{API}/jobs/history-links?status=failed")).json() == {"jobs": 1, "completed": 0}


@pytest.mark.asyncio
async def test_the_end_of_a_queue_print_finds_its_archive_whatever_name_the_printer_reports(
    db_session, printer_factory, archive_factory
):
    printer = await printer_factory(name="H2D", model="H2D")
    archive = await archive_factory(printer.id, status="printing", filename="Maniglia - plate 2.gcode.3mf")
    await queued_job(db_session, printer.id, "printing", archive.id)
    # The printer reports the name it was sent under, with the file's fingerprint: no name match...
    assert without_fingerprint("H2D-Maniglia_-_plate_2__68c1997a") == "H2D-Maniglia_-_plate_2"
    assert without_fingerprint("Sovol-Zero-plate-1__bfcb55b1.gcode") == "Sovol-Zero-plate-1.gcode"
    assert without_fingerprint("no-fingerprint.gcode.3mf") == "no-fingerprint.gcode.3mf"
    # ...the printing queue item knows its archive.
    assert await outcome.queue_print_archive(db_session, printer.id) == archive.id  # no name reported
    sent = ["Maniglia_-_plate_2__68c1997a", "/data/Metadata/plate_2.gcode"]
    assert await outcome.queue_print_archive(db_session, printer.id, sent) == archive.id
    # A queue item left hanging "printing" never takes a print by hand or outside the queue.
    assert await outcome.queue_print_archive(db_session, printer.id, ["Gancio__1a2b3c4d", "Gancio.gcode"]) is None
    other = await printer_factory(name="P1S", model="P1S")
    assert await outcome.queue_print_archive(db_session, other.id) is None


@pytest.mark.asyncio
async def test_archives_left_printing_are_closed_with_their_queue_item_end(
    db_session, printer_factory, archive_factory
):
    printer = await printer_factory(name="A1", model="A1")
    stuck = await archive_factory(printer.id, status="printing")
    stuck_failed = await archive_factory(printer.id, status="printing")
    still = await archive_factory(printer.id, status="printing")
    ended = utc(hours=3)
    await queued_job(db_session, printer.id, "completed", stuck.id, completed_at=ended)
    await queued_job(db_session, printer.id, "failed", stuck_failed.id, completed_at=ended, error_message="Spaghetti")
    await queued_job(db_session, printer.id, "printing", still.id)
    assert await outcome.close_stale_archives(db_session) == 2
    rows = {
        a.id: a
        for a in (await db_session.execute(select(PrintArchive).execution_options(populate_existing=True))).scalars()
    }
    assert rows[stuck.id].status == "completed" and rows[stuck.id].completed_at is not None
    assert (rows[stuck_failed.id].status, rows[stuck_failed.id].failure_reason) == ("failed", "Spaghetti")
    assert rows[still.id].status == "printing"  # still printing: untouched
    assert await outcome.close_stale_archives(db_session) == 0


@pytest.mark.asyncio
async def test_an_archive_reprinted_now_is_never_closed(db_session, printer_factory, archive_factory):
    printer = await printer_factory(name="A1", model="A1")
    reprinted = await archive_factory(printer.id, status="printing")
    await queued_job(db_session, printer.id, "completed", reprinted.id, completed_at=utc(hours=5))  # its first print
    await queued_job(db_session, printer.id, "printing", reprinted.id)  # printed again from the archive, right now
    assert await outcome.close_stale_archives(db_session) == 0
    await db_session.refresh(reprinted)
    assert reprinted.status == "printing"


@pytest.mark.asyncio
async def test_an_end_without_a_time_is_still_seen_by_a_since_reader(async_client, db_session, printer_factory):
    printer = await printer_factory(name="A1", model="A1")
    since = datetime.now(timezone.utc) - timedelta(minutes=1)
    job, item = await queued_job(db_session, printer.id, "completed")  # completed_at never written
    assert (await async_client.delete(f"/api/v1/queue/{item.id}")).status_code == 200
    batches = (await async_client.get(f"{API}/jobs", params={"state": "completed", "since": since.isoformat()})).json()
    assert job.id in {b["id"] for b in batches}


@pytest.mark.asyncio
async def test_a_job_back_to_waiting_forgets_its_last_end(async_client, db_session, printer_factory):
    from unittest.mock import AsyncMock, patch

    printer = await printer_factory(name="A1", model="A1")
    job, item = await queued_job(db_session, printer.id, "failed", completed_at=utc(hours=1))
    assert (await async_client.delete(f"/api/v1/queue/{item.id}")).status_code == 200
    await db_session.refresh(job)
    assert job.outcome == "failed"
    with patch("backend.app.farm.slicing.ask_variant_slicing", AsyncMock(return_value=True)):
        assert (await async_client.post(f"{API}/board/job:{job.id}/reslice")).status_code == 200
    await db_session.refresh(job)
    assert (job.status, job.outcome, job.finished_at, job.archive_id) == ("waiting", None, None, None)


@pytest.mark.asyncio
async def test_many_items_deleted_at_once_all_keep_their_outcome(db_session, printer_factory):
    printer = await printer_factory(name="A1", model="A1")
    items = [PrintQueueItem(printer_id=printer.id, position=n, status="completed") for n in range(outcome.CHUNK + 20)]
    db_session.add_all(items)
    await db_session.flush()
    db_session.add_all(
        FarmPrintJob(component_code="C-0071", plate_ids=[1], expected_pieces=1, status="queued", queue_item_id=i.id)
        for i in items
    )
    await db_session.commit()
    for item in items:
        await db_session.delete(item)
    await db_session.commit()
    kept = (await db_session.execute(select(FarmPrintJob.outcome))).scalars().all()
    assert len(kept) == outcome.CHUNK + 20 and set(kept) == {"completed"}
