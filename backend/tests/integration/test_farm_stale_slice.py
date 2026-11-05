"""Printfarm (fork): a queued farm print starts only from a slice of the current version of its 3MF.

The owner saved 3MFs again after queueing and the printers got the old slice: a K1 and an SV08
printed the G-code extracted from the output before it was re-sliced. No printer, NAS or slicer is
contacted: the queue check runs on the test database with the start itself stubbed. Data is made up.
"""

import hashlib
import zipfile
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.farm.jobs import service
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem


@pytest.fixture
async def queued(db_session, tmp_path, printer_factory, monkeypatch):
    """Project at version v1, its output sliced from v1 and a queue item for it on printer A."""
    from backend.app.api.routes import library as lib
    from backend.app.farm import reslice
    from backend.app.farm.models import FarmLibraryFile

    monkeypatch.setattr(lib, "get_library_files_dir", lambda: tmp_path)
    monkeypatch.setattr(service, "_reslice_asked", {})
    monkeypatch.setattr(reslice, "_running", set())
    asked = []

    async def fake_schedule(db, project_ids):
        asked.append(project_ids)
        reslice._running.update(project_ids)  # under way, as the real job is until it ends

    monkeypatch.setattr(reslice, "schedule", fake_schedule)
    src = tmp_path / "part.gcode.3mf"  # the local copy of the output (the NAS cache)

    async def fake_local(db, lib_file):
        return src

    monkeypatch.setattr(service, "_local_path", fake_local)

    def slice_to(version: str, text: str) -> None:
        """A (re-)slice writes the output in place, from that project version, and its local copy."""
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("Metadata/plate_1.gcode", text)
        output.file_hash = hashlib.sha256(src.read_bytes()).hexdigest()
        output.file_metadata = {
            "used_embedded_settings": True,
            "sliced_from_library_file_id": project.id,
            "sliced_from_sha256": version,
            "sliced_plate": None,
        }

    project = LibraryFile(filename="part.3mf", file_path="/nas/part.3mf", file_type="3mf", file_size=1)
    db_session.add(project)
    await db_session.flush()
    state = FarmLibraryFile(watch_folder_id=1, library_file_id=project.id, path="/nas/part.3mf", mtime=1, size=1)
    state.sha256 = "v1"
    output = LibraryFile(filename="part.gcode.3mf", file_path=str(src), file_type="gcode", file_size=1)
    output.source_type = "sliced"
    slice_to("v1", "; first")
    printer_a = await printer_factory(name="A", model="A1")
    db_session.add_all([state, output])
    await db_session.flush()
    item = PrintQueueItem(printer_id=printer_a.id, library_file_id=output.id, position=1, status="pending")
    db_session.add(item)
    await db_session.commit()

    class Q:
        pass

    q = Q()
    q.project, q.state, q.output, q.item, q.printer_a = project, state, output, item, printer_a
    q.asked, q.slice_to, q.src, q.running = asked, slice_to, src, reslice._running
    return q


async def klipper_item(db_session, q):
    """Point the queue item at the G-code extracted from the output, as for a Klipper printer."""
    gcode = await service.gcode_for_klipper(db_session, q.output, q.src, 1)
    q.item.library_file_id = gcode.id
    await db_session.commit()
    return gcode


async def plain_item(db_session, printer, position, **kwargs):
    """A queued file that isn't a farm output (uploaded or sliced by hand)."""
    lib_file = LibraryFile(
        filename=f"plain{position}.gcode.3mf", file_path="/x/plain.3mf", file_type="3mf", file_size=1
    )
    db_session.add(lib_file)
    await db_session.flush()
    item = PrintQueueItem(
        printer_id=printer, library_file_id=lib_file.id, position=position, status="pending", **kwargs
    )
    db_session.add(item)
    await db_session.commit()
    return item


async def queue_check(test_engine, find_printer=None) -> list[tuple[int, int]]:
    """One real ``check_queue`` (printers idle and connected): the (item, library file) pairs started."""
    from backend.app.services.print_scheduler import PrintScheduler

    scheduler = PrintScheduler()
    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch("backend.app.services.print_scheduler.async_session", sessions),
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch.object(scheduler, "_get_bool_setting", AsyncMock(return_value=False)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(return_value=True)),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=None)),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_find_idle_printer_for_model", AsyncMock(return_value=(find_printer, None))),
        patch.object(scheduler, "_start_print", AsyncMock()) as start,
    ):
        pm.is_connected.return_value = True
        await scheduler.check_queue()
    return [(c.args[1].id, c.args[1].library_file_id) for c in start.await_args_list]


