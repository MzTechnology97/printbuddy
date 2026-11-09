"""3MF Workspace API (fork): read a library 3MF's project, save an edited copy.

Same permissions as the library: reading needs ``library:read``, saving a copy (a new library
file) ``library:upload``. Only library rows are opened, never a path from the request.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import library as lib
from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.nas.access import read_file, run as access_run
from backend.app.farm.workspace import project, settings as ws_settings
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workspace", tags=["farm-workspace"])
_one_build = asyncio.Semaphore(1)  # a copy holds its source in memory: one at a time

MAX_CHANGES = 1000
Value = str | int | float | bool


class CopyRequest(BaseModel):
    base_sha256: str = Field(min_length=64, max_length=64)
    changes: dict[str, Value | list[Value]]
    filename: str | None = Field(default=None, max_length=200)


async def _library_3mf(db: AsyncSession, file_id: int) -> LibraryFile:
    lib_file = await db.scalar(LibraryFile.active().where(LibraryFile.id == file_id))
    if lib_file is None:
        raise HTTPException(status_code=404, detail="File not found")
    if not lib_file.filename.lower().endswith(".3mf"):
        raise HTTPException(status_code=400, detail="Only 3MF files open in the workspace")
    return lib_file


async def _read(lib_file: LibraryFile) -> tuple[bytes, str]:
    """The file's bytes (a NAS file read from the NAS, never a stale cached copy) and their SHA-256."""
    try:
        data = await access_run(read_file, lib_file.file_path, lambda fh: fh.read(project.MAX_ARCHIVE_BYTES + 1))
    except OSError as exc:
        raise HTTPException(status_code=404, detail="File not readable (missing, or the NAS is not reachable)") from exc
    return data, hashlib.sha256(data).hexdigest()


def _document(lib_file: LibraryFile, data: bytes) -> dict:
    """Blocking: everything the workspace shows. ``ArchiveError`` for a file that isn't a readable 3MF."""
    zf = project.open_archive(data)
    cfg = project.read_settings(zf)
    kind, slicer, application = project.classify(lib_file.filename, zf, cfg)
    structure = project.read_structure(zf, cfg)
    overrides: dict[str, list[dict]] = {}
    for obj in structure["objects"]:
        for o in obj["overrides"]:
            overrides.setdefault(o["key"], []).append({"object_id": obj["id"], "object": obj["name"], **o})
    editable = kind == project.KIND_PROJECT and not structure["warnings"]
    return {
        "kind": kind,
        "slicer": slicer,
        "application": application,
        "editable": editable,
        "plates": structure["plates"],
        "objects": structure["objects"],
        "warnings": structure["warnings"],
        "settings": ws_settings.describe(cfg, overrides) if cfg else [],
        "tabs": list(ws_settings.TABS),
    }


@router.get("/files/{file_id}")
async def get_workspace(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ)),
):
    """The project of a library 3MF: kind, plates and objects, every setting with its state."""
    lib_file = await _library_3mf(db, file_id)
    data, sha = await _read(lib_file)
    try:
        doc = await asyncio.to_thread(_document, lib_file, data)
    except project.ArchiveError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot open this 3MF: {exc}"[:500]) from exc
    state = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == file_id))
    return {
        "file": {
            "id": lib_file.id,
            "filename": lib_file.filename,
            "folder_id": lib_file.folder_id,
            "is_external": lib_file.is_external,
            "size": len(data),
            "watched": state is not None,  # on a farm-synced folder (NAS library)
        },
        "sha256": sha,
        "copy_of": (lib_file.file_metadata or {}).get("workspace"),
        **doc,
        "slicer": (state.slicer if state else None) or doc["slicer"],  # the per-file override wins, as when slicing
        "outputs": await _outputs(db, lib_file, state.sha256 if state else lib_file.file_hash),
    }


async def _outputs(db: AsyncSession, lib_file: LibraryFile, version: str | None) -> list[dict]:
    """The latest project-settings slice of each plate (the Preview tab opens it), by plate.

    ``current``: sliced from the version of the 3MF opened now. Colour variants and Klipper G-code
    taken out of an output are left out: they are farm outputs of their own.
    """
    from backend.app.farm.slicing import colour_key, plate_key

    latest: dict[int, LibraryFile] = {}
    rows = await db.execute(
        LibraryFile.active()
        .where(
            LibraryFile.source_type == "sliced",
            LibraryFile.file_metadata["sliced_from_library_file_id"].as_integer() == lib_file.id,
        )
        .order_by(LibraryFile.id)
    )
    for out in rows.scalars():
        meta = out.file_metadata or {}
        if (
            meta.get("used_embedded_settings")
            and meta.get("sliced_from_library_file_id") == lib_file.id
            and "extracted_from_library_file_id" not in meta
            and not colour_key(meta.get("sliced_color"))
        ):
            latest[plate_key(meta.get("sliced_plate"))] = out
    return [
        {
            "plate": plate,
            "library_file_id": out.id,
            "filename": out.filename,
            "print_time_seconds": (out.file_metadata or {}).get("print_time_seconds"),
            "filament_used_g": (out.file_metadata or {}).get("filament_used_g"),
            "current": (out.file_metadata or {}).get("sliced_from_sha256") == version,
        }
        for plate, out in sorted(latest.items())
    ]


