"""Farm API: watched NAS folders, per-file slicer, slicing with embedded settings."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.library_sync import sync_external_folder
from backend.app.farm.models import FarmLibraryFile, FarmWatchedFolder
from backend.app.farm.nas.routes import router as nas_router
from backend.app.farm.plates.routes import router as plates_router
from backend.app.models.library import LibraryFile, LibraryFolder

router = APIRouter(prefix="/farm", tags=["farm"])

_read = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ))
_write = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD))


class WatchRequest(BaseModel):
    # 0 = no periodic sync (manual scan only, still hash-aware).
    scan_interval_seconds: int = Field(default=300, ge=0, le=86400)


class SlicerOverrideRequest(BaseModel):
    slicer: Literal["auto", "orcaslicer", "bambu_studio"]


class FarmSliceRequest(BaseModel):
    plate: int | None = Field(default=None, ge=0)


def _watch_dict(w: FarmWatchedFolder) -> dict:
    return {
        "folder_id": w.folder_id,
        "scan_interval_seconds": w.scan_interval_seconds,
        "last_scan_at": w.last_scan_at,
        "last_result": w.last_result,
    }


def _state_dict(st: FarmLibraryFile) -> dict:
    return {
        "id": st.id,
        "library_file_id": st.library_file_id,
        "path": st.path,
        "status": st.status,
        "status_detail": st.status_detail,
        "sha256": st.sha256,
        "application": st.application,
        "slicer_detected": st.slicer_detected,
        "slicer_override": st.slicer_override,
        "slicer": st.slicer,
        "printer_model_raw": st.printer_model_raw,
        "printer_model": st.printer_model,
        "checked_at": st.checked_at,
    }


async def _external_folder(db: AsyncSession, folder_id: int) -> LibraryFolder:
    folder = await db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(status_code=404, detail="Folder not found")
    if not folder.is_external:
        raise HTTPException(status_code=400, detail="Not an external folder")
    return folder


async def _file_state(db: AsyncSession, library_file_id: int) -> FarmLibraryFile:
    st = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == library_file_id))
    if st is None:
        raise HTTPException(status_code=404, detail="File is not on a watched folder (or not scanned yet)")
    return st


async def _run_sync(db: AsyncSession, folder: LibraryFolder) -> dict:
    try:
        return await sync_external_folder(db, folder)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/library/folders")
async def list_watched_folders(db: AsyncSession = Depends(get_db), _=_read):
    return [_watch_dict(w) for w in (await db.execute(select(FarmWatchedFolder))).scalars().all()]


@router.put("/library/folders/{folder_id}")
async def watch_folder(folder_id: int, body: WatchRequest, db: AsyncSession = Depends(get_db), _=_write):
    """Turn an external folder into a farm-synced one (or change its interval)."""
    await _external_folder(db, folder_id)
    watch = await db.get(FarmWatchedFolder, folder_id)
    if watch is None:
        watch = FarmWatchedFolder(folder_id=folder_id)
        db.add(watch)
    watch.scan_interval_seconds = body.scan_interval_seconds
    await db.commit()
    return _watch_dict(watch)


@router.delete("/library/folders/{folder_id}")
async def unwatch_folder(folder_id: int, db: AsyncSession = Depends(get_db), _=_write):
    watch = await db.get(FarmWatchedFolder, folder_id)
    if watch is None:
        raise HTTPException(status_code=404, detail="Folder is not watched")
    states = (await db.execute(select(FarmLibraryFile).where(FarmLibraryFile.watch_folder_id == folder_id))).scalars()
    for st in states:
        await db.delete(st)
    await db.delete(watch)
    await db.commit()
    return {"status": "success"}


@router.post("/library/folders/{folder_id}/sync")
async def sync_folder_now(folder_id: int, db: AsyncSession = Depends(get_db), _=_write):
    folder = await _external_folder(db, folder_id)
    if await db.get(FarmWatchedFolder, folder_id) is None:
        raise HTTPException(status_code=400, detail="Folder is not watched")
    return await _run_sync(db, folder)


@router.get("/library/files")
async def list_farm_files(
    folder_id: int | None = None, status: str | None = None, db: AsyncSession = Depends(get_db), _=_read
):
    query = select(FarmLibraryFile).order_by(FarmLibraryFile.path)
    if folder_id is not None:
        query = query.where(FarmLibraryFile.watch_folder_id == folder_id)
    if status is not None:
        query = query.where(FarmLibraryFile.status == status)
    return [_state_dict(st) for st in (await db.execute(query)).scalars().all()]


@router.get("/library/files/{library_file_id}")
async def get_farm_file(library_file_id: int, db: AsyncSession = Depends(get_db), _=_read):
    return _state_dict(await _file_state(db, library_file_id))


@router.put("/library/files/{library_file_id}/slicer")
async def set_slicer_override(
    library_file_id: int, body: SlicerOverrideRequest, db: AsyncSession = Depends(get_db), _=_write
):
    """Force the slicer for one file; ``auto`` goes back to detection. Survives edits and moves."""
    st = await _file_state(db, library_file_id)
    st.slicer_override = None if body.slicer == "auto" else body.slicer
    await db.commit()
    return _state_dict(st)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    return [str(v) for v in value] if isinstance(value, list) else [str(value)]


@router.get("/library/files/{library_file_id}/project")
async def get_project_settings(library_file_id: int, db: AsyncSession = Depends(get_db), _=_read):
    """What slicing with the project's own settings will use: slicer and sidecar, printer, process, filaments.

    Reads only ``project_settings.config`` (a ranged read for NAS files: nothing is downloaded).
    """
    import zipfile

    from backend.app.farm.detect import SLICEABLE, read_project_settings
    from backend.app.farm.nas.access import read_file, run as access_run
    from backend.app.farm.slicing import sidecar_url

    lib_file = await db.scalar(LibraryFile.active().where(LibraryFile.id == library_file_id))
    if lib_file is None:
        raise HTTPException(status_code=404, detail="File not found")
    if lib_file.file_type != "3mf":
        raise HTTPException(status_code=400, detail="Only unsliced 3MF projects carry project settings")
    st = await _file_state(db, library_file_id)
    try:
        cfg = await access_run(read_file, lib_file.file_path, read_project_settings)
    except (OSError, zipfile.BadZipFile) as exc:
        raise HTTPException(status_code=422, detail=f"Cannot read the project settings: {exc}"[:500]) from exc

    names, types, colors = (_as_list(cfg.get(k)) for k in ("filament_settings_id", "filament_type", "filament_colour"))
    sliceable = st.status == "ok" and st.slicer in SLICEABLE
    return {
        "slicer": st.slicer,
        "sliceable": sliceable,
        "sidecar_url": await sidecar_url(db, st.slicer) if sliceable else None,
        "printer_model": st.printer_model,
        "printer_model_raw": st.printer_model_raw,
        "printer_preset": next(iter(_as_list(cfg.get("printer_settings_id"))), None),
        "process_preset": next(iter(_as_list(cfg.get("print_settings_id"))), None),
        "nozzle_diameter": next(iter(_as_list(cfg.get("nozzle_diameter"))), None),
        "filaments": [
            {
                "index": i + 1,
                "preset": names[i] if i < len(names) else None,
                "type": types[i] if i < len(types) else None,
                "color": colors[i] if i < len(colors) else None,
            }
            for i in range(max(len(names), len(types), len(colors)))
        ],
    }


@router.post("/library/files/{library_file_id}/slice", status_code=202)
async def slice_farm_file(library_file_id: int, body: FarmSliceRequest, db: AsyncSession = Depends(get_db), _=_write):
    """Slice with the 3MF's own settings on the sidecar of its slicer. Poll ``status_url``."""
    from backend.app.core import database
    from backend.app.farm.slicing import resolve_slicer, slice_embedded
    from backend.app.services.slice_dispatch import http_exception_to_job_error, slice_dispatch

    lib_file = await db.scalar(LibraryFile.active().where(LibraryFile.id == library_file_id))
    if lib_file is None:
        raise HTTPException(status_code=404, detail="File not found")
    if lib_file.file_type != "3mf":
        raise HTTPException(status_code=400, detail="Only unsliced 3MF projects can be sliced")
    resolve_slicer(await _file_state(db, library_file_id))  # fail fast, before queuing

    async def _run(job_id: int):
        async with database.async_session() as task_db:
            task_file = await task_db.get(LibraryFile, library_file_id)
            try:
                task_state = await _file_state(task_db, library_file_id)
                response = await slice_embedded(
                    task_db, lib_file=task_file, state=task_state, plate=body.plate, job_id=job_id
                )
            except HTTPException as exc:
                raise http_exception_to_job_error(exc) from exc
        return response.model_dump()

    job = await slice_dispatch.enqueue(
        kind="library_file", source_id=library_file_id, source_name=lib_file.filename, run=_run
    )
    return {"job_id": job.id, "status": job.status, "status_url": f"/api/v1/slice-jobs/{job.id}"}


