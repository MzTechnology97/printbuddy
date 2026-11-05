"""Fork: a failed or stopped queue item keeps why, where and what was said, for the queue history.

No printer is contacted: uploads, starts and the printer manager are stubbed. Data is made up.
"""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services import queue_failure

FTP_LOG = logging.getLogger("backend.app.services.bambu_ftp")


@pytest.fixture
async def queued_file(db_session, printer_factory, tmp_path):
    """A pending queue item for a local library file on a printer."""
    printer = await printer_factory(name="P1S")
    path = tmp_path / "part.gcode.3mf"
    path.write_bytes(b"3mf")
    lib_file = LibraryFile(filename="part.gcode.3mf", file_path=str(path), file_type="3mf", file_size=3)
    db_session.add(lib_file)
    await db_session.flush()
    item = PrintQueueItem(printer_id=printer.id, library_file_id=lib_file.id, position=1, status="pending")
    db_session.add(item)
    await db_session.commit()
    return item


async def start(db_session, item, *, connected=True, upload=None, started=True):
    """``_start_print`` with the printer, the upload and the start command stubbed."""
    from backend.app.services.print_scheduler import PrintScheduler

    scheduler = PrintScheduler()

    async def same_name(name, path):
        return name

    with (
        patch("backend.app.services.print_scheduler.printer_manager") as pm,
        patch("backend.app.services.print_scheduler.notification_service") as notify,
        patch("backend.app.services.print_scheduler.get_ftp_retry_settings", AsyncMock(return_value=(False, 1, 1, 10))),
        patch("backend.app.services.print_scheduler.farm_printer_files.fingerprint", same_name),
        patch("backend.app.services.archive.ArchiveService.archive_print", AsyncMock(return_value=None)),
        patch.object(scheduler, "_delete_remote_file_for_provider", AsyncMock()),
        patch.object(scheduler, "_upload_file_for_provider", upload or AsyncMock(return_value=True)),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_mark_printer_dispatched", MagicMock()),
    ):
        pm.is_connected.return_value = connected
        pm.get_status.return_value = None
        pm.start_print.return_value = started
        notify.on_queue_job_failed = AsyncMock()
        notify.on_queue_job_started = AsyncMock()
        await scheduler._start_print(db_session, item)
    await db_session.refresh(item)
    return item


# ---------------------------------------------------------------- sending


@pytest.mark.asyncio
async def test_upload_failure_keeps_the_ftp_error_not_only_the_generic_advice(db_session, queued_file):
    async def failing_upload(**kwargs):
        await asyncio.to_thread(FTP_LOG.error, "FTP upload failed for /part.gcode.3mf: 552 Storage quota exceeded")
        return False

    item = await start(db_session, queued_file, upload=failing_upload)
    assert item.status == "failed"
    assert (
        item.error_message
        == "Upload to the printer failed: FTP upload failed for /part.gcode.3mf: 552 Storage quota exceeded"
    )
    assert item.failure_detail["phase"] == "upload"
    assert (
        "ERROR: FTP upload failed for /part.gcode.3mf: 552 Storage quota exceeded" in item.failure_detail["technical"]
    )


@pytest.mark.asyncio
async def test_failures_before_the_upload_and_at_the_start_say_their_phase(db_session, queued_file):
    item = await start(db_session, queued_file, connected=False)
    assert (item.error_message, item.failure_detail) == ("Printer not connected", {"phase": "dispatch"})

    item.status = "pending"
    await db_session.commit()
    item = await start(db_session, item, started=False)
    assert (item.error_message, item.failure_detail["phase"]) == ("Failed to send print command to printer", "start")


# ---------------------------------------------------------------- the printer ended it


async def complete(item, data: dict) -> PrintQueueItem:
    """``on_print_complete`` for the printing item, everything around it stubbed (as test_print_queue_api)."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = [item]
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value=result)
    before = set(asyncio.all_tasks())
    with (
        patch("backend.app.main.async_session", return_value=session),
        patch("backend.app.core.database.async_session", return_value=session),
        patch("backend.app.main.ws_manager") as ws,
        patch("backend.app.main.mqtt_relay") as relay,
        patch("backend.app.main.notification_service") as notify,
        patch("backend.app.main.smart_plug_manager") as plug,
        patch("backend.app.main.printer_manager") as pm,
        patch("backend.app.main._user_stopped_printers", set()),  # another test's stop must not turn it "cancelled"
    ):
        ws.send_print_complete = AsyncMock()
        ws.broadcast = AsyncMock()
        relay.on_print_complete = AsyncMock()
        relay.on_queue_job_completed = AsyncMock()
        notify.on_print_complete = AsyncMock()
        plug.on_print_complete = AsyncMock()
        pm.get_printer.return_value = None
        from backend.app.main import on_print_complete

        await on_print_complete(item.printer_id, {"filename": "part.gcode", "subtask_name": "part", **data})
        for task in asyncio.all_tasks() - before:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    return item


def moonraker(state: str, message: str = "") -> dict:
    return {
        "print_stats": {"state": state, "message": message, "info": {"current_layer": 30, "total_layer": 120}},
        "webhooks": {"state": "ready", "state_message": "Printer is ready"},
        "virtual_sdcard": {"progress": 0.25},
    }


@pytest.mark.asyncio
async def test_klipper_error_reaches_the_queue_item(queued_file, db_session):
    queued_file.status = "printing"
    await db_session.commit()
    item = await complete(
        queued_file, {"status": "failed", "raw_data": moonraker("error", "Heater extruder not heating")}
    )
    assert (item.status, item.error_message) == ("failed", "Printer error: Heater extruder not heating")
    detail = item.failure_detail
    assert (detail["phase"], detail["progress"], detail["layer"], detail["total_layers"]) == ("printing", 25.0, 30, 120)


@pytest.mark.asyncio
async def test_klipper_cancel_on_the_printer_is_told_apart(queued_file, db_session):
    queued_file.status = "printing"
    await db_session.commit()
    item = await complete(queued_file, {"status": "stopped", "raw_data": moonraker("cancelled")})
    assert (item.status, item.error_message) == ("cancelled", "Cancelled on the printer")
    assert item.failure_detail["code"] == "cancelled_on_printer"


@pytest.mark.asyncio
async def test_a_failure_without_a_reason_is_never_empty(queued_file, db_session):
    queued_file.status = "printing"
    await db_session.commit()
    item = await complete(queued_file, {"status": "failed", "hms_errors": []})
    assert (item.error_message, item.failure_detail["code"]) == (queue_failure.UNKNOWN_REASON, "unknown")


# ---------------------------------------------------------------- cancelled by someone


@pytest.mark.asyncio
async def test_cancel_says_who_and_from_where(async_client, db_session, queued_file):
    other = PrintQueueItem(
        printer_id=queued_file.printer_id, library_file_id=queued_file.library_file_id, position=2, status="pending"
    )
    db_session.add(other)
    await db_session.commit()

    assert (await async_client.post(f"/api/v1/queue/{queued_file.id}/cancel")).status_code == 200
    api = await async_client.post(f"/api/v1/queue/{other.id}/cancel", headers={"X-API-Key": "bb_made_up"})
    assert api.status_code == 200
    history = {i["id"]: i for i in (await async_client.get("/api/v1/queue/", params={"status": "cancelled"})).json()}
    assert history[queued_file.id]["failure_detail"] == {"phase": "dispatch", "by": None, "via": "panel"}
    assert history[other.id]["failure_detail"]["via"] == "api"
