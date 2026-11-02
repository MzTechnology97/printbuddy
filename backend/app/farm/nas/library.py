"""Library sync of the NAS folder straight over SMB: listings and a few zip entries, no copies.

The watched-folder logic (refresh in place, relink, missing, corrupt, pending) is the
one in ``library_sync``; only listing and per-file analysis change:

- the listing gives name, size and mtime, so unchanged files cost nothing;
- a 3MF is opened as a seekable remote file: ``zipfile`` reads the central directory
  and the few entries we need (header, settings, thumbnail), not the whole file;
- the version id stored as ``sha256`` is a hash of the zip directory (names, CRC-32,
  sizes), which changes with the content like a file hash but needs no full read.
  Other files (STL, STEP, G-code) use size and mtime.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import time
import zipfile
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import library as lib
from backend.app.farm.detect import CorruptFileError, inspect_3mf
from backend.app.farm.library_sync import _Analysis, _apply_detection, _DiskEntry, _file_type, _sync
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.nas import access, cache, service
from backend.app.farm.nas.remote import RemoteEntry, check_name, with_retry
from backend.app.models.library import LibraryFile, LibraryFolder

logger = logging.getLogger(__name__)

# Same order as the upstream 3MF parser.
THUMBNAILS = (
    "Metadata/plate_1.png",
    "Metadata/thumbnail.png",
    "Metadata/model_thumbnail.png",
    "Auxiliaries/.thumbnails/thumbnail_middle.png",
    "Auxiliaries/.thumbnails/thumbnail_small.png",
    "Auxiliaries/.thumbnails/thumbnail_3mf.png",
)


def walk(active: access.Active) -> tuple[list[_DiskEntry], set[str]]:
    """Print files under the configured subfolder. Raises if any listing fails (nothing goes missing)."""
    entries: list[_DiskEntry] = []
    seen_dirs: set[str] = set()
    stack: list[tuple[str, ...]] = [()]
    while stack:
        rel = stack.pop()
        rel_dir = str(Path(*rel)) if rel else ""
        seen_dirs.add(rel_dir)
        for e in with_retry(active.remote.scandir, active.base + rel):
            if e.name.startswith("."):
                continue
            try:
                name = check_name(e.name)
            except ValueError:
                continue
            if e.is_dir:
                stack.append(rel + (name,))
            elif (file_type := _file_type(Path(name))) is not None:
                path = active.cache_root.joinpath(*rel, name)
                entries.append(_DiskEntry(str(path), rel_dir, name, file_type, e.mtime, e.size))
    return entries, seen_dirs


def stat_fingerprint(entry: _DiskEntry) -> str:
    return hashlib.sha256(f"{entry.size}:{int(entry.mtime)}".encode()).hexdigest()


def zip_fingerprint(zf: zipfile.ZipFile) -> str:
    h = hashlib.sha256()
    for info in sorted(zf.infolist(), key=lambda i: i.filename):
        h.update(f"{info.filename}\0{info.CRC}\0{info.file_size}\n".encode())
    return h.hexdigest()


def _inspect_zip(entry: _DiskEntry, fh: BinaryIO) -> _Analysis:
    try:
        info = inspect_3mf(fh)
    except CorruptFileError as exc:
        return _Analysis(sha256=stat_fingerprint(entry), corrupt=str(exc)[:255])
    with zipfile.ZipFile(fh) as zf:
        result = _Analysis(sha256=zip_fingerprint(zf), info=info)
        names = set(zf.namelist())
        thumb = next((zf.read(n) for n in THUMBNAILS if n in names), None) if entry.file_type == "3mf" else None
    if thumb:
        thumb_full = lib.get_library_thumbnails_dir() / f"{os.urandom(16).hex()}.png"
        thumb_full.write_bytes(thumb)
        result.thumbnail_path = lib.to_relative_path(thumb_full)
    return result


def analyze(active: access.Active, entry: _DiskEntry) -> _Analysis:
    """Blocking; run in a thread. OSError (connection lost, server-side read error) = retried next scan."""
    if entry.file_type not in ("3mf", "gcode.3mf"):
        return _Analysis(sha256=stat_fingerprint(entry))
    return with_retry(active.remote.read, active.parts(entry.path), partial(_inspect_zip, entry))


# Slicing outputs of a NAS project are written next to it, in this subfolder.
OUTPUT_DIR = "Printbuddy"


@dataclass(slots=True)
class SavedOutput:
    path: Path  # library path, with the file already in the cache
    folder_id: int
    filename: str
    mtime: float
    size: int


def _unique(name: str, taken: set[str]) -> str:
    if name not in taken:
        return name
    stem, ext = (
        (name.removesuffix(".gcode.3mf"), ".gcode.3mf") if name.endswith(".gcode.3mf") else os.path.splitext(name)
    )
    n = 2
    while f"{stem} ({n}){ext}" in taken:
        n += 1
    return f"{stem} ({n}){ext}"


def _publish(active: access.Active, out_dir: tuple[str, ...], name: str, data: bytes) -> RemoteEntry:
    """Write a new file on the NAS without ever replacing one: temp dot name, then rename without overwrite."""
    remote = active.remote
    with_retry(remote.makedirs, out_dir)
    final = _unique(check_name(name), {e.name for e in with_retry(remote.scandir, out_dir)})
    tmp = out_dir + (f".{final}.part",)  # dot names are skipped by the scan
    with_retry(remote.write, tmp, data)
    with_retry(remote.rename, tmp, out_dir + (final,))
    return next(e for e in with_retry(remote.scandir, out_dir) if e.name == final)


async def save_sliced(db: AsyncSession, lib_file: LibraryFile, filename: str, data: bytes) -> SavedOutput | None:
    """Store a slicing output of a NAS project on the NAS (``<project dir>/Printbuddy``) and keep
    the local copy in the cache. None when the project isn't on the NAS."""
    active = await service.refresh_access(db)
    src = active.parts(lib_file.file_path) if active else None
    if src is None:
        return None
    out_dir = src[:-1] + (OUTPUT_DIR,)
    entry = await asyncio.to_thread(_publish, active, out_dir, filename, data)
    path = active.cache_root.joinpath(*out_dir[len(active.base) :], entry.name)

    def keep_copy() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, (time.time(), entry.mtime))  # NAS mtime: the cache sees it as current

    await asyncio.to_thread(keep_copy)
    limit = (await service.load(db)).cache_mb * 1024 * 1024
    await asyncio.to_thread(cache.evict, active.cache_root, limit)
    folder = await db.scalar(
        select(LibraryFolder).where(
            LibraryFolder.parent_id == lib_file.folder_id, LibraryFolder.external_path == str(path.parent)
        )
    )
    if folder is None:
        folder = LibraryFolder(
            name=OUTPUT_DIR,
            parent_id=lib_file.folder_id,
            is_external=True,
            external_path=str(path.parent),
            external_readonly=True,
        )
        db.add(folder)
        await db.flush()
    return SavedOutput(path, folder.id, entry.name, entry.mtime, entry.size)


