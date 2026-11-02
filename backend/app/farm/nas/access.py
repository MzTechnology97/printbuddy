"""The one SMB session to the configured NAS folder, and reads through it.

Library rows of NAS files store a path under ``cache_root``: the file is there only
once it has been used (see ``cache.py``). Reads that need a few zip entries (scan,
plates) go straight to the NAS instead, so nothing is downloaded for them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, TypeVar

from backend.app.farm.nas.remote import Remote, with_retry

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Active:
    key: tuple  # host, share, user, password, path: a change opens a new session
    remote: Remote
    base: tuple[str, ...]  # the configured subfolder on the share
    cache_root: Path

    def parts(self, path: str | Path) -> tuple[str, ...] | None:
        """Share path of a library path under the cache root, else None (not a NAS file)."""
        try:
            rel = Path(path).relative_to(self.cache_root)
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
        with open(path, "rb") as fh:
            return fn(fh)
    return with_retry(active.remote.read, parts, fn)
