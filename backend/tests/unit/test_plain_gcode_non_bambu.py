"""A .gcode.3mf printed on a non-Bambu printer outside the farm goes as the plate's plain G-code.

Library print, archive reprint and print queue upload ``Metadata/plate_N.gcode`` as ``<name>.gcode``
(``<name>_plate_<N>.gcode`` for several plates), with the preview and layer info of ``for_klipper``,
from a temporary file removed after the upload. Bambu still gets the 3MF. Ported from upstream
Printbuddy 42f008372. No printer is contacted; data is made up.
"""

from __future__ import annotations

import io
import zipfile
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.farm.klipper_gcode import NoPlateGcode, plain_gcode, plain_gcode_name
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services import print_scheduler as ps
from backend.app.services.background_dispatch import BackgroundDispatchService, PrintDispatchJob
from backend.app.services.print_scheduler import PrintScheduler

BD = "backend.app.services.background_dispatch"
PLATE_2 = b"; HEADER_BLOCK_START\n; HEADER_BLOCK_END\nG28\n;LAYER_CHANGE\nG1 X1\n;LAYER_CHANGE\nG1 X2\n"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (64, 48), (247, 84, 3, 255)).save(buf, "PNG")
    return buf.getvalue()


def _make_3mf(path: Path, plates: dict[int, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        for number, gcode in plates.items():
            zf.writestr(f"Metadata/plate_{number}.gcode", gcode)
            zf.writestr(f"Metadata/plate_{number}.png", _png())
    return path


def _assert_klipper_plate_2(data: bytes) -> None:
    assert b"; thumbnail begin 96x96 " in data and b"; thumbnail begin 300x300 " in data
    assert b"SET_PRINT_STATS_INFO TOTAL_LAYER=2" in data and b"SET_PRINT_STATS_INFO CURRENT_LAYER=2" in data
    assert data.startswith(b"; HEADER_BLOCK_START\n; HEADER_BLOCK_END\n") and data.endswith(b"G1 X2\n")


class RecordingClient:
    """Provider client keeping each upload's path and bytes, read while the file exists."""

    def __init__(self):
        self.uploads: list[tuple[str, bytes, Path]] = []

    def upload_file(self, local_path: Path, remote_path: str) -> bool:
        self.uploads.append((remote_path, Path(local_path).read_bytes(), Path(local_path)))
        return True

    def delete_file(self, remote_path: str) -> bool:
        return True


# ---------------------------------------------------------------- the helper


def test_names_and_plates(tmp_path):
    multi = _make_3mf(tmp_path / "multi.3mf", {1: b"G28\n", 2: PLATE_2})
    single = _make_3mf(tmp_path / "single.3mf", {3: b"G28\n"})
    assert plain_gcode_name(multi, "Bracket.gcode.3mf", "klipper", 2) == ("Bracket_plate_2.gcode", 2)
    assert plain_gcode_name(multi, "Bracket.gcode.3mf", "prusalink", None) == ("Bracket_plate_1.gcode", 1)
    assert plain_gcode_name(single, "Clip.3mf", "octoprint", None) == ("Clip.gcode", 3)
    # Bambu and plain G-code go as they are.
    assert plain_gcode_name(multi, "Bracket.gcode.3mf", "bambu", 2) is None
    assert plain_gcode_name(tmp_path / "x.gcode", "Clip.gcode", "klipper", None) is None


def test_missing_plate_gcode_is_a_clear_error(tmp_path):
    src = _make_3mf(tmp_path / "b.3mf", {1: b"G28\n"})
    with pytest.raises(NoPlateGcode, match=r"Plate 3 of Bracket.gcode.3mf is not sliced \(sliced plates: 1\)"):
        plain_gcode_name(src, "Bracket.gcode.3mf", "klipper", 3)
    project = _make_3mf(tmp_path / "p.3mf", {})
    with pytest.raises(NoPlateGcode, match="contains no sliced G-code"):
        plain_gcode_name(project, "Part.3mf", "klipper", None)
    (tmp_path / "bad.3mf").write_bytes(b"not a zip")
    with pytest.raises(NoPlateGcode, match="not a readable 3MF"):
        plain_gcode_name(tmp_path / "bad.3mf", "bad.3mf", "klipper", None)


@pytest.mark.asyncio
async def test_temp_gcode_for_klipper_only_layers_for_klipper_and_removed_after(tmp_path):
    src = _make_3mf(tmp_path / "b.3mf", {1: b"G28\n", 2: PLATE_2})
    before = src.read_bytes()
    async with plain_gcode(src, ("Bracket_plate_2.gcode", 2), "fluidd") as path:
        assert path.name == "Bracket_plate_2.gcode" and path.parent != src.parent
        _assert_klipper_plate_2(path.read_bytes())
    assert not path.exists() and not path.parent.exists()
    # Other firmwares: the preview (comment lines) only, no Klipper command.
    async with plain_gcode(src, ("Bracket_plate_2.gcode", 2), "prusalink") as path:
        data = path.read_bytes()
    assert b"; thumbnail begin" in data and b"SET_PRINT_STATS_INFO" not in data
    async with plain_gcode(src, None, "bambu") as path:
        assert path == src
    assert src.read_bytes() == before


# ---------------------------------------------------------------- background dispatch


def _session_factory(db):
    @asynccontextmanager
    async def factory():
        yield db

    return factory


def _printer(provider: str):
    return SimpleNamespace(id=10, name="Voron", ip_address="192.0.2.5", access_code="", model="K2", provider=provider)


def _job(kind: str, plate_id: int | None = None) -> PrintDispatchJob:
    return PrintDispatchJob(
        id=1,
        kind=kind,
        source_id=99,
        source_name="Bracket.gcode.3mf",
        printer_id=10,
        printer_name="Voron",
        options={"plate_id": plate_id} if plate_id else {},
    )


def _dispatch_patches(stack: ExitStack, db, client, start_print, archive_service) -> None:
    for target, value in {
        "async_session": _session_factory(db),
        "ArchiveService": MagicMock(return_value=archive_service),
        "printer_manager.is_connected": MagicMock(return_value=True),
        "printer_manager.get_client": MagicMock(return_value=client),
        "printer_manager.get_status": MagicMock(return_value=None),
        "printer_manager.start_print": start_print,
        "get_ftp_retry_settings": AsyncMock(return_value=(False, 0, 0, 30.0)),
        "upload_file_async": AsyncMock(side_effect=AssertionError("Bambu FTP must not be used")),
        "delete_file_async": AsyncMock(),
        "ws_manager.broadcast": AsyncMock(),
        "cache_3mf_download": MagicMock(),
        "farm_printer_files.before_upload": AsyncMock(return_value=False),
    }.items():
        stack.enter_context(patch(f"{BD}.{target}", value))
    stack.enter_context(patch.object(BackgroundDispatchService, "_verify_print_response", AsyncMock(return_value=True)))
    stack.enter_context(patch.object(BackgroundDispatchService, "_register_provider_file_info_estimate", AsyncMock()))
    stack.enter_context(patch("backend.app.main.register_expected_print"))
    stack.enter_context(patch("backend.app.farm.nas.cache.ensure_local", AsyncMock(return_value=True)))


@pytest.mark.asyncio
async def test_reprint_archive_uploads_plate_gcode_to_moonraker(tmp_path):
    archive_file = _make_3mf(tmp_path / "bracket.3mf", {1: b"G28\n", 2: PLATE_2})
    before = archive_file.read_bytes()
    archive_service = MagicMock(
        get_archive=AsyncMock(
            return_value=SimpleNamespace(id=99, filename="Bracket.gcode.3mf", file_path=str(archive_file))
        )
    )
    db = MagicMock(scalar=AsyncMock(return_value=_printer("klipper")))
    client, start_print = RecordingClient(), MagicMock(return_value=True)
    with ExitStack() as stack:
        _dispatch_patches(stack, db, client, start_print, archive_service)
        await BackgroundDispatchService()._run_reprint_archive(_job("reprint_archive", plate_id=2))

    [(remote, data, local)] = client.uploads
    assert remote == "/Bracket_plate_2.gcode"
    _assert_klipper_plate_2(data)
    assert not local.exists()  # the temporary G-code is gone
    assert start_print.call_args.args[1] == "Bracket_plate_2.gcode"
    assert archive_file.read_bytes() == before


@pytest.mark.asyncio
async def test_library_print_uploads_plate_gcode_to_moonraker(tmp_path):
    src = _make_3mf(tmp_path / "bracket.3mf", {1: b"G28\n", 2: PLATE_2})
    lib_file = SimpleNamespace(id=99, filename="Bracket.gcode.3mf", file_path=str(src))
    archive_service = MagicMock(archive_print=AsyncMock(return_value=SimpleNamespace(id=5)))
    db = MagicMock(
        scalar=AsyncMock(side_effect=[lib_file, _printer("mainsail")]), flush=AsyncMock(), commit=AsyncMock()
    )
    client, start_print = RecordingClient(), MagicMock(return_value=True)
    with ExitStack() as stack:
        _dispatch_patches(stack, db, client, start_print, archive_service)
        stack.enter_context(patch(f"{BD}.LibraryFile"))
        stack.enter_context(patch(f"{BD}.settings", SimpleNamespace(base_dir=tmp_path)))
        await BackgroundDispatchService()._run_print_library_file(_job("print_library_file", plate_id=2))

    [(remote, data, local)] = client.uploads
    assert remote == "/Bracket_plate_2.gcode"
    _assert_klipper_plate_2(data)
    assert not local.exists()
    archive_service.archive_print.assert_awaited_once()
    assert archive_service.archive_print.call_args.kwargs["source_file"] == src  # the archive keeps the 3MF


@pytest.mark.asyncio
async def test_library_print_without_plate_gcode_fails_before_the_archive(tmp_path):
    src = _make_3mf(tmp_path / "bracket.3mf", {1: b"G28\n"})
    lib_file = SimpleNamespace(id=99, filename="Bracket.gcode.3mf", file_path=str(src))
    archive_service = MagicMock(archive_print=AsyncMock())
    db = MagicMock(scalar=AsyncMock(side_effect=[lib_file, _printer("klipper")]))
    client, start_print = RecordingClient(), MagicMock(return_value=True)
    with ExitStack() as stack:
        _dispatch_patches(stack, db, client, start_print, archive_service)
        stack.enter_context(patch(f"{BD}.LibraryFile"))
        stack.enter_context(patch(f"{BD}.settings", SimpleNamespace(base_dir=tmp_path)))
        with pytest.raises(RuntimeError, match=r"Plate 3 .* not sliced"):
            await BackgroundDispatchService()._run_print_library_file(_job("print_library_file", plate_id=3))

    archive_service.archive_print.assert_not_called()
    assert client.uploads == [] and not start_print.called


@pytest.mark.asyncio
async def test_reprint_archive_still_sends_the_3mf_to_bambu(tmp_path):
    archive_file = _make_3mf(tmp_path / "bracket.3mf", {1: b"G28\n"})
    archive_service = MagicMock(
        get_archive=AsyncMock(
            return_value=SimpleNamespace(id=99, filename="Bracket.gcode.3mf", file_path=str(archive_file))
        )
    )
    db = MagicMock(scalar=AsyncMock(return_value=_printer("bambu")))
    start_print, ftp_upload = MagicMock(return_value=True), AsyncMock(return_value=True)
    with ExitStack() as stack:
        _dispatch_patches(stack, db, RecordingClient(), start_print, archive_service)
        stack.enter_context(patch(f"{BD}.upload_file_async", ftp_upload))
        await BackgroundDispatchService()._run_reprint_archive(_job("reprint_archive"))

    assert ftp_upload.call_args.args[2:4] == (archive_file, "/Bracket.3mf")
    assert start_print.call_args.args[1] == "Bracket.3mf"


# ---------------------------------------------------------------- print queue


async def _start_queue_item(test_engine, scheduler, item_id, client, archive_print):
    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch.object(ps, "printer_manager") as pm,
        patch.object(ps, "notification_service") as notify,
        patch.object(ps, "get_ftp_retry_settings", AsyncMock(return_value=(False, 1, 1, 10))),
        patch.object(ps, "upload_file_async", AsyncMock(side_effect=AssertionError("Bambu FTP must not be used"))),
        patch.object(ps.farm_printer_files, "before_upload", AsyncMock(return_value=False)),
        patch("backend.app.services.archive.ArchiveService.archive_print", archive_print),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_mark_printer_dispatched", MagicMock()),
        patch.object(scheduler, "_watchdog_print_start", AsyncMock()),
    ):
        pm.is_connected.return_value = True
        pm.get_client.return_value = client
        pm.get_status.return_value = SimpleNamespace(state="IDLE", subtask_id=None, gcode_file=None)
        pm.start_print.return_value = True
        for name in ("on_queue_job_failed", "on_queue_job_started", "on_queue_job_waiting", "on_queue_job_assigned"):
            setattr(notify, name, AsyncMock())
        async with sessions() as db:
            item = await db.get(PrintQueueItem, item_id)
            await scheduler._start_print(db, item)
        return pm


async def _queue_item(db_session, printer_factory, tmp_path, plates, plate_id):
    printer = await printer_factory(name="K2 Plus", model="K2", provider="klipper")
    src = _make_3mf(tmp_path / "bracket.3mf", plates)
    lib = LibraryFile(filename="Bracket.gcode.3mf", file_path=str(src), file_type="3mf", file_size=1)
    db_session.add(lib)
    await db_session.flush()
    item = PrintQueueItem(printer_id=printer.id, library_file_id=lib.id, plate_id=plate_id, position=1)
    db_session.add(item)
    await db_session.commit()
    return item, src


@pytest.mark.asyncio
async def test_queue_item_uploads_plate_gcode_to_moonraker(db_session, test_engine, printer_factory, tmp_path):
    item, src = await _queue_item(db_session, printer_factory, tmp_path, {1: b"G28\n", 2: PLATE_2}, 2)
    before = src.read_bytes()
    client = RecordingClient()
    pm = await _start_queue_item(test_engine, PrintScheduler(), item.id, client, AsyncMock(return_value=None))

    [(remote, data, local)] = client.uploads
    # The queue fingerprints the printer-side name (SD keep): Bracket_plate_2__<sha8>.gcode
    assert remote.startswith("/Bracket_plate_2__") and remote.endswith(".gcode")
    _assert_klipper_plate_2(data)
    assert not local.exists()
    assert pm.start_print.call_args.args[1] == remote[1:]
    await db_session.refresh(item)
    assert item.status == "printing"
    assert src.read_bytes() == before


@pytest.mark.asyncio
async def test_queue_item_without_plate_gcode_fails_before_the_archive(
    db_session, test_engine, printer_factory, tmp_path
):
    item, _ = await _queue_item(db_session, printer_factory, tmp_path, {1: b"G28\n"}, 3)
    client, archive_print = RecordingClient(), AsyncMock(return_value=None)
    await _start_queue_item(test_engine, PrintScheduler(), item.id, client, archive_print)

    await db_session.refresh(item)
    assert item.status == "failed"
    assert "Plate 3 of Bracket.gcode.3mf is not sliced" in item.error_message
    archive_print.assert_not_awaited()
    assert client.uploads == []
