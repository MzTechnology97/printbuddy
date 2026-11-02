"""Farm API: watched NAS folders, per-file slicer, embedded-settings slicing, plates, printer models."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.library_sync import sync_external_folder
from backend.app.farm.models import (
    FarmLibraryFile,
    FarmPlate,
    FarmPlateYield,
    FarmPrinterModelMap,
    FarmWatchedFolder,
)
from backend.app.farm.plates import PlateReadError, confirm, folder_hint_warning, refresh_plates
from backend.app.farm.printer_map import PrinterModelResolver, canonical_model
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


# ---------------------------------------------------------------- plates


class YieldIn(BaseModel):
    object_name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=1, le=10000)
    component_code: str | None = Field(default=None, max_length=32)


class PlateUpdate(BaseModel):
    # None keeps the proposed yield; a list replaces it (e.g. after merging objects).
    yields: list[YieldIn] | None = None


class PrinterModelMapIn(BaseModel):
    source_model: str = Field(min_length=1, max_length=100)
    target_model: str = Field(min_length=1, max_length=50)


def _plate_dict(plate: FarmPlate, resolver: PrinterModelResolver, warning: str | None = None) -> dict:
    target = resolver.resolve(plate.printer_model)
    return {
        "id": plate.id,
        "library_file_id": plate.library_file_id,
        "plate_index": plate.plate_index,
        "name": plate.name,
        "printer_model": plate.printer_model,
        "target_model": target.target_model,
        "target_model_source": target.how,
        "materials": plate.materials or [],
        "nozzle_diameter": plate.nozzle_diameter,
        "print_time_seconds": plate.print_time_seconds,
        "filament_grams": plate.filament_grams,
        "sliced_file_id": plate.sliced_file_id,
        "status": plate.status,
        "confirmed_at": plate.confirmed_at,
        "yields": [
            {"object_name": y.object_name, "quantity": y.quantity, "component_code": y.component_code}
            for y in plate.yields
        ],
        "warning": warning,
    }


@router.get("/library/files/{library_file_id}/plates")
async def get_file_plates(library_file_id: int, db: AsyncSession = Depends(get_db), _=_read):
    """Plates of a project file, proposed from the 3MF (refreshed when the file changes)."""
    lib_file = await db.scalar(LibraryFile.active().where(LibraryFile.id == library_file_id))
    if lib_file is None:
        raise HTTPException(status_code=404, detail="File not found")
    if lib_file.file_type != "3mf":
        raise HTTPException(status_code=400, detail="Plates come from unsliced 3MF projects")
    state = await _file_state(db, library_file_id)
    try:
        plates = await refresh_plates(db, lib_file, state)
    except PlateReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resolver = await PrinterModelResolver.load(db)
    warning = await folder_hint_warning(db, state, state.printer_model, resolver)
    return [_plate_dict(p, resolver, warning) for p in plates]


@router.get("/plates")
async def list_plates(
    status: str | None = None, component_code: str | None = None, db: AsyncSession = Depends(get_db), _=_read
):
    """Stored plates (only files whose plates were opened at least once)."""
    query = select(FarmPlate).order_by(FarmPlate.library_file_id, FarmPlate.plate_index)
    if status is not None:
        query = query.where(FarmPlate.status == status)
    if component_code is not None:
        query = query.where(FarmPlate.yields.any(FarmPlateYield.component_code == component_code.strip().upper()))
    resolver = await PrinterModelResolver.load(db)
    return [_plate_dict(p, resolver) for p in (await db.execute(query)).scalars()]


@router.put("/plates/{plate_id}")
async def confirm_plate(plate_id: int, body: PlateUpdate, db: AsyncSession = Depends(get_db), _=_write):
    """Confirm a proposed plate, optionally correcting yields and component codes."""
    plate = await db.get(FarmPlate, plate_id)
    if plate is None:
        raise HTTPException(status_code=404, detail="Plate not found")
    confirm(plate, [y.model_dump() for y in body.yields] if body.yields is not None else None)
    await db.commit()
    await db.refresh(plate, ["yields"])
    return _plate_dict(plate, await PrinterModelResolver.load(db))


# ---------------------------------------------------------------- printer models


@router.get("/printer-models")
async def list_printer_models(db: AsyncSession = Depends(get_db), _=_read):
    """3MF printer models seen on the NAS and how each maps to a Printbuddy printer model."""
    resolver = await PrinterModelResolver.load(db)
    seen = (
        await db.execute(
            select(FarmLibraryFile.printer_model, func.count())
            .where(FarmLibraryFile.printer_model.isnot(None))
            .group_by(FarmLibraryFile.printer_model)
        )
    ).all()
    rows = (await db.execute(select(FarmPrinterModelMap).order_by(FarmPrinterModelMap.source_model))).scalars()
    return {
        "printer_models": sorted(set(resolver.by_canonical.values())),
        "mappings": [{"id": r.id, "source_model": r.source_model, "target_model": r.target_model} for r in rows],
        "seen": [
            {"printer_model": model, "files": count, **_resolution_dict(resolver.resolve(model))}
            for model, count in sorted(seen)
        ],
    }


def _resolution_dict(res) -> dict:
    return {"target_model": res.target_model, "target_model_source": res.how}


@router.put("/printer-models")
async def set_printer_model(body: PrinterModelMapIn, db: AsyncSession = Depends(get_db), _=_write):
    """Map a 3MF printer model (e.g. a custom Klipper profile) to a Printbuddy printer model, once."""
    resolver = await PrinterModelResolver.load(db)
    target = resolver.by_canonical.get(canonical_model(body.target_model) or "")
    if target is None:
        raise HTTPException(status_code=400, detail=f"No printer with model '{body.target_model}'")
    source = body.source_model.strip().casefold()
    row = await db.scalar(select(FarmPrinterModelMap).where(FarmPrinterModelMap.source_model == source))
    if row is None:
        row = FarmPrinterModelMap(source_model=source)
        db.add(row)
    row.target_model = target
    await db.commit()
    return {"id": row.id, "source_model": row.source_model, "target_model": row.target_model}


@router.delete("/printer-models/{map_id}")
async def delete_printer_model(map_id: int, db: AsyncSession = Depends(get_db), _=_write):
    row = await db.get(FarmPrinterModelMap, map_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Mapping not found")
    await db.delete(row)
    await db.commit()
    return {"status": "success"}