def _copy_name(source: str, wanted: str | None) -> str:
    stem = source[: -len(".3mf")]
    if not wanted or not wanted.strip():
        return f"{stem} (edited {datetime.now():%Y-%m-%d %H%M}).3mf"
    name = wanted.strip()
    if any(c in name for c in '/\\:*?"<>|') or any(ord(c) < 32 for c in name):
        raise HTTPException(
            status_code=422, detail="The file name can't hold / \\ : * ? \" < > | or control characters"
        )
    if name.lower().endswith(".gcode.3mf"):
        raise HTTPException(status_code=422, detail="A copy is a project, not a .gcode.3mf")
    return name if name.lower().endswith(".3mf") else f"{name}.3mf"


def _build_copy(lib_file: LibraryFile, data: bytes, changes: dict, dest: Path) -> list[dict]:
    """Blocking: write the copy to ``dest``; the changes applied (HTTP errors for what can't be saved)."""
    try:
        zf = project.open_archive(data)
        cfg = project.read_settings(zf)
        kind, _, _ = project.classify(lib_file.filename, zf, cfg)
        if kind != project.KIND_PROJECT or cfg is None:
            raise HTTPException(status_code=400, detail=f"This file can only be inspected ({kind})")
        if project.read_structure(zf, cfg)["warnings"]:
            raise HTTPException(status_code=400, detail="This file can only be inspected (unreadable model settings)")
        new_cfg, applied = ws_settings.apply_changes(cfg, changes)
        if not applied:
            raise HTTPException(status_code=400, detail="Nothing changed: no copy saved")
        project.write_copy(data, new_cfg, dest)
        return applied
    except ws_settings.InvalidChange as exc:
        raise HTTPException(status_code=422, detail=f"Some values were refused: {exc}"[:1000]) from exc
    except project.ArchiveError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot save a copy of this 3MF: {exc}"[:500]) from exc


def _publish(tmp: Path) -> tuple[Path, str, dict | None, str | None]:
    """Blocking: the checked copy under its library name, its hash, metadata and thumbnail (as an upload has)."""
    path = tmp.with_name(f"{uuid.uuid4().hex}.3mf")
    os.replace(tmp, path)  # a reader never sees half a file
    metadata = thumbnail = None
    try:
        raw = lib.ThreeMFParser(str(path)).parse()
        if raw.get("_thumbnail_data"):
            thumb = lib.get_library_thumbnails_dir() / f"{uuid.uuid4().hex}{raw.get('_thumbnail_ext', '.png')}"
            thumb.write_bytes(raw["_thumbnail_data"])
            thumbnail = lib.to_relative_path(thumb)
        metadata = lib._without_print_name(lib._clean_3mf_metadata(raw) or None)
    except Exception as exc:  # metadata is optional, as for an upload
        logger.warning("Workspace copy %s: metadata not read: %s", path.name, exc)
    return path, lib.calculate_file_hash(path), metadata, thumbnail


@router.post("/files/{file_id}/copy")
async def save_copy(
    file_id: int,
    body: CopyRequest,
    db: AsyncSession = Depends(get_db),
    user: User | None = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD)),
):
    """Save the project with ``changes`` as a new 3MF in the internal library; the original stays as it is.

    Refused (409) when the file changed since ``base_sha256`` was read. The copy goes to the
    original's folder when that is an internal one, else to the library root: never next to a NAS
    original, where library sync would take it for a farm project. One copy is built at a time
    (the source is held in memory), off the event loop; the file and its library row (with the
    ``workspace`` lineage) appear together, and nothing is left behind when either fails.
    """
    if len(body.changes) > MAX_CHANGES:
        raise HTTPException(status_code=422, detail=f"Too many changes at once (at most {MAX_CHANGES})")
    lib_file = await _library_3mf(db, file_id)
    filename = _copy_name(lib_file.filename, body.filename)
    folder = await db.get(LibraryFolder, lib_file.folder_id) if lib_file.folder_id else None
    tmp = lib.get_library_files_dir() / f".{uuid.uuid4().hex}.3mf.part"
    path = thumbnail = None
    async with _one_build:
        data, sha = await _read(lib_file)
        if sha != body.base_sha256:
            raise HTTPException(
                status_code=409, detail="The file changed since it was opened: reload it, then edit again"
            )
        try:
            applied = await asyncio.to_thread(_build_copy, lib_file, data, body.changes, tmp)
            del data
            path, file_hash, metadata, thumbnail = await asyncio.to_thread(_publish, tmp)
            source_info = (lib_file.file_metadata or {}).get("workspace") or {}
            saved = LibraryFile(
                folder_id=folder.id if folder is not None and not folder.is_external else None,
                filename=filename,
                file_path=lib.to_relative_path(path),
                file_type="3mf",
                file_size=path.stat().st_size,
                file_hash=file_hash,
                thumbnail_path=thumbnail,
                source_type="3mf_workspace",
                created_by_id=user.id if user else None,
                file_metadata={
                    **(metadata or {}),
                    "workspace": {
                        "copy_of_library_file_id": lib_file.id,
                        "copy_of_filename": lib_file.filename,
                        "original_library_file_id": source_info.get("original_library_file_id", lib_file.id),
                        "source_sha256": sha,
                        "saved_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                        "changes": applied,
                    },
                },
            )
            db.add(saved)
            await db.commit()
        except BaseException:
            for leftover in (tmp, path, lib.to_absolute_path(thumbnail)):
                if leftover is not None:
                    leftover.unlink(missing_ok=True)
            raise
    return {"library_file_id": saved.id, "filename": saved.filename, "folder_id": saved.folder_id, "changes": applied}