async def reread(db_session, item: PrintQueueItem) -> PrintQueueItem:
    await db_session.refresh(item)  # as the queue check left it
    return item


# ---------------------------------------------------------------- the check


@pytest.mark.asyncio
async def test_a_3mf_changed_after_queueing_waits_for_its_new_slice(db_session, queued):
    q = queued
    assert await service.stale_slice_reason(db_session, q.item) is None  # sliced from the current version

    q.state.sha256 = "v2"  # the scan saw the new version
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NEW_SLICE
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NEW_SLICE
    assert q.asked == [[q.project.id]]  # asked once, then it is under way

    q.running.clear()  # the re-slice ended without replacing the output: failed
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_RESLICE_FAILED
    assert q.asked == [[q.project.id]]  # tried again only after a while

    q.slice_to("v2", "; second")  # a slice of the new version replaced the output in place
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) is None

    q.state.status = "missing"  # gone from the NAS for now
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NO_SOURCE


@pytest.mark.asyncio
async def test_what_can_never_start_fails_instead_of_waiting(db_session, queued):
    from backend.app.farm.plates.models import FarmPlate

    q = queued
    q.state.sha256 = "v2"
    db_session.add(FarmPlate(library_file_id=q.project.id, plate_index=2, source_sha256="v2"))
    await db_session.commit()
    # Version v2 has only plate 2: plate 1 of this output is never re-sliced.
    assert await service.stale_slice_reason(db_session, q.item) == service.FAIL_PLATE_GONE
    assert q.asked == []

    q.project.deleted_at = datetime(2026, 11, 4)
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) == service.FAIL_NO_PROJECT


@pytest.mark.asyncio
async def test_klipper_item_gets_the_gcode_of_the_new_slice(db_session, queued):
    from backend.app.api.routes import library as lib

    q = queued
    gcode = await klipper_item(db_session, q)
    q.state.sha256 = "v2"  # saved again: the extracted G-code is from v1 and must wait
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NEW_SLICE

    q.slice_to("v2", "; second")
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) is None
    assert q.item.library_file_id == gcode.id  # same row, new bytes
    await db_session.refresh(gcode)
    assert lib.to_absolute_path(gcode.file_path).read_text() == "; second"
    assert gcode.file_metadata["sliced_from_sha256"] == "v2"


@pytest.mark.asyncio
async def test_an_old_local_copy_of_the_new_slice_is_never_extracted(db_session, queued):
    """The NAS didn't answer the fetch of the new output: the cache still holds the v1 slice."""
    from backend.app.api.routes import library as lib

    q = queued
    gcode = await klipper_item(db_session, q)
    q.state.sha256 = "v2"
    q.output.file_hash = "f" * 64  # re-sliced on the NAS; the local copy wasn't refreshed
    q.output.file_metadata = {**q.output.file_metadata, "sliced_from_sha256": "v2"}
    await db_session.commit()

    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_SLICE_UNREADABLE
    await db_session.refresh(gcode)
    assert gcode.file_metadata["extracted_from_hash"] != "f" * 64  # not marked as the new slice
    assert lib.to_absolute_path(gcode.file_path).read_text() == "; first"

    q.slice_to("v2", "; second")  # fetched at last
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) is None
    await db_session.refresh(gcode)
    assert lib.to_absolute_path(gcode.file_path).read_text() == "; second"


@pytest.mark.asyncio
async def test_outputs_of_the_upstream_slice_route_are_left_alone(db_session, queued):
    """The library's own slice route marks embedded settings and the source, not its version."""
    q = queued
    q.output.file_metadata = {"used_embedded_settings": True, "sliced_from_library_file_id": q.project.id}
    q.state.sha256 = "v2"
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) is None
    assert q.asked == []  # the user's output is never re-sliced over


