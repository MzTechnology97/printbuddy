"""Local copies of NAS files, made only when a file is used; evicted LRU over a size limit.

Library rows of NAS files point into the cache root, so upstream code that opens
``LibraryFile.file_path`` works once ``ensure_local`` has run. That happens on use
only: slicing, printing (queue and direct), and the library routes that need the
whole file (download, plates, G-code preview...). The scan never downloads.

On a cached copy, mtime is the NAS mtime (to tell a stale copy) and atime is the
last use, set explicitly (so ``noatime`` mounts don't matter).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import database
from backend.app.core.auth import is_auth_enabled
from backend.app.core.config import settings as app_settings
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.nas import access, service
from backend.app.farm.nas.remote import with_retry
from backend.app.models.library import LibraryFile

logger = logging.getLogger(__name__)

# A file used in the last hour is never evicted: it may be uploading to a printer.
KEEP_SECONDS = 3600

_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


def _fresh(path: Path, size: int, mtime: float) -> bool:
    """Whether the cached copy matches the NAS version; marks it used."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return False
    if st.st_size != size or abs(st.st_mtime - mtime) >= 1:
        return False
    os.utime(path, (time.time(), st.st_mtime))
    return True


def _fetch(remote, parts: tuple[str, ...], tmp: Path) -> None:
    with open(tmp, "wb") as fh:  # reopened on retry: never append to a partial copy
        remote.copy_to(parts, fh)


def _download(active: access.Active, parts: tuple[str, ...], path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.part")
    try:
        with_retry(_fetch, active.remote, parts, tmp)
        os.utime(tmp, (time.time(), mtime))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def usage(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0


def evict(root: Path, limit_bytes: int) -> int:
    """Delete least recently used copies until the cache fits; returns bytes freed."""
    files = []
    for p in root.rglob("*"):
        try:
            if p.is_file():
                st = p.stat()
                files.append((st.st_atime, st.st_size, p))
        except OSError:
            continue
    total = sum(size for _, size, _ in files)
    freed = 0
    cutoff = time.time() - KEEP_SECONDS
    for atime, size, p in sorted(files):
        if total - freed <= limit_bytes or atime > cutoff:
            break
        p.unlink(missing_ok=True)
        freed += size
    if freed:
        logger.info("NAS cache: evicted %d bytes (limit %d)", freed, limit_bytes)
    return freed


async def ensure_local(db: AsyncSession, lib_file: LibraryFile) -> bool:
    """Make sure a NAS file is in the cache. False when it isn't a NAS file or can't be fetched
    (the caller then reports the file as not found, as it would anyway)."""
    try:
        active = await service.refresh_access(db)
        parts = active.parts(lib_file.file_path) if active else None
        if parts is None:
            return False
        state = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == lib_file.id))
        if state is None or state.status != "ok":
            return False  # gone from the NAS or corrupt
        path = access.library_path(lib_file.file_path)
        async with _locks[str(path)]:
            if not await asyncio.to_thread(_fresh, path, state.size, state.mtime):
                await asyncio.to_thread(_download, active, parts, path, state.mtime)
        cfg = await service.load(db)
        await asyncio.to_thread(evict, active.cache_root, cfg.cache_mb * 1024 * 1024)
        return True
    except Exception as exc:
        logger.warning("NAS cache: cannot fetch %s: %s", lib_file.filename, exc)
        return False


async def ensure_local_by_id(file_id: int, has_credentials: bool) -> None:
    async with database.async_session() as db:
        # This runs before the route's own auth check: anonymous requests must not trigger downloads.
        if not has_credentials and await is_auth_enabled(db):
            return
        lib_file = await db.get(LibraryFile, file_id)
        if lib_file is not None and lib_file.is_external:
            await ensure_local(db, lib_file)


# Upstream library routes that open the whole file (not the listing, details or thumbnail).
_NEEDS_FILE = re.compile(
    rf"^{re.escape(app_settings.api_prefix)}/library/files/(\d+)/"
    r"(?:plates|plate-thumbnail/|filament-requirements|slice|print|source-snapshot|source-assets/|download|dl/|gcode)"
)


class NasCacheMiddleware:
    """Fetch a NAS file into the cache before the upstream route that reads it runs."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http" and (m := _NEEDS_FILE.match(scope["path"])):
            headers = dict(scope.get("headers") or [])
            has_credentials = (
                b"authorization" in headers
                or b"x-api-key" in headers
                or b"cookie" in headers
                or b"token" in (scope.get("query_string") or b"")
                or "/dl/" in scope["path"]  # slicer download links carry their own token
            )
            try:
                await ensure_local_by_id(int(m[1]), has_credentials)
            except Exception as exc:  # the route itself reports a missing file
                logger.warning("NAS cache: prefetch for %s failed: %s", scope["path"], exc)
        await self.app(scope, receive, send)
