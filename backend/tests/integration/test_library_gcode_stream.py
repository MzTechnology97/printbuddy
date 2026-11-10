"""Fork (printfarm): /library/files/{id}/gcode streams a .gcode.3mf's plate (plates run to ~800 MB)."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from backend.app.api.routes import library
from backend.app.models.library import LibraryFile


async def add_sliced(db, plate: bytes) -> LibraryFile:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
        zf.writestr("Metadata/plate_1.gcode", plate)
    data = buf.getvalue()
    path = library.get_library_files_dir() / f"stream-{hashlib.sha256(data).hexdigest()[:12]}.gcode.3mf"
    path.write_bytes(data)
    row = LibraryFile(
        filename="plate.gcode.3mf",
        file_path=library.to_relative_path(path),
        file_type="gcode",
        file_size=len(data),
        file_hash=hashlib.sha256(data).hexdigest(),
    )
    db.add(row)
    await db.commit()
    return row


@pytest.mark.asyncio
async def test_plate_is_streamed_with_its_length(async_client, db_session, monkeypatch):
    # several MiB of distinct lines, so the response takes several chunks
    plate = "".join(f"G1 X{i % 250}.{i % 7} Y{i % 233}.5 E0.0{i % 9}\n" for i in range(150_000)).encode()
    assert len(plate) > 3 << 20
    row = await add_sliced(db_session, plate)

    def no_full_read(*_args, **_kwargs):
        raise AssertionError("the plate must not be read whole into memory")

    monkeypatch.setattr(zipfile.ZipFile, "read", no_full_read)
    resp = await async_client.get(f"/api/v1/library/files/{row.id}/gcode")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-length"] == str(len(plate))
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.content == plate


@pytest.mark.asyncio
async def test_archive_without_gcode_and_broken_archive(async_client, db_session):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    path = library.get_library_files_dir() / "stream-empty.3mf"
    path.write_bytes(buf.getvalue())
    empty = LibraryFile(filename="empty.3mf", file_path=library.to_relative_path(path), file_type="3mf", file_size=1)
    bad_path = library.get_library_files_dir() / "stream-broken.3mf"
    bad_path.write_bytes(b"not a zip")
    broken = LibraryFile(
        filename="broken.3mf", file_path=library.to_relative_path(bad_path), file_type="3mf", file_size=9
    )
    db_session.add_all([empty, broken])
    await db_session.commit()
    assert (await async_client.get(f"/api/v1/library/files/{empty.id}/gcode")).status_code == 404
    assert (await async_client.get(f"/api/v1/library/files/{broken.id}/gcode")).status_code == 400