async def track_sliced(db: AsyncSession, new_file: LibraryFile, saved: SavedOutput, data: bytes) -> None:
    """The farm state of a saved output, as the next scan would record it (so it is seen as unchanged)."""
    entry = _DiskEntry(str(saved.path), "", saved.filename, "gcode.3mf", saved.mtime, saved.size)
    try:
        info = inspect_3mf(io.BytesIO(data))
    except CorruptFileError:
        info = None
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        analysis = _Analysis(sha256=zip_fingerprint(zf), info=info)
    state = FarmLibraryFile(
        watch_folder_id=await service.nas_folder_id(db), library_file_id=new_file.id, path="", mtime=0, size=0
    )
    _apply_detection(state, entry, analysis)
    db.add(state)


async def sync(db: AsyncSession, folder: LibraryFolder) -> dict:
    """Sync the NAS library folder. A NAS that can't be listed changes nothing; the error is reported."""
    active = await service.refresh_access(db)
    if active is None:
        result = {"status": "error", "error": "NAS not configured"}
    else:
        try:
            result = await _sync(db, folder, walk=partial(walk, active), analyze=partial(analyze, active))
        except Exception as exc:
            await db.rollback()
            logger.warning("NAS library sync failed: %s", exc)
            result = {"status": "error", "error": str(exc)[:500]}
    await service.store_result(db, result)
    return result
