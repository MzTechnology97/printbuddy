"""The one SMB session to the configured NAS folder, and reads through it.

Library rows of NAS files store a path under ``cache_root``: the file is there only
once it has been used (see ``cache.py``). Reads that need a few zip entries (scan,
plates) go straight to the NAS instead, so nothing is downloaded for them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import BinaryIO, TypeVar

from backend.app.core.config import settings as app_settings
from backend.app.farm.nas.remote import Remote, with_retry

T = TypeVar("T")

# SMB calls run in their own small pool, not in the default one that printer polling also uses:
# a NAS that stops answering can hold these threads, never the rest of Printbuddy.
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="nas")


async def run(fn: Callable[..., T], *args) -> T:
    """``asyncio.to_thread`` for anything that may talk to the NAS."""
    return await asyncio.get_running_loop().run_in_executor(_executor, partial(fn, *args))


def library_path(path: str | Path) -> Path:
    """Absolute path of a ``LibraryFile.file_path``.

    An upstream startup migration strips the data dir from every library path under it,
    so NAS rows (cached under ``<data>/farm_nas/cache``) come back relative after a restart.
    """
    p = Path(path)
    return p if p.is_absolute() else Path(app_settings.base_dir) / p


@dataclass(frozen=True, slots=True)
class Active:
    key: tuple  # host, share, user, password, path: a change opens a new session
    remote: Remote
    base: tuple[str, ...]  # the configured subfolder on the share
    cache_root: Path

    def parts(self, path: str | Path) -> tuple[str, ...] | None:
        """Share path of a library path under the cache root, else None (not a NAS file)."""
        try:
            rel = library_path(path).relative_to(self.cache_root)
        except ValueError:
            return None
        return self.base + rel.parts


_active: Active | None = None


def current() -> Active | None:
    return _active


def activate(new: Active | None) -> None:
    global _active
    old, _active = _active, new
    if old is not None and (new is None or old.remote is not new.remote):
        try:
            old.remote.close()
        except Exception:  # pragma: no cover - best effort on a dead connection
            pass


def read_file(path: str | Path, fn: Callable[[BinaryIO], T]) -> T:
    """Run ``fn`` on the file: from the NAS for NAS files (never a stale cached copy), else from disk."""
    active = _active
    parts = active.parts(path) if active else None
    if parts is None:
        with open(library_path(path), "rb") as fh:
            return fn(fh)
    return with_retry(active.remote.read, parts, fn)
