"""Containment-checked path joining (ported from Bambuddy 0.2.4.5).

Use ``safe_join_under`` whenever a path part comes from a request body, a ZIP
``namelist()``, an upload filename, a printer's FTP listing or anything else not
under our control. ``Path("/lib") / "/etc"`` is ``Path("/etc")``, and ``..``
walks out of the parent, so a plain join is not enough.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException


class PathTraversalError(ValueError):
    """A join would leave the trusted parent directory."""


def safe_join_under(parent: Path, *parts: str, http: bool = True) -> Path:
    """Join ``parts`` under ``parent``; return the resolved path, or refuse if it leaves ``parent``.

    Refuses empty, non-str, NUL-containing and absolute parts, and anything that
    resolves outside ``parent`` (``..``, drive letters, symlinks). Raises
    ``HTTPException(400)`` by default, ``PathTraversalError`` with ``http=False``.
    """
    if not parts:
        _fail("no path parts", http)
    for part in parts:
        if not isinstance(part, str) or not part:
            _fail("empty or non-string path part", http)
        if "\x00" in part:
            _fail("NUL byte in path part", http)
        if part.startswith(("/", "\\")):
            _fail("absolute path part", http)
    return assert_under(parent, parent.joinpath(*parts), http=http)


def assert_under(parent: Path, candidate: Path, *, http: bool = True) -> Path:
    """Return ``candidate`` resolved, refusing it unless it lies under ``parent``."""
    resolved = candidate.resolve()
    if not resolved.is_relative_to(parent.resolve()):
        _fail("path escapes the parent directory", http)
    return resolved


def _fail(reason: str, http: bool) -> None:
    if http:
        raise HTTPException(status_code=400, detail="Invalid path in upload")
    raise PathTraversalError(reason)
