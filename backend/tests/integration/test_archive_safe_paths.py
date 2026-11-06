"""Archive photo and timelapse paths stay inside the archive dir (Bambuddy 0.2.4.5)."""

import shutil

import pytest
from httpx import AsyncClient

from backend.app.core.config import settings


@pytest.mark.asyncio
@pytest.mark.integration
async def test_photo_not_listed_on_archive_is_not_served(async_client: AsyncClient, printer_factory, archive_factory):
    printer = await printer_factory()
    archive = await archive_factory(printer.id, file_path="archives/safe_path_test/a.3mf", photos=["ok.jpg"])
    photos = settings.base_dir / "archives" / "safe_path_test" / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "ok.jpg").write_bytes(b"jpg")
    (photos / "unlisted.jpg").write_bytes(b"jpg")
    try:
        assert (await async_client.get(f"/api/v1/archives/{archive.id}/photos/ok.jpg")).status_code == 200
        assert (await async_client.get(f"/api/v1/archives/{archive.id}/photos/unlisted.jpg")).status_code == 404
    finally:
        shutil.rmtree(photos.parent)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("name", ["../escaped.mp4", "/abs/escaped.mp4"])
async def test_attach_timelapse_refuses_unsafe_name(db_session, printer_factory, archive_factory, name):
    from backend.app.services.archive import ArchiveService

    printer = await printer_factory()
    archive = await archive_factory(printer.id, file_path="archives/safe_path_test/a.3mf")
    assert await ArchiveService(db_session).attach_timelapse(archive.id, b"video", name) is False
    assert not (settings.base_dir / "archives" / "escaped.mp4").exists()
