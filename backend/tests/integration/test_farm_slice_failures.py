"""Printfarm (fork): a job waiting for a slice that failed says why, and a sidecar that was down is tried
again as soon as it answers.

The sidecars were refusing connections and two jobs kept saying "is being sliced" for over 30 minutes,
with nothing in the log. No slicer is contacted: the slice and the /health call are stubbed. Data is made up.
"""

import asyncio
import logging
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.farm import slicing
from backend.app.farm.jobs import service as jobs
from backend.app.models.library import LibraryFile
from backend.app.services.slicer_api import SlicerApiService, SlicerApiUnavailableError, SlicerInputError

REFUSED = "Slicer sidecar unreachable: [Errno 111] Connection refused"


def refused() -> SlicerApiUnavailableError:
    """What slicer_api raises when the sidecar refuses the connection."""
    error = SlicerApiUnavailableError(REFUSED)
    error.__cause__ = httpx.ConnectError("[Errno 111] Connection refused")
    return error


@pytest.fixture
async def project(db_session, test_engine, monkeypatch):
    """A 3MF sliced with OrcaSlicer, the slice queue and the sidecar's /health stubbed."""
    from backend.app.core import database
    from backend.app.farm import notify
    from backend.app.farm.models import FarmLibraryFile
    from backend.app.services.slice_dispatch import slice_dispatch

    lib_file = LibraryFile(filename="part.3mf", file_path="/nas/part.3mf", file_type="3mf", file_size=1)
    db_session.add(lib_file)
    await db_session.flush()
    state = FarmLibraryFile(watch_folder_id=1, library_file_id=lib_file.id, path="/nas/part.3mf", mtime=1, size=1)
    state.sha256, state.slicer_detected = "v1", "orcaslicer"
    db_session.add(state)
    await db_session.commit()

    for name in ("_variant_asked", "_failed", "_health", "_versions"):
        monkeypatch.setattr(slicing, name, {})
    monkeypatch.setattr(jobs, "_slicing", {})
    monkeypatch.setattr(database, "async_session", async_sessionmaker(test_engine, class_=AsyncSession))
    monkeypatch.setattr(notify, "slicing_done", lambda *args, **kwargs: None)
    queued = []

    async def enqueue(*, kind, source_id, source_name, run):
        queued.append(run)

    monkeypatch.setattr(slice_dispatch, "enqueue", enqueue)
    health = AsyncMock(side_effect=SlicerApiUnavailableError(REFUSED))
    monkeypatch.setattr(SlicerApiService, "health", health)
    monkeypatch.setattr(SlicerApiService, "close", AsyncMock())
    plate = SimpleNamespace(library_file_id=lib_file.id, plate_index=2)
    return SimpleNamespace(plate=plate, queued=queued, health=health, state=state)


def slice_raises(monkeypatch, status: int, detail: str, cause: Exception) -> None:
    async def failing(*args, **kwargs):
        raise HTTPException(status_code=status, detail=detail) from cause

    monkeypatch.setattr(slicing, "slice_embedded", failing)


async def run_slice(project) -> None:
    """The queued slice job runs (and fails: the slice job carries the error)."""
    from backend.app.services.slice_dispatch import _SliceJobError

    with pytest.raises(_SliceJobError):
        await project.queued[-1](1)


def a_minute_later(key) -> None:
    slicing._failed[key]["at"] -= slicing.EARLY_RETRY_SECONDS


@pytest.mark.asyncio
async def test_unreachable_slicer_is_named_logged_and_tried_again_once_it_answers(
    db_session, project, monkeypatch, caplog
):
    slice_raises(monkeypatch, 502, REFUSED, refused())
    plate = project.plate
    being_sliced = f"plate 2 of file {plate.library_file_id} is being sliced"
    assert await slicing.queue_slice_note(db_session, plate, "Nero") == being_sliced
    with caplog.at_level(logging.WARNING, logger="backend.app.farm.slicing"):
        await run_slice(project)
    assert "Farm slice of part.3mf plate 2 (Nero) on OrcaSlicer failed (slicer not reachable)" in caplog.text
    assert REFUSED in caplog.text

    note = await slicing.queue_slice_note(db_session, plate, "Nero")
    assert "OrcaSlicer not reachable, sliced again as soon as it answers (at the latest at " in note
    assert "being sliced" not in note
    assert len(project.queued) == 1  # not asked again yet

    key = (plate.library_file_id, 2, "nero")
    a_minute_later(key)
    assert "not reachable" in await slicing.queue_slice_note(db_session, plate, "Nero")  # /health still refused
    assert len(project.queued) == 1 and project.health.await_count == 1

    project.health.side_effect, project.health.return_value = None, {"status": "ok"}
    slicing._health.clear()  # the next probe, after the wait that follows a failed one
    assert await slicing.queue_slice_note(db_session, plate, "Nero") == being_sliced
    assert len(project.queued) == 2  # sliced again now, not in 30 minutes


