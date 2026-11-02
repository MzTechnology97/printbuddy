"""Read-only access to the NAS share: path rules and the SMB implementation.

Security rules, enforced here and nowhere else:
- only reads (directory listings and file reads, never writes);
- paths are lists of plain names: no separators, no ``.``/``..``, so nothing can
  climb out of the folder a caller is scoped to;
- the share root may hold credential files: callers list its *directories* only
  (``list_dirs``), and mirror only inside the configured subfolder.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from typing import BinaryIO, Protocol

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$")


class NasPathError(ValueError):
    """A path component that could escape the configured folder."""


def check_name(name: str) -> str:
    if not name or name in (".", "..") or _FORBIDDEN.search(name) or name != name.strip():
        raise NasPathError(f"Invalid path component: {name!r}")
    return name


def split_path(path: str) -> tuple[str, ...]:
    """'Stampa 3D/Box' or 'Stampa 3D\\Box' -> ('Stampa 3D', 'Box'), validated."""
    return tuple(check_name(p) for p in re.split(r"[\\/]+", path.strip().strip("\\/")) if p)


@dataclass(slots=True)
class RemoteEntry:
    name: str
    is_dir: bool
    size: int
    mtime: float


class Remote(Protocol):
    def scandir(self, parts: tuple[str, ...]) -> list[RemoteEntry]: ...
    def copy_to(self, parts: tuple[str, ...], dest: BinaryIO) -> None: ...
    def close(self) -> None: ...


def list_dirs(remote: Remote, parts: tuple[str, ...]) -> list[str]:
    """Directory names only (file names are never returned, not even in the share root)."""
    return sorted(e.name for e in remote.scandir(parts) if e.is_dir and not e.name.startswith("."))


class SmbRemote:
    """SMB2/3 client with its own connection cache, so credentials aren't pooled globally."""

    def __init__(self, host: str, share: str, username: str, password: str, timeout: int = 15) -> None:
        if not HOST_RE.match(host):
            raise NasPathError(f"Invalid host: {host!r}")
        self._root = f"\\\\{host}\\{check_name(share)}"
        self._cache: dict = {}
        self._kwargs = {
            "username": username or None,
            "password": password or None,
            "connection_cache": self._cache,
            "connection_timeout": timeout,
        }

    def _unc(self, parts: tuple[str, ...]) -> str:
        return "\\".join([self._root, *(check_name(p) for p in parts)])

    def scandir(self, parts: tuple[str, ...]) -> list[RemoteEntry]:
        import smbclient

        entries = []
        for e in smbclient.scandir(self._unc(parts), **self._kwargs):
            if e.is_symlink():
                continue  # reparse points could lead outside the folder
            st = e.stat()
            entries.append(RemoteEntry(e.name, e.is_dir(), st.st_size, st.st_mtime))
        return entries

    def copy_to(self, parts: tuple[str, ...], dest: BinaryIO) -> None:
        import smbclient

        with smbclient.open_file(self._unc(parts), mode="rb", share_access="rwd", **self._kwargs) as src:
            shutil.copyfileobj(src, dest, 1024 * 1024)

    def close(self) -> None:
        import smbclient

        smbclient.reset_connection_cache(connection_cache=self._cache)
