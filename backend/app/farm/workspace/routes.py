"""3MF Workspace API (fork): read a library 3MF's project, save an edited copy.

Same permissions as the library: reading needs ``library:read``, saving a copy (a new library
file) ``library:upload``. Only library rows are opened, never a path from the request.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timezone

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

router = APIRouter(prefix="/workspace", tags=["farm-workspace"])

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
    }


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


def _build_copy(lib_file: LibraryFile, data: bytes, changes: dict) -> tuple[bytes, list[dict]]:
    """Blocking: the copy's bytes and the changes applied (HTTP errors for what can't be saved)."""
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
        return project.write_copy(data, new_cfg), applied
    except ws_settings.InvalidChange as exc:
        raise HTTPException(status_code=422, detail=f"Some values were refused: {exc}"[:1000]) from exc
    except project.ArchiveError as exc:
        raise HTTPException(status_code=422, detail=f"Cannot save a copy of this 3MF: {exc}"[:500]) from exc


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
    original, where library sync would take it for a farm project.
    """
    if len(body.changes) > MAX_CHANGES:
        raise HTTPException(status_code=422, detail=f"Too many changes at once (at most {MAX_CHANGES})")
    lib_file = await _library_3mf(db, file_id)
    filename = _copy_name(lib_file.filename, body.filename)
    data, sha = await _read(lib_file)
    if sha != body.base_sha256:
        raise HTTPException(status_code=409, detail="The file changed since it was opened: reload it, then edit again")
    copy, applied = await asyncio.to_thread(_build_copy, lib_file, data, body.changes)

    folder = await db.get(LibraryFolder, lib_file.folder_id) if lib_file.folder_id else None
    saved, _ = await lib.save_3mf_bytes_to_library(
        db,
        file_bytes=copy,
        filename=filename,
        folder_id=folder.id if folder is not None and not folder.is_external else None,
        source_type="3mf_workspace",
        owner_id=user.id if user else None,
    )
    source_info = (lib_file.file_metadata or {}).get("workspace") or {}
    saved.file_metadata = {
        **(saved.file_metadata or {}),
        "workspace": {
            "copy_of_library_file_id": lib_file.id,
            "copy_of_filename": lib_file.filename,
            "original_library_file_id": source_info.get("original_library_file_id", lib_file.id),
            "source_sha256": sha,
            "saved_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "changes": applied,
        },
    }
    await db.commit()
    return {"library_file_id": saved.id, "filename": saved.filename, "folder_id": saved.folder_id, "changes": applied}