class SdFilesIn(BaseModel):
    keep_days: int = Field(ge=1, le=365)
    keep_printer_ids: list[int] = Field(default_factory=list, max_length=500)


async def _sd_files(db: AsyncSession) -> dict:
    """Bambu printers and whether each keeps its files on the SD after a print (reprint from the display)."""
    from backend.app.farm import printer_files
    from backend.app.models.printer import Printer
    from backend.app.services.printer_providers.factory import normalize_provider

    kept = await printer_files.kept_printers(db)
    printers = (await db.execute(select(Printer).order_by(Printer.name))).scalars().all()
    return {
        "keep_days": await printer_files.keep_days(db),
        "printers": [
            {
                "id": p.id,
                "name": p.name,
                "model": p.model,
                "keep": p.id in kept,
                "ghost_print_risk": printer_files.ghost_print_risk(p.model),
            }
            for p in printers
            if normalize_provider(p.provider) == "bambu"
        ],
    }


@router.get("/sd-files", dependencies=[Depends(require_permission_if_auth_enabled(Permission.SETTINGS_READ))])
async def get_sd_files(db: AsyncSession = Depends(get_db)) -> dict:
    return await _sd_files(db)


@router.put("/sd-files", dependencies=[Depends(require_permission_if_auth_enabled(Permission.SETTINGS_UPDATE))])
async def put_sd_files(body: SdFilesIn, db: AsyncSession = Depends(get_db)) -> dict:
    from backend.app.farm import printer_files

    bambu = {p["id"] for p in (await _sd_files(db))["printers"]}
    await printer_files.save_settings(db, body.keep_days, set(body.keep_printer_ids) & bambu)
    return await _sd_files(db)


class AutoSpoolsIn(BaseModel):
    enabled: bool


@router.get("/auto-spools", dependencies=[Depends(require_permission_if_auth_enabled(Permission.SETTINGS_READ))])
async def get_auto_spools(db: AsyncSession = Depends(get_db)) -> dict:
    """Whether spools are created for the filaments the 3MFs use."""
    from backend.app.api.routes.settings import get_setting
    from backend.app.farm import auto_spools

    enabled = (await get_setting(db, auto_spools.ENABLED_KEY) or "1") != "0"
    return {"enabled": enabled}


@router.put("/auto-spools", dependencies=[Depends(require_permission_if_auth_enabled(Permission.SETTINGS_UPDATE))])
async def put_auto_spools(body: AutoSpoolsIn, db: AsyncSession = Depends(get_db)) -> dict:
    from backend.app.api.routes.settings import set_setting
    from backend.app.farm import auto_spools

    await set_setting(db, auto_spools.ENABLED_KEY, "1" if body.enabled else "0")
    await db.commit()
    return {"enabled": body.enabled}


router.include_router(plates_router)
router.include_router(nas_router)