@pytest.mark.asyncio
async def test_a_3mf_saved_since_the_last_scan_waits_for_the_scan(db_session, queued, monkeypatch):
    """Saved minutes before the start, before the scan saw it: one stat on the NAS holds the print."""
    from backend.app.farm.models import FarmWatchedFolder
    from backend.app.farm.nas import access, service as nas_service
    from backend.app.farm.nas.remote import NasConnectionError, RemoteEntry

    q = queued
    watch = FarmWatchedFolder(folder_id=1, scan_interval_seconds=300, last_scan_at=datetime(2026, 1, 1))
    db_session.add(watch)
    await db_session.commit()

    on_nas = {"size": 1, "mtime": 1.0, "error": None}
    stats = []

    class FakeRemote:
        def stat(self, parts):
            stats.append(parts)
            if on_nas["error"]:
                raise on_nas["error"]
            return RemoteEntry(parts[-1], False, on_nas["size"], on_nas["mtime"])

    class FakeActive:
        remote = FakeRemote()

        def parts(self, path):
            return ("Stampa 3D", "part.3mf") if path == "/nas/part.3mf" else None

    async def fake_access(db):
        return FakeActive()

    async def run_here(fn, *args):
        return fn(*args)

    monkeypatch.setattr(nas_service, "refresh_access", fake_access)
    monkeypatch.setattr(access, "run", run_here)

    assert await service.stale_slice_reason(db_session, q.item) is None  # same size and mtime as scanned
    assert stats == [("Stampa 3D", "part.3mf")]  # a stat, nothing read

    on_nas["mtime"] = 500.0  # saved again since the scan
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NAS_CHANGED
    await db_session.refresh(watch)
    assert watch.last_scan_at is None  # the scan runs at the next tick, with its 60 s settle rule
    assert q.asked == []  # no slice of a version not read yet

    on_nas["error"] = NasConnectionError("no answer")  # the NAS is down: the known version stands
    assert await service.stale_slice_reason(db_session, q.item) is None
    on_nas["error"] = FileNotFoundError("part.3mf")  # the NAS answered: gone, read it again
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NAS_CHANGED

    on_nas.update(error=None, size=2, mtime=500.0)  # the scan read the new version...
    q.state.size, q.state.mtime, q.state.sha256 = 2, 500.0, "v2"
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NEW_SLICE  # ...it is re-sliced


@pytest.mark.asyncio
async def test_files_that_are_not_farm_outputs_start_as_before(db_session, printer_factory):
    printer = await printer_factory(name="Farm", model="A1")
    item = await plain_item(db_session, printer.id, 1)
    assert await service.stale_slice_reason(db_session, item) is None


# ---------------------------------------------------------------- the real queue check


@pytest.mark.asyncio
async def test_queue_check_holds_only_the_printer_whose_slice_is_old(db_session, test_engine, queued, printer_factory):
    q = queued
    next_on_a = await plain_item(db_session, q.printer_a.id, 2)
    printer_b = await printer_factory(name="B", model="A1")  # a higher id: checked after A
    on_b = await plain_item(db_session, printer_b.id, 3)
    q.state.sha256 = "v2"
    await db_session.commit()

    started = await queue_check(test_engine)
    assert started == [(on_b.id, on_b.library_file_id)]  # B prints; A's next item keeps its turn
    assert (await reread(db_session, q.item)).waiting_reason == service.WAIT_NEW_SLICE
    assert (await reread(db_session, next_on_a)).status == "pending"


@pytest.mark.asyncio
async def test_queue_check_sends_the_gcode_extracted_from_the_new_slice(db_session, test_engine, queued):
    from backend.app.api.routes import library as lib

    q = queued
    gcode = await klipper_item(db_session, q)
    q.state.sha256 = "v2"
    await db_session.commit()
    assert await queue_check(test_engine) == []

    q.slice_to("v2", "; second")  # the re-slice replaced the output
    await db_session.commit()
    assert await queue_check(test_engine) == [(q.item.id, gcode.id)]
    assert (await reread(db_session, q.item)).waiting_reason is None
    await db_session.refresh(gcode)
    assert lib.to_absolute_path(gcode.file_path).read_text() == "; second"


@pytest.mark.asyncio
async def test_queue_check_goes_on_when_the_slice_check_breaks(
    db_session, test_engine, queued, printer_factory, monkeypatch
):
    q = queued
    printer_b = await printer_factory(name="B", model="A1")
    on_b = await plain_item(db_session, printer_b.id, 2)

    async def broken(db, item):
        if item.id == q.item.id:
            raise RuntimeError("boom")
        return None

    monkeypatch.setattr(service, "stale_slice_reason", broken)
    assert await queue_check(test_engine) == [(on_b.id, on_b.library_file_id)]
    assert (await reread(db_session, q.item)).waiting_reason == service.WAIT_CHECK_FAILED


