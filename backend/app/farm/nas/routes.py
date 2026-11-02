"""Settings API for the NAS share. The password is write-only: responses say only whether it is set."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.library_sync import _file_type, get_watch, sync_external_folder
from backend.app.farm.nas import access, cache, service
from backend.app.farm.nas.remote import HOST_RE, NasPathError, list_dirs, split_path
from backend.app.models.library import LibraryFolder

router = APIRouter(prefix="/nas", tags=["farm"])  # mounted under /farm

_read = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_READ))
_write = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_UPDATE))


class Connection(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    share: str = Field(min_length=1, max_length=80)
    username: str = Field(default="", max_length=100)
    # None = use the stored password (lets the UI test/browse without retyping it).
    password: str | None = Field(default=None, max_length=256)


class NasSettingsIn(Connection):
    path: str = Field(min_length=1, max_length=500)
    scan_interval_seconds: int = Field(default=300, ge=0, le=86400)
    cache_mb: int = Field(default=service.DEFAULT_CACHE_MB, ge=256, le=1024 * 1024)


class BrowseIn(Connection):
    path: str = Field(default="", max_length=500)


async def _view(db: AsyncSession) -> dict:
    cfg = await service.load(db)
    watch = await get_watch(db, cfg.folder_id) if cfg.folder_id else None
    return {
        "host": cfg.host,
        "share": cfg.share,
        "path": cfg.path,
        "username": cfg.username,
        "password_set": bool(cfg.password),
        "scan_interval_seconds": watch.scan_interval_seconds if watch else 300,
        "folder_id": cfg.folder_id,
        "last_scan_at": watch.last_scan_at if watch else None,
        "last_result": cfg.last_result,
        "cache_mb": cfg.cache_mb,
        "cache_used_bytes": await asyncio.to_thread(cache.usage, service.cache_dir()),
    }


async def _password(db: AsyncSession, body: Connection) -> str:
    """The typed password, or the stored one only for the stored host/share/user.

    Otherwise anyone allowed to edit settings could point the host at their own
    server and collect the NAS login.
    """
    if body.password is not None:
        return body.password
    cfg = await service.load(db)
    if cfg.password and not cfg.same_target(body.host, body.share, body.username):
        raise HTTPException(status_code=400, detail="Host, share or user changed: type the password again")
    return cfg.password


def _check_host(host: str) -> str:
    if not HOST_RE.match(host.strip()):
        raise HTTPException(status_code=400, detail="Invalid host: use a name or an IPv4 address")
    return host.strip()


async def _with_remote(db: AsyncSession, body: Connection, action):
    password = await _password(db, body)
    host = _check_host(body.host)

    def run():
        remote = service.make_remote(host, body.share.strip(), body.username, password)
        try:
            return action(remote)
        finally:
            remote.close()

    try:
        return await access.run(run)
    except NasPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Connection/auth/permission problems: shown to the user as-is (no secrets in them).
        raise HTTPException(status_code=502, detail=f"NAS error: {exc}"[:500]) from exc


@router.get("")
async def get_nas_settings(db: AsyncSession = Depends(get_db), _=_read):
    return await _view(db)


@router.put("")
async def save_nas_settings(body: NasSettingsIn, db: AsyncSession = Depends(get_db), _=_write):
    try:
        await service.save(
            db,
            host=_check_host(body.host),
            share=body.share,
            path=body.path,
            username=body.username,
            password=body.password,
            interval=body.scan_interval_seconds,
            cache_mb=body.cache_mb,
        )
    except ValueError as exc:  # includes NasPathError
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await _view(db)


@router.post("/test")
async def test_nas(body: NasSettingsIn, db: AsyncSession = Depends(get_db), _=_write):
    """Connect and count the subfolder's top level (directories, print files)."""
    try:
        parts = split_path(body.path)
    except NasPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not parts:
        raise HTTPException(status_code=400, detail="A subfolder is required")

    def count(remote):
        entries = remote.scandir(parts)
        dirs = sum(e.is_dir for e in entries)
        files = sum(not e.is_dir and _file_type(Path(e.name)) is not None for e in entries)
        return {"ok": True, "directories": dirs, "print_files": files}

    return await _with_remote(db, body, count)


@router.post("/browse")
async def browse_nas(body: BrowseIn, db: AsyncSession = Depends(get_db), _=_write):
    """Directory names under ``path`` (share-relative), to pick the subfolder. Files are never listed."""
    try:
        parts = split_path(body.path)
    except NasPathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    dirs = await _with_remote(db, body, lambda remote: list_dirs(remote, parts))
    return {"path": "/".join(parts), "directories": dirs}


@router.post("/sync")
async def sync_nas_now(db: AsyncSession = Depends(get_db), _=_write):
    """Rescan the NAS folder now (listing and 3MF headers only: nothing is downloaded)."""
    cfg = await service.load(db)
    folder = await db.get(LibraryFolder, cfg.folder_id) if cfg.folder_id else None
    if not cfg.configured or folder is None:
        raise HTTPException(status_code=400, detail="NAS not configured")
    return await sync_external_folder(db, folder)
