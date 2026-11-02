"""Hash-aware sync of watched external folders (the NAS library).

Compared with the upstream scan (``POST /library/folders/{id}/scan``), a watched
folder also:

- refreshes files edited in place (same ``LibraryFile`` id, new hash/metadata/thumbnail);
- relinks moved or renamed files by SHA-256, so queue items keep their file;
- keeps vanished files as ``missing`` instead of deleting their row;
- ignores files modified in the last minute (save in progress);
- marks broken 3MF files ``corrupt`` instead of importing them;
- runs periodically (``FarmWatchedFolder.scan_interval_seconds``).

Folders that aren't watched keep the upstream behaviour unchanged.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import library as lib
from backend.app.core import database
from backend.app.farm.detect import CorruptFileError, ProjectInfo, inspect_3mf
from backend.app.farm.models import FarmLibraryFile, FarmWatchedFolder
from backend.app.models.library import LibraryFile, LibraryFolder

logger = logging.getLogger(__name__)

SETTLE_SECONDS = 60
SCHEDULER_TICK_SECONDS = 30

_folder_locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)


@dataclass(slots=True)
class _DiskEntry:
    path: str
    rel_dir: str
    filename: str
    file_type: str
    mtime: float
    size: int


@dataclass(slots=True)
class _Analysis:
    sha256: str
    corrupt: str | None = None
    info: ProjectInfo | None = None
    metadata: dict | None = None
    thumbnail_path: str | None = None


def _file_type(filepath: Path) -> str | None:
    """Same extension rules as the upstream scan."""
    ext = filepath.suffix.lower()
    if ext not in lib._SCANNABLE_EXTENSIONS:
        compound = "".join(filepath.suffixes[-2:]).lower() if len(filepath.suffixes) >= 2 else ""
        if compound not in lib._SCANNABLE_EXTENSIONS:
            return None
    file_type = ext[1:] if ext else "unknown"
    if file_type == "3mf" and len(filepath.suffixes) >= 2 and filepath.suffixes[-2].lower() == ".gcode":
        file_type = "gcode.3mf"
    return file_type


def _walk(root: Path, show_hidden: bool) -> tuple[list[_DiskEntry], set[str]]:
    entries: list[_DiskEntry] = []
    seen_dirs: set[str] = set()
    real_root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        if not show_hidden:
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        rel_dir = str(Path(dirpath).relative_to(root))
        rel_dir = "" if rel_dir == "." else rel_dir
        seen_dirs.add(rel_dir)
        for filename in filenames:
            if not show_hidden and filename.startswith("."):
                continue
            filepath = Path(dirpath) / filename
            file_type = _file_type(filepath)
            if file_type is None:
                continue
            try:
                filepath.resolve().relative_to(real_root)  # symlink must stay inside the share
                stat = filepath.stat()
            except (ValueError, OSError):
                continue
            entries.append(_DiskEntry(str(filepath), rel_dir, filename, file_type, stat.st_mtime, stat.st_size))
    return entries, seen_dirs


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _analyze(entry: _DiskEntry) -> _Analysis:
    """Blocking per-file work (hash, integrity, metadata, thumbnail); run in a thread."""
    path = Path(entry.path)
    result = _Analysis(sha256=_sha256(path))
    if entry.file_type in ("3mf", "gcode.3mf"):
        try:
            result.info = inspect_3mf(path)
        except CorruptFileError as exc:
            result.corrupt = str(exc)[:255]
            return result
    thumbs_dir = lib.get_library_thumbnails_dir()
    if entry.file_type == "3mf":
        try:
            raw = lib.ThreeMFParser(str(path)).parse() or {}
            thumb = raw.get("_thumbnail_data")
            if thumb:
                thumb_full = thumbs_dir / f"{os.urandom(16).hex()}{raw.get('_thumbnail_ext', '.png')}"
                thumb_full.write_bytes(thumb)
                result.thumbnail_path = lib.to_relative_path(thumb_full)
            result.metadata = lib._without_print_name(lib._clean_3mf_metadata(raw))
        except Exception as exc:  # metadata is optional, like in the upstream scan
            logger.debug("Failed to extract metadata from external 3mf %s: %s", path, exc)
    elif entry.file_type == "gcode":
        result.metadata = lib.extract_gcode_file_metadata(path) or None
        thumb = lib.extract_gcode_thumbnail(path)
        if thumb:
            thumb_full = thumbs_dir / f"{os.urandom(16).hex()}.png"
            thumb_full.write_bytes(thumb)
            result.thumbnail_path = lib.to_relative_path(thumb_full)
    elif path.suffix.lower() in lib.IMAGE_EXTENSIONS:
        created = lib.create_image_thumbnail(path, thumbs_dir)
        if created:
            result.thumbnail_path = lib.to_relative_path(Path(created))
    return result


async def _try_analyze(entry: _DiskEntry) -> _Analysis | None:
    """None when the share fails to read the file (EIO on SMB): retried next scan, never marked corrupt."""
    try:
        return await asyncio.to_thread(_analyze, entry)
    except OSError as exc:
        logger.warning("Farm sync: cannot read %s, retrying next scan: %s", entry.path, exc)
        return None


def _drop_thumbnail(rel: str | None) -> None:
    abs_thumb = lib.to_absolute_path(rel)
    try:
        if abs_thumb and abs_thumb.exists():
            abs_thumb.unlink()
    except OSError:
        pass


def _apply_detection(state: FarmLibraryFile, entry: _DiskEntry, analysis: _Analysis) -> None:
    state.path = entry.path
    state.mtime = entry.mtime
    state.size = entry.size
    state.sha256 = analysis.sha256
    state.status = "corrupt" if analysis.corrupt else "ok"
    state.status_detail = analysis.corrupt
    state.checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if analysis.info:
        state.application = analysis.info.application
        state.slicer_detected = analysis.info.slicer
        state.printer_model_raw = analysis.info.printer_model_raw
        state.printer_model = analysis.info.printer_model


async def get_watch(db: AsyncSession, folder_id: int) -> FarmWatchedFolder | None:
    return await db.get(FarmWatchedFolder, folder_id)


async def sync_if_watched(db: AsyncSession, folder: LibraryFolder) -> dict | None:
    """Hook for the upstream scan endpoint: farm sync for watched folders, else None."""
    if await get_watch(db, folder.id) is None:
        return None
    return await sync_external_folder(db, folder)


async def sync_external_folder(db: AsyncSession, folder: LibraryFolder) -> dict:
    async with _folder_locks[folder.id]:
        return await _sync(db, folder)


async def _sync(db: AsyncSession, folder: LibraryFolder) -> dict:
    root = Path(folder.external_path or "")
    if not folder.is_external or not root.is_dir():
        raise FileNotFoundError(f"External path is not accessible: {folder.external_path}")
    now = time.time()
    entries, seen_dirs = await asyncio.to_thread(_walk, root, folder.external_show_hidden)

    # Subfolder tree of this external folder: relative dir -> folder id.
    ext_folders = (await db.execute(select(LibraryFolder).where(LibraryFolder.is_external.is_(True)))).scalars().all()
    children: defaultdict[int, list[LibraryFolder]] = defaultdict(list)
    for f in ext_folders:
        if f.parent_id is not None:
            children[f.parent_id].append(f)
    folder_cache: dict[str, int] = {"": folder.id}
    stack = [folder.id]
    while stack:
        for child in children[stack.pop()]:
            stack.append(child.id)
            try:
                rel = str(Path(child.external_path or "").relative_to(root))
            except ValueError:
                continue
            if rel != ".":
                folder_cache[rel] = child.id
    folder_ids = list(folder_cache.values())

    async def folder_for(rel_dir: str) -> int:
        """Folder id for a relative dir, creating the chain like the upstream scan."""
        if rel_dir in folder_cache:
            return folder_cache[rel_dir]
        parent, current = folder.id, ""
        for part in Path(rel_dir).parts:
            current = str(Path(current) / part) if current else part
            if current not in folder_cache:
                sub = LibraryFolder(
                    name=part,
                    parent_id=parent,
                    is_external=True,
                    external_path=str(root / current),
                    external_readonly=folder.external_readonly,
                    external_show_hidden=folder.external_show_hidden,
                )
                db.add(sub)
                await db.flush()
                folder_cache[current] = sub.id
            parent = folder_cache[current]
        return parent

    files = (
        (
            await db.execute(
                LibraryFile.active().where(LibraryFile.folder_id.in_(folder_ids), LibraryFile.is_external.is_(True))
            )
        )
        .scalars()
        .all()
    )
    by_path = {f.file_path: f for f in files}
    file_ids = {f.id for f in files}
    states = (
        (await db.execute(select(FarmLibraryFile).where(FarmLibraryFile.watch_folder_id == folder.id))).scalars().all()
    )
    state_by_file: dict[int, FarmLibraryFile] = {}
    corrupt_by_path: dict[str, FarmLibraryFile] = {}
    for st in states:
        if st.library_file_id is None:
            corrupt_by_path[st.path] = st
        elif st.library_file_id in file_ids:
            state_by_file[st.library_file_id] = st
        else:
            await db.delete(st)  # library row deleted by the user (SQLite doesn't cascade)

    def new_state(**kwargs) -> FarmLibraryFile:
        st = FarmLibraryFile(watch_folder_id=folder.id, **kwargs)
        db.add(st)
        return st

    counts = dict.fromkeys(("added", "updated", "relinked", "missing", "corrupt", "pending", "unreadable"), 0)
    disk_paths = {e.path for e in entries}
    fresh: list[_DiskEntry] = []
    touched = False

    for entry in entries:
        settled = now - entry.mtime >= SETTLE_SECONDS
        lib_file = by_path.get(entry.path)
        if lib_file is None:
            if not settled:
                counts["pending"] += 1
                continue
            known_bad = corrupt_by_path.get(entry.path)
            if known_bad and known_bad.mtime == entry.mtime and known_bad.size == entry.size:
                counts["corrupt"] += 1
                continue
            fresh.append(entry)
            continue

        st = state_by_file.get(lib_file.id)
        if st and st.status != "missing" and st.mtime == entry.mtime and st.size == entry.size:
            if st.status == "corrupt":
                counts["corrupt"] += 1
            continue
        if not settled:
            counts["pending"] += 1
            continue
        analysis = await _try_analyze(entry)
        if analysis is None:
            counts["unreadable"] += 1
            continue
        if st is None:
            st = state_by_file[lib_file.id] = new_state(library_file_id=lib_file.id, path=entry.path, mtime=0, size=0)
        elif not analysis.corrupt:
            counts["updated"] += 1
        _apply_detection(st, entry, analysis)
        if analysis.corrupt:
            # Keep the last good metadata; the file is flagged until it's saved again.
            counts["corrupt"] += 1
            continue
        _drop_thumbnail(lib_file.thumbnail_path)
        lib_file.file_type = entry.file_type
        lib_file.file_size = entry.size
        lib_file.file_hash = analysis.sha256
        lib_file.thumbnail_path = analysis.thumbnail_path
        lib_file.file_metadata = analysis.metadata
        touched = True

    # Analyse new paths first: their hashes decide which missing files were moved.
    candidates: defaultdict[str, list[tuple[_DiskEntry, _Analysis]]] = defaultdict(list)
    for entry in fresh:
        analysis = await _try_analyze(entry)
        if analysis is None:
            counts["unreadable"] += 1
        elif analysis.corrupt:
            st = corrupt_by_path.get(entry.path) or new_state(path=entry.path, mtime=0, size=0)
            _apply_detection(st, entry, analysis)
            corrupt_by_path[entry.path] = st
            counts["corrupt"] += 1
        else:
            candidates[analysis.sha256].append((entry, analysis))

    for lib_file in files:
        if lib_file.file_path in disk_paths:
            continue
        st = state_by_file.get(lib_file.id)
        matches = candidates.get(st.sha256) if st and st.sha256 else None
        if matches:
            pick = next((m for m in matches if m[0].filename == lib_file.filename), matches[0])
            matches.remove(pick)
            entry, analysis = pick
            _drop_thumbnail(analysis.thumbnail_path)  # same bytes: keep the existing thumbnail
            lib_file.file_path = entry.path
            lib_file.filename = entry.filename
            lib_file.folder_id = await folder_for(entry.rel_dir)
            _apply_detection(st, entry, analysis)
            counts["relinked"] += 1
            continue
        if st is None:
            st = new_state(library_file_id=lib_file.id, path=lib_file.file_path, mtime=0, size=lib_file.file_size)
        st.status = "missing"
        st.status_detail = None
        counts["missing"] += 1

    for group in candidates.values():
        for entry, analysis in group:
            lib_file = LibraryFile(
                folder_id=await folder_for(entry.rel_dir),
                is_external=True,
                filename=entry.filename,
                file_path=entry.path,
                file_type=entry.file_type,
                file_size=entry.size,
                file_hash=analysis.sha256,
                thumbnail_path=analysis.thumbnail_path,
                file_metadata=analysis.metadata,
            )
            db.add(lib_file)
            await db.flush()
            st = corrupt_by_path.pop(entry.path, None) or new_state(path=entry.path, mtime=0, size=0)
            st.library_file_id = lib_file.id
            _apply_detection(st, entry, analysis)
            counts["added"] += 1
            touched = True

    # Corrupt files that disappeared from disk.
    for path, st in corrupt_by_path.items():
        if path not in disk_paths:
            await db.delete(st)

    await _remove_vanished_subfolders(db, folder.id, folder_cache, seen_dirs)

    watch = await get_watch(db, folder.id)
    if watch is not None:
        watch.last_scan_at = datetime.now(timezone.utc).replace(tzinfo=None)
        watch.last_result = counts
    await db.commit()

    if touched:
        asyncio.create_task(
            lib._backfill_external_stl_thumbnails(list(set(folder_cache.values()))),
            name=f"stl-backfill-folder-{folder.id}",
        )
    # "removed" keeps the upstream response shape; watched folders never delete rows.
    return {"status": "success", "removed": 0, **counts}


async def _remove_vanished_subfolders(
    db: AsyncSession, root_id: int, folder_cache: dict[str, int], seen_dirs: set[str]
) -> None:
    """Drop empty subfolders whose directory is gone, deepest first (as upstream)."""
    await db.flush()
    gone = sorted(
        ((rel, fid) for rel, fid in folder_cache.items() if rel and fid != root_id and rel not in seen_dirs),
        key=lambda item: len(Path(item[0]).parts),
        reverse=True,
    )
    for _rel, fid in gone:
        n_files = await db.scalar(select(func.count(LibraryFile.id)).where(LibraryFile.folder_id == fid))
        n_children = await db.scalar(select(func.count(LibraryFolder.id)).where(LibraryFolder.parent_id == fid))
        if not n_files and not n_children:
            sub = await db.get(LibraryFolder, fid)
            if sub is not None:
                await db.delete(sub)
                await db.flush()


class FarmLibraryScheduler:
    """Periodic sync of watched folders. inotify is unreliable on SMB/NFS, so we poll."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="farm-library-sync")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(SCHEDULER_TICK_SECONDS)
                await self.run_due()
            except asyncio.CancelledError:
                break
            except Exception:  # pragma: no cover - defensive, keep the loop alive
                logger.exception("Farm library sync loop failed")

    async def run_due(self) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        async with database.async_session() as db:
            watches = (
                (await db.execute(select(FarmWatchedFolder).where(FarmWatchedFolder.scan_interval_seconds > 0)))
                .scalars()
                .all()
            )
            for watch in watches:
                if watch.last_scan_at and (now - watch.last_scan_at).total_seconds() < watch.scan_interval_seconds:
                    continue
                folder = await db.get(LibraryFolder, watch.folder_id)
                if folder is None:
                    continue
                try:
                    await sync_external_folder(db, folder)
                except Exception as exc:
                    await db.rollback()
                    logger.warning("Farm sync of folder %s failed: %s", watch.folder_id, exc)


farm_library_scheduler = FarmLibraryScheduler()