@pytest.mark.asyncio
async def test_a_slicer_error_says_what_and_waits_as_before(db_session, project, monkeypatch, caplog):
    slice_raises(monkeypatch, 400, "Plate 2 has no objects", SlicerInputError("Plate 2 has no objects"))
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, None)  # no colour: the plain slice (jobs.ask_slicing)
    with caplog.at_level(logging.WARNING, logger="backend.app.farm.slicing"):
        await run_slice(project)
    assert "Farm slice of part.3mf plate 2 on OrcaSlicer failed: Plate 2 has no objects" in caplog.text

    a_minute_later((plate.library_file_id, 2, None))
    note = await slicing.queue_slice_note(db_session, plate, None)
    where = f"plate 2 of file {plate.library_file_id}"
    assert re.fullmatch(rf"{where}: slicing failed, trying again at \d\d:\d\d UTC: Plate 2 has no objects", note)
    assert project.health.await_count == 0 and len(project.queued) == 1  # no early retry for a real error


@pytest.mark.asyncio
async def test_a_path_conflict_says_to_space_the_objects_out(db_session, project, monkeypatch, caplog):
    """The CLI refuses a plate whose objects' paths overlap (e.g. tree supports of close copies) and blames the
    wipe tower: the note says what it is; the log keeps the CLI's own words."""
    cli = (
        "Slicer CLI failed (500): Slicing failed with error from slicer: G-code conflicts detected after "
        "slicing. Please make sure the 3mf file can be successfully sliced in the latest Orca Slicer. If the "
        "file slices normally in Orca Slicer, try moving the wipe tower further from other models"
    )
    slice_raises(monkeypatch, 502, cli, SlicerInputError(cli))
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, None)
    with caplog.at_level(logging.WARNING, logger="backend.app.farm.slicing"):
        await run_slice(project)
    assert "G-code conflicts detected" in caplog.text

    a_minute_later((plate.library_file_id, 2, None))
    note = await slicing.queue_slice_note(db_session, plate, None)
    assert "paths of different objects overlap on the plate" in note
    assert "space the objects out in the project" in note
    assert "wipe tower" not in note


@pytest.mark.asyncio
async def test_the_time_survives_a_long_reason_in_the_job_note(db_session, project, monkeypatch):
    long = "Slicer CLI failed (500): " + "x" * 400
    slice_raises(monkeypatch, 502, long, SlicerInputError(long))
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, None)
    await run_slice(project)
    a_minute_later((plate.library_file_id, 2, None))
    note = (await slicing.queue_slice_note(db_session, plate, None))[:255]  # what the job keeps
    assert re.search(r"trying again at \d\d:\d\d UTC: Slicer CLI failed", note)


@pytest.mark.asyncio
async def test_a_new_version_of_the_project_is_sliced_at_once(db_session, project, monkeypatch):
    """The owner fixed the 3MF and saved it: the old failure and its 30-minute wait are of the old version."""
    cli = "Slicer CLI failed (500): G-code conflicts detected after slicing."
    slice_raises(monkeypatch, 502, cli, SlicerInputError(cli))
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, None)
    await run_slice(project)
    a_minute_later((plate.library_file_id, 2, None))
    assert "slicing failed" in await slicing.queue_slice_note(db_session, plate, None)
    assert len(project.queued) == 1  # the same version: no new slice before the wait is over

    project.state.sha256 = "v2"  # the NAS scan saw the file saved again
    await db_session.commit()
    note = await slicing.queue_slice_note(db_session, plate, None)
    assert note == f"plate 2 of file {plate.library_file_id} is being sliced"
    assert len(project.queued) == 2  # sliced again now, on the new version


