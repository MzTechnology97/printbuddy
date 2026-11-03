"""Notification photo for Klipper printers: the Moonraker webcam snapshot (fake Moonraker, nothing on the network)."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from backend.app import main
from backend.app.services.printer_manager import printer_manager
from backend.app.services.printer_providers.moonraker import MoonrakerPrinterClient

# What Moonraker's server/webcams/list answers on a Klipper printer with crowsnest (made-up host).
WEBCAMS_LIST = {
    "webcams": [
        {
            "name": "cam",
            "enabled": True,
            "service": "mjpegstreamer",
            "stream_url": "/webcam/?action=stream",
            "snapshot_url": "/webcam/?action=snapshot",
            "rotation": 0,
            "source": "database",
        }
    ]
}


@asynccontextmanager
async def _no_db():
    yield None


def _klipper_printer(printer_id: int, **camera) -> SimpleNamespace:
    return SimpleNamespace(
        id=printer_id,
        name="SV08",
        provider="klipper",
        model="SV08",
        ip_address="sv08.local",
        access_code=None,
        camera_rotation=0,
        external_camera_enabled=bool(camera),
        external_camera_url=camera.get("url"),
        external_camera_type=camera.get("type"),
        external_camera_snapshot_url=camera.get("snapshot_url"),
    )


def _moonraker(webcams: dict) -> MoonrakerPrinterClient:
    client = MoonrakerPrinterClient("http://sv08.local:7125")
    client.state.connected = True
    client._get = lambda path, **kwargs: webcams if path == "server/webcams/list" else {}
    client.webcams = client.discover_webcams()
    return client


async def _snapshot(printer, client, frame):
    """``_capture_snapshot_for_notification`` with ``frame`` as what the camera answers."""
    capture = AsyncMock(return_value=frame)
    bambu = AsyncMock(return_value=b"bambu")
    with (
        patch.object(main, "async_session", _no_db),
        patch("backend.app.api.routes.settings.get_setting", AsyncMock(return_value=None)),
        patch("backend.app.services.external_camera.capture_frame", capture),
        patch("backend.app.services.camera.capture_camera_frame_bytes", bambu),
        patch.dict(printer_manager._clients, {printer.id: client}),
    ):
        image = await main._capture_snapshot_for_notification(printer.id, printer, main.logging.getLogger("test"))
    bambu.assert_not_called()  # never the Bambu camera protocol on a Klipper printer
    return image, capture


@pytest.mark.asyncio
async def test_klipper_notification_gets_the_moonraker_snapshot():
    image, capture = await _snapshot(_klipper_printer(901), _moonraker(WEBCAMS_LIST), b"\xff\xd8jpeg")
    assert image == b"\xff\xd8jpeg"
    # The relative path is completed with the UI host (port 80), not Moonraker's 7125.
    assert capture.call_args.kwargs["snapshot_url"] == "http://sv08.local/webcam/?action=snapshot"
    assert capture.call_args.kwargs["timeout"] == main._NOTIFICATION_SNAPSHOT_TIMEOUT <= 5


@pytest.mark.asyncio
async def test_camera_that_does_not_answer_leaves_the_notification_without_a_photo():
    image, capture = await _snapshot(_klipper_printer(902), _moonraker(WEBCAMS_LIST), None)
    assert image is None
    capture.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_webcam_configured_means_no_photo_and_no_capture():
    image, capture = await _snapshot(_klipper_printer(903), _moonraker({"webcams": []}), b"unused")
    assert image is None
    capture.assert_not_called()


@pytest.mark.asyncio
async def test_manual_camera_wins_over_the_moonraker_webcam():
    printer = _klipper_printer(
        904, url="http://cam.local/stream", type="mjpeg", snapshot_url="http://cam.local/snapshot.jpg"
    )
    image, capture = await _snapshot(printer, _moonraker(WEBCAMS_LIST), b"\xff\xd8manual")
    assert image == b"\xff\xd8manual"
    assert capture.call_args.kwargs["snapshot_url"] == "http://cam.local/snapshot.jpg"
