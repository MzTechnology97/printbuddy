"""Read-only access to the NAS share: path rules and the SMB implementation.

Security rules, enforced here and nowhere else:
- nothing on the NAS is ever deleted; files are written under a temporary dot name and
  then renamed. Library uploads never replace a file; a slicing output replaces only the
  previous output of the same project plate (in the project's ``Printbuddy`` folder);
- paths are lists of plain names: no separators, no ``.``/``..``, so nothing can
  climb out of the folder a caller is scoped to;
- the share root may hold credential files: callers list its *directories* only
  (``list_dirs``), and mirror only inside the configured subfolder.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import BinaryIO, Protocol, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")

_FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$")


class NasPathError(ValueError):
    """A path component that could escape the configured folder."""


class NasConnectionError(ConnectionError):
    """The connection to the NAS failed or was dropped (worth retrying), as opposed to a per-file error."""


class NasFileError(OSError):
    """The NAS refused or failed one path; not retried, the file is tried again next scan."""


class NasBusyError(OSError):
    """The shared session stayed busy too long; not retried (the caller tries again later)."""


# An SMB call that takes longer than this has its connection dropped (smbprotocol itself waits forever).
OP_TIMEOUT = 120
LOCK_TIMEOUT = OP_TIMEOUT + 30


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
    def read(self, parts: tuple[str, ...], fn: Callable[[BinaryIO], T]) -> T: ...
    def copy_to(self, parts: tuple[str, ...], dest: BinaryIO) -> None: ...
    def makedirs(self, parts: tuple[str, ...]) -> None: ...
    def write(self, parts: tuple[str, ...], data: bytes) -> None: ...
    def rename(self, src: tuple[str, ...], dst: tuple[str, ...]) -> None: ...
    def replace(self, src: tuple[str, ...], dst: tuple[str, ...]) -> None: ...
    def close(self) -> None: ...


# A NAS that drops the connection (it did after an I/O error on one file) may refuse
# new ones for a few seconds: wait and retry instead of failing every later file.
RETRY_DELAYS = (2, 5, 15)


def with_retry(fn: Callable[..., T], *args) -> T:
    """Call ``fn``, retrying connection errors with backoff (per-file server errors are not retried)."""
    for delay in (*RETRY_DELAYS, None):
        try:
            return fn(*args)
        except ConnectionError as exc:
            if delay is None:
                raise
            logger.info("NAS: connection lost (%s), retrying in %ss", exc, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def list_dirs(remote: Remote, parts: tuple[str, ...]) -> list[str]:
    """Directory names only (file names are never returned, not even in the share root)."""
    return sorted(e.name for e in remote.scandir(parts) if e.is_dir and not e.name.startswith("."))


class SmbRemote:
    """SMB2/3 client with its own connection cache, so credentials aren't pooled globally."""

    def __init__(self, host: str, share: str, username: str, password: str, timeout: int = 15) -> None:
        if not HOST_RE.match(host):
            raise NasPathError(f"Invalid host: {host!r}")
        self._root = f"\\\\{host}\\{check_name(share)}"
        self._lock = threading.Lock()  # one shared session: one request at a time
        self._cache: dict = {}
        self._kwargs = {
            "username": username or None,
            "password": password or None,
            "connection_cache": self._cache,
            "connection_timeout": timeout,
        }

    def _unc(self, parts: tuple[str, ...]) -> str:
        return "\\".join([self._root, *(check_name(p) for p in parts)])

    @contextmanager
    def _session(self):
        """One request at a time on the shared session, never waiting forever.

        smbprotocol waits for a response with no time limit, so a request the NAS never answers
        would hold the session (and every caller queued behind it) for good. A watchdog drops the
        connection after ``OP_TIMEOUT``: the stuck request fails, is retried on a new connection,
        and the session is free again. Waiting for the session itself gives up after ``LOCK_TIMEOUT``.
        """
        if not self._lock.acquire(timeout=LOCK_TIMEOUT):
            raise NasBusyError(f"NAS busy: a request has held the connection for over {LOCK_TIMEOUT}s")
        watchdog = threading.Timer(OP_TIMEOUT, self._drop, args=(f"no answer in {OP_TIMEOUT}s",))
        watchdog.daemon = True
        watchdog.start()
        try:
            with self._connection_errors():
                yield
        finally:
            watchdog.cancel()
            self._lock.release()

    def _drop(self, reason: str) -> None:
        """Close the sockets without a polite logoff (it would wait on the server too): every
        request still waiting is woken with an error, and the next one opens a new connection."""
        if reason:
            logger.warning("NAS: dropping the connection (%s)", reason)
        for conn in list(self._cache.values()):
            try:
                conn.disconnect(False)
            except Exception:  # pragma: no cover - best effort on a dead connection
                logger.debug("NAS: closing a connection failed", exc_info=True)
        self._cache.clear()

    @staticmethod
    @contextmanager
    def _connection_errors():
        from smbprotocol.exceptions import SMBConnectionClosed, SMBException, SMBResponseException

        try:
            yield
        except NasPathError:
            raise
        except SMBResponseException as exc:
            # The server answered: a problem with this path (e.g. a disk I/O error), not the connection.
            raise NasFileError(str(exc)) from exc
        except (SMBConnectionClosed, SMBException, ConnectionError, TimeoutError, ValueError) as exc:
            # smbprotocol reports a refused/failed connect as ValueError("Failed to connect ...");
            # a connection dropped by the watchdog surfaces as SMBConnectionClosed / SMBException.
            raise NasConnectionError(str(exc)) from exc

    def scandir(self, parts: tuple[str, ...]) -> list[RemoteEntry]:
        import smbclient

        entries = []
        with self._session():
            for e in smbclient.scandir(self._unc(parts), **self._kwargs):
                if e.is_symlink():
                    continue  # reparse points could lead outside the folder
                st = e.stat()
                entries.append(RemoteEntry(e.name, e.is_dir(), st.st_size, st.st_mtime))
        return entries

    def read(self, parts: tuple[str, ...], fn: Callable[[BinaryIO], T]) -> T:
        """Run ``fn`` on the open remote file: seekable, so zip entries are read without the whole file."""
        import smbclient

        with (
            self._session(),
            smbclient.open_file(self._unc(parts), mode="rb", share_access="rwd", **self._kwargs) as src,
        ):
            return fn(src)

    def copy_to(self, parts: tuple[str, ...], dest: BinaryIO) -> None:
        self.read(parts, lambda src: shutil.copyfileobj(src, dest, 1024 * 1024))

    def makedirs(self, parts: tuple[str, ...]) -> None:
        import smbclient

        with self._session():
            smbclient.makedirs(self._unc(parts), exist_ok=True, **self._kwargs)

    def write(self, parts: tuple[str, ...], data: bytes) -> None:
        """Create or overwrite ``parts``: callers only ever write their own temporary (dot) file."""
        import smbclient

        with (
            self._session(),
            smbclient.open_file(self._unc(parts), mode="wb", **self._kwargs) as dst,
        ):
            dst.write(data)

    def rename(self, src: tuple[str, ...], dst: tuple[str, ...]) -> None:
        """Fails if ``dst`` exists: nothing on the NAS is replaced this way."""
        import smbclient

        with self._session():
            smbclient.rename(self._unc(src), self._unc(dst), **self._kwargs)

    def replace(self, src: tuple[str, ...], dst: tuple[str, ...]) -> None:
        """Rename over ``dst``: only for the farm's own slicing outputs, which a new slice replaces."""
        import smbclient

        with self._session():
            smbclient.replace(self._unc(src), self._unc(dst), **self._kwargs)

    def close(self) -> None:
        self._drop("")
