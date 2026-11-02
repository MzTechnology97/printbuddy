"""One-way copy of the NAS subfolder into a local directory (size + mtime).

Only print files (same extensions as the library scan) are copied, never other
files that may sit in the share. The remote mtime is kept on the copy, so the
watched-folder sync sees real edit times (and its "saved < 1 minute ago" rule).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from backend.app.farm.library_sync import SETTLE_SECONDS, _file_type
from backend.app.farm.nas.remote import Remote, check_name

logger = logging.getLogger(__name__)


class MirrorError(Exception):
    """Listing failed: nothing was deleted locally."""


def mirror(remote: Remote, base: tuple[str, ...], dest: Path) -> dict:
    """Bring ``dest`` in line with ``base`` on the share. Blocking; run in a thread."""
    now = time.time()
    counts = dict.fromkeys(("downloaded", "unchanged", "deleted", "pending", "unreadable"), 0)
    counts["bytes"] = 0
    wanted: set[Path] = set()

    # List everything first: a failed listing must not delete local copies.
    files: list[tuple[tuple[str, ...], int, float]] = []
    stack: list[tuple[str, ...]] = [()]
    while stack:
        rel = stack.pop()
        try:
            entries = remote.scandir(base + rel)
        except Exception as exc:
            raise MirrorError(f"Cannot list {'/'.join(base + rel)}: {exc}") from exc
        for e in entries:
            if e.name.startswith("."):
                continue
            try:
                parts = rel + (check_name(e.name),)
            except ValueError:
                continue
            if e.is_dir:
                stack.append(parts)
            elif _file_type(Path(e.name)) is not None:
                files.append((parts, e.size, e.mtime))

    dest.mkdir(parents=True, exist_ok=True)
    for parts, size, mtime in files:
        local = dest.joinpath(*parts)
        wanted.add(local)
        try:
            st = local.stat()
            if st.st_size == size and abs(st.st_mtime - mtime) < 1:
                counts["unchanged"] += 1
                continue
        except FileNotFoundError:
            pass
        if now - mtime < SETTLE_SECONDS:
            counts["pending"] += 1  # being saved: keep the previous copy, retry next round
            continue
        local.parent.mkdir(parents=True, exist_ok=True)
        tmp = local.with_name(f".{local.name}.part")
        try:
            with open(tmp, "wb") as fh:
                remote.copy_to(base + parts, fh)
            os.utime(tmp, (mtime, mtime))
            os.replace(tmp, local)
        except Exception as exc:
            tmp.unlink(missing_ok=True)
            counts["unreadable"] += 1
            logger.warning("NAS mirror: cannot copy %s: %s", "/".join(parts), exc)
            continue
        counts["downloaded"] += 1
        counts["bytes"] += size

    # Remove local copies of files gone from the share, deepest dirs last.
    for root, dirnames, filenames in os.walk(dest, topdown=False):
        for name in filenames:
            path = Path(root) / name
            if path not in wanted:
                path.unlink(missing_ok=True)
                counts["deleted"] += not name.startswith(".")
        for name in dirnames:
            try:
                (Path(root) / name).rmdir()  # only succeeds when empty
            except OSError:
                pass
    return counts
