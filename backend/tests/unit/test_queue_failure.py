"""Fork: the reason a queue item failed, from each source: the upload, a Bambu printer, a Klipper printer, or none.

No printer is contacted. Data is made up.
"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from backend.app.services import queue_failure as qf

FTP_LOG = logging.getLogger("backend.app.services.bambu_ftp")


# ---------------------------------------------------------------- Bambu


def test_bambu_failure_names_its_hms_codes_and_where_it_stopped():
    reason, detail = qf.printer_failure(
        "failed",
        {
            "hms_errors": [{"code": "0x8004", "attr": 0x03000000, "module": 3, "severity": 2}],
            "raw_data": {"print_error": 0x03008004},
            "last_progress": 37,
            "last_layer_num": 52,
        },
    )
    assert reason.startswith("[0300_8004]")
    assert (detail["phase"], detail["code"], detail["progress"], detail["layer"]) == (
        "printing",
        "printer_error",
        37,
        52,
    )
    assert "print_error=50364420 (0x03008004)" in detail["technical"]


def test_bambu_stopped_on_the_printer():
    reason, detail = qf.printer_failure("cancelled", {"hms_errors": [], "last_progress": 80})
    assert (reason, detail["code"], detail["progress"]) == ("Stopped on the printer", "cancelled_on_printer", 80)


# ---------------------------------------------------------------- Klipper / Moonraker


def klipper(state: str, message: str = "", hooks: str = "ready", hooks_message: str = "") -> dict:
    return {
        "status": "failed",
        "raw_data": {
            "print_stats": {"state": state, "message": message, "info": {"current_layer": 12, "total_layer": 80}},
            "webhooks": {"state": hooks, "state_message": hooks_message},
            "virtual_sdcard": {"progress": 0.4211},
        },
    }


def test_klipper_error_gives_its_message_layer_and_progress():
    reason, detail = qf.printer_failure("failed", klipper("error", "Move out of range: 300.000 0.000 0.200 [0.000]"))
    assert reason == "Printer error: Move out of range: 300.000 0.000 0.200 [0.000]"
    assert (detail["code"], detail["progress"], detail["layer"], detail["total_layers"]) == (
        "printer_error",
        42.1,
        12,
        80,
    )
    assert "print_stats.state=error" in detail["technical"]


def test_klipper_shutdown_says_what_klipper_said():
    reason, detail = qf.printer_failure(
        "failed", klipper("error", hooks="shutdown", hooks_message="MCU 'mcu' shutdown: Timer too close")
    )
    assert reason == "Klipper shutdown: MCU 'mcu' shutdown: Timer too close"
    assert "webhooks.state=shutdown" in detail["technical"]


def test_klipper_cancelled_on_the_printer():
    reason, detail = qf.printer_failure("cancelled", klipper("cancelled"))
    assert (reason, detail["code"]) == ("Cancelled on the printer", "cancelled_on_printer")


# ---------------------------------------------------------------- unknown


@pytest.mark.parametrize(
    "data",
    [
        {"hms_errors": []},  # Bambu with nothing active
        klipper("standby"),  # Klipper reports no message
    ],
)
def test_a_failure_the_printer_did_not_explain_is_never_empty(data):
    reason, detail = qf.printer_failure("failed", data)
    assert (reason, detail["code"]) == (qf.UNKNOWN_REASON, "unknown")


def test_mark_failed_without_a_reason_says_so():
    item = SimpleNamespace()
    qf.mark_failed(item, None, "upload", technical=[])
    assert (item.status, item.error_message, item.failure_detail) == (
        "failed",
        qf.UNKNOWN_REASON,
        {"phase": "upload", "code": "unknown"},
    )


# ---------------------------------------------------------------- upload log and who


@pytest.mark.asyncio
async def test_capture_keeps_the_errors_logged_by_this_upload_threads_included():
    with qf.capture_logged_errors() as lines:
        FTP_LOG.info("FTP connected")  # below a warning: not a reason
        await asyncio.to_thread(FTP_LOG.error, "FTP 552 error - Storage quota exceeded (SD card full?)")
    FTP_LOG.error("another printer: 421")  # after the upload: not this item's
    assert lines == ["ERROR: FTP 552 error - Storage quota exceeded (SD card full?)"]


@pytest.mark.asyncio
async def test_bambu_ftp_upload_threads_log_into_the_capture(monkeypatch, tmp_path):
    from backend.app.services import bambu_ftp

    class FakeClient:
        A1_MODELS: tuple = ()
        _mode_cache: dict = {}
        busy = False

        def __init__(self, *args, **kwargs):
            pass

        def connect(self):
            return True

        def upload_file(self, local_path, remote_path, progress_callback=None):
            bambu_ftp.logger.error("FTP upload failed for %s: 553 Could not create file (error code: 553)", remote_path)
            return False

        def disconnect(self):
            pass

    monkeypatch.setattr(bambu_ftp, "BambuFTPClient", FakeClient)
    src = tmp_path / "part.3mf"
    src.write_bytes(b"x")
    with qf.capture_logged_errors() as lines:
        assert await bambu_ftp.upload_file_async("192.0.2.1", "code", src, "/part.3mf") is False
    assert "ERROR: FTP upload failed for /part.3mf: 553 Could not create file (error code: 553)" in lines


def test_requester_tells_the_panel_from_an_api_key():
    user = SimpleNamespace(username="mario")
    panel = SimpleNamespace(headers={"authorization": "Bearer eyJ.jwt"})
    api = SimpleNamespace(headers={"x-api-key": "bb_secret"})
    assert qf.requester(user, panel) == {"by": "mario", "via": "panel"}
    assert qf.requester(None, api) == {"by": "API key", "via": "api"}
    assert qf.requester(None, None) == {"by": None, "via": "panel"}