@pytest.mark.asyncio
async def test_a_slice_that_works_clears_the_failure(db_session, project, monkeypatch):
    slice_raises(monkeypatch, 502, REFUSED, refused())
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, "Nero")
    await run_slice(project)

    async def works(*args, **kwargs):
        return SimpleNamespace(model_dump=lambda: {"library_file_id": 9})

    monkeypatch.setattr(slicing, "slice_embedded", works)
    project.health.side_effect, project.health.return_value = None, {"status": "ok"}
    a_minute_later((plate.library_file_id, 2, "nero"))
    await slicing.queue_slice_note(db_session, plate, "Nero")  # back: asked again
    assert await project.queued[-1](2) == {"library_file_id": 9}
    assert slicing._failed == {}


@pytest.mark.asyncio
async def test_a_slice_that_timed_out_is_not_called_unreachable(db_session, project, monkeypatch, caplog):
    """The sidecar took the request and didn't answer in time: it may still be slicing it, no early retry."""
    timed_out = SlicerApiUnavailableError("Slicer sidecar unreachable: ReadTimeout")
    timed_out.__cause__ = httpx.ReadTimeout("timed out")
    slice_raises(monkeypatch, 502, "Slicer sidecar unreachable: ReadTimeout", timed_out)
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, "Nero")
    with caplog.at_level(logging.WARNING, logger="backend.app.farm.slicing"):
        await run_slice(project)
    assert "on OrcaSlicer failed (timed out)" in caplog.text

    a_minute_later((plate.library_file_id, 2, "nero"))
    note = await slicing.queue_slice_note(db_session, plate, "Nero")
    assert re.search(r"slicing timed out, trying again at \d\d:\d\d UTC: Slicer sidecar unreachable: ReadTimeout", note)
    assert "not reachable" not in note
    assert project.health.await_count == 0 and len(project.queued) == 1


@pytest.mark.asyncio
async def test_one_short_probe_per_slicer_however_many_jobs_wait(db_session, project, monkeypatch):
    slice_raises(monkeypatch, 502, REFUSED, refused())
    plates = [project.plate, SimpleNamespace(library_file_id=project.plate.library_file_id, plate_index=3)]
    for plate in plates:
        await slicing.queue_slice_note(db_session, plate, "Nero")
        await run_slice(project)
        a_minute_later((plate.library_file_id, plate.plate_index, "nero"))

    for plate in plates:  # one dispatcher tick, two jobs on the same sidecar
        assert "not reachable" in await slicing.queue_slice_note(db_session, plate, "Nero")
    assert project.health.await_count == 1
    for plate in plates:  # the next ticks: after a failed probe the next one waits a minute, then more
        await slicing.queue_slice_note(db_session, plate, "Nero")
    assert project.health.await_count == 1


@pytest.mark.asyncio
async def test_a_probe_that_hangs_is_cut_short(db_session, project, monkeypatch):
    """A host that doesn't answer at all costs the dispatcher the short probe timeout, not the client's 10 s."""
    slice_raises(monkeypatch, 502, REFUSED, refused())
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, "Nero")
    await run_slice(project)
    a_minute_later((plate.library_file_id, 2, "nero"))

    async def hangs():
        await asyncio.sleep(5)

    monkeypatch.setattr(SlicerApiService, "health", lambda self: hangs())
    monkeypatch.setattr(slicing, "PROBE_TIMEOUT_SECONDS", 0.05)
    note = await asyncio.wait_for(slicing.queue_slice_note(db_session, plate, "Nero"), 1)
    assert "not reachable" in note


@pytest.mark.asyncio
async def test_a_slice_found_another_way_clears_the_failure(db_session, project, monkeypatch):
    """Re-sliced automatically or by hand: the job no longer says the slice failed."""
    from backend.app.farm.plates import service as plates

    slice_raises(monkeypatch, 400, "Plate 2 has no objects", SlicerInputError("Plate 2 has no objects"))
    plate = project.plate
    await slicing.queue_slice_note(db_session, plate, None)
    await run_slice(project)
    assert slicing._failed

    async def sliced(db, lib_file, sha256, colour=None):
        return {2: SimpleNamespace(id=9)}

    monkeypatch.setattr(plates, "_latest_slices", sliced)
    assert (await slicing.variant_slice(db_session, plate, None)).id == 9
    assert slicing._failed == {}
