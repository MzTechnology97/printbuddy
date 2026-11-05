"""Printfarm (fork): a job waiting for a slice that failed says why, and a sidecar that was down is tried
again as soon as it answers.

The sidecars were refusing connections and two jobs kept saying "is being sliced" for over 30 minutes,
with nothing in the log. No slicer is contacted: the slice and the /health call are stubbed. Data is made up.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.farm import slicing
from backend.app.farm.jobs import service as jobs
from backend.app.models.library import LibraryFile
from backend.app.services.slicer_api import SlicerApiService, SlicerApiUnavailableError, SlicerInputError

REFUSED = "Slicer sidecar unreachable: [Errno 111] Connection refused"


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

    for name in ("_variant_asked", "_failed", "_health"):
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
    return SimpleNamespace(plate=plate, queued=queued, health=health)


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
    slice_raises(monkeypatch, 502, REFUSED, SlicerApiUnavailableError(REFUSED))
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
    slicing._health.clear()  # the next dispatcher tick
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
    assert note.startswith(f"{where}: slicing failed (Plate 2 has no objects), trying again at ")
    assert note.endswith(" UTC")
    assert project.health.await_count == 0 and len(project.queued) == 1  # no early retry for a real error


@pytest.mark.asyncio
async def test_a_slice_that_works_clears_the_failure(db_session, project, monkeypatch):
    slice_raises(monkeypatch, 502, REFUSED, SlicerApiUnavailableError(REFUSED))
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