@pytest.mark.asyncio
async def test_queue_check_fails_an_item_that_can_never_start(db_session, test_engine, queued):
    from backend.app.farm.plates.models import FarmPlate

    q = queued
    next_on_a = await plain_item(db_session, q.printer_a.id, 2)
    q.state.sha256 = "v2"
    db_session.add(FarmPlate(library_file_id=q.project.id, plate_index=2, source_sha256="v2"))
    await db_session.commit()

    assert await queue_check(test_engine) == [(next_on_a.id, next_on_a.library_file_id)]  # not stuck behind it
    failed = await reread(db_session, q.item)
    assert (failed.status, failed.error_message) == ("failed", service.FAIL_PLATE_GONE)


@pytest.mark.asyncio
async def test_queue_check_leaves_upstream_slices_alone(db_session, test_engine, queued):
    q = queued
    q.output.file_metadata = {"used_embedded_settings": True, "sliced_from_library_file_id": q.project.id}
    q.state.sha256 = "v2"
    await db_session.commit()
    assert await queue_check(test_engine) == [(q.item.id, q.output.id)]


@pytest.mark.asyncio
async def test_model_queue_checks_before_assigning_a_printer(db_session, test_engine, queued):
    q = queued
    q.item.printer_id, q.item.target_model = None, "A1"
    q.state.sha256 = "v2"
    await db_session.commit()

    assert await queue_check(test_engine, find_printer=q.printer_a.id) == []
    item = await reread(db_session, q.item)
    assert (item.printer_id, item.waiting_reason) == (None, service.WAIT_NEW_SLICE)

    q.slice_to("v2", "; second")
    await db_session.commit()
    assert await queue_check(test_engine, find_printer=q.printer_a.id) == [(q.item.id, q.output.id)]


@pytest.mark.asyncio
async def test_a_kept_slice_moves_to_the_new_one_only_with_the_same_pieces(db_session, queued, tmp_path, monkeypatch):
    """The queued output was kept and v2 sliced into a row of its own (``slicing.slice_embedded``)."""
    from pathlib import Path

    from backend.app.farm.jobs.models import FarmPrintJob
    from backend.app.farm.plates.models import FarmPlate, FarmPlateYield

    async def own_path(db, lib_file):
        return Path(lib_file.file_path)

    monkeypatch.setattr(service, "_local_path", own_path)
    q = queued
    plate = FarmPlate(library_file_id=q.project.id, plate_index=1, source_sha256="v1")
    plate.yields = [FarmPlateYield(object_name="part", quantity=4)]
    db_session.add(plate)
    await db_session.flush()
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()  # noqa: E731
    job = FarmPrintJob(component_code="C-0001", plate_ids=[plate.id], expected_pieces=4, status="queued")
    job.queue_item_id, job.plate_id, job.pieces = q.item.id, plate.id, 4
    job.queued_as = {"objects": {"part": 4}, "file_hash": sha(q.src)}
    new_src = tmp_path / "part - 1a2b3c4d.gcode.3mf"
    with zipfile.ZipFile(new_src, "w") as zf:
        zf.writestr("Metadata/plate_1.gcode", "; second")
    newer = LibraryFile(filename=new_src.name, file_path=str(new_src), file_type="gcode", file_size=1)
    newer.source_type, newer.file_hash = "sliced", sha(new_src)
    newer.file_metadata = {**q.output.file_metadata, "sliced_from_sha256": "v2"}
    q.state.sha256 = "v2"
    db_session.add_all([job, newer])
    await db_session.commit()

    assert await service.stale_slice_reason(db_session, q.item) == service.WAIT_NEW_SLICE  # v2's plates not read
    plate.source_sha256 = "v2"
    plate.yields[0].quantity = 6  # v2 prints other pieces on plate 1
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) == service.FAIL_PLATE_CHANGED
    assert q.item.library_file_id == q.output.id

    plate.yields[0].quantity = 4  # the same pieces, a better slice
    await db_session.commit()
    assert await service.stale_slice_reason(db_session, q.item) is None
    await db_session.refresh(job)
    assert (q.item.library_file_id, job.queued_as["file_hash"]) == (newer.id, sha(new_src))
    assert await service.stale_slice_reason(db_session, q.item) is None  # its new file checks out
