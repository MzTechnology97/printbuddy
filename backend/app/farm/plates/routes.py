"""Plates proposed from the 3MF and the 3MF printer -> Printbuddy model map."""

from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.geometry import group
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield, FarmPrinterModelMap
from backend.app.farm.plates.printer_map import PrinterModelResolver, canonical_model
from backend.app.farm.plates.service import (
    PlateReadError,
    confirm,
    folder_hint_warning,
    link_by_geometry,
    refresh_plates,
    suggest_shapes,
)
from backend.app.farm.products.folders import excluded_files
from backend.app.farm.products.models import FarmShape
from backend.app.models.library import LibraryFile

router = APIRouter(tags=["farm"])  # mounted under /farm by farm.routes

_read = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ))
_write = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPLOAD))


async def _file_state(db: AsyncSession, library_file_id: int) -> FarmLibraryFile:
    st = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == library_file_id))
    if st is None:
        raise HTTPException(status_code=404, detail="File is not on a watched folder (or not scanned yet)")
    return st


class YieldIn(BaseModel):
    object_name: str = Field(min_length=1, max_length=255)
    quantity: int = Field(ge=1, le=10000)
    shape_id: int | None = None
    component_code: str | None = Field(default=None, max_length=32)


class PlateUpdate(BaseModel):
    # None keeps the proposed yield; a list replaces it (e.g. after merging objects).
    yields: list[YieldIn] | None = None


class PrinterModelMapIn(BaseModel):
    source_model: str = Field(min_length=1, max_length=100)
    target_model: str = Field(min_length=1, max_length=50)


async def _proposals(db: AsyncSession, plates: list[FarmPlate]) -> dict[str, int]:
    """Shape proposed for the object names still without one."""
    return await suggest_shapes(db, sorted({y.object_name for p in plates for y in p.yields if y.shape_id is None}))


def _plate_dict(
    plate: FarmPlate,
    resolver: PrinterModelResolver,
    warning: str | None = None,
    proposals: dict[str, int] | None = None,
) -> dict:
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
            {
                "object_name": y.object_name,
                "quantity": y.quantity,
                "shape_id": y.shape_id,
                "suggested_shape_id": None if y.shape_id else (proposals or {}).get(y.object_name),
                "component_code": y.component_code,
            }
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
    if library_file_id in await excluded_files(db):
        return []  # left out by its products: no plates proposed nor measured
    try:
        plates = await refresh_plates(db, lib_file, state)
    except PlateReadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resolver = await PrinterModelResolver.load(db)
    warning = await folder_hint_warning(db, state, state.printer_model, resolver)
    proposals = await _proposals(db, plates)
    return [_plate_dict(p, resolver, warning, proposals) for p in plates]


@router.get("/plates/summary")
async def plates_summary(db: AsyncSession = Depends(get_db), _=_read):
    """Plates per file and status, for the confirmation page: ``[{library_file_id, proposed, confirmed}]``."""
    rows = await db.execute(
        select(FarmPlate.library_file_id, FarmPlate.status, func.count())
        .where(FarmPlate.library_file_id.notin_(await excluded_files(db)))
        .group_by(FarmPlate.library_file_id, FarmPlate.status)
    )
    out: dict[int, dict] = {}
    for file_id, status, count in rows.all():
        out.setdefault(file_id, {"library_file_id": file_id, "proposed": 0, "confirmed": 0})[status] = count
    return list(out.values())


@router.get("/plates")
async def list_plates(
    status: str | None = None,
    component_code: str | None = None,
    shape_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _=_read,
):
    """Stored plates (only files whose plates were opened at least once)."""
    query = (
        select(FarmPlate)
        .where(FarmPlate.library_file_id.notin_(await excluded_files(db)))
        .order_by(FarmPlate.library_file_id, FarmPlate.plate_index)
    )
    if status is not None:
        query = query.where(FarmPlate.status == status)
    if component_code is not None:
        query = query.where(FarmPlate.yields.any(FarmPlateYield.component_code == component_code.strip().upper()))
    if shape_id is not None:
        query = query.where(FarmPlate.yields.any(FarmPlateYield.shape_id == shape_id))
    resolver = await PrinterModelResolver.load(db)
    plates = list((await db.execute(query)).scalars())
    proposals = await _proposals(db, plates)
    return [_plate_dict(p, resolver, None, proposals) for p in plates]


@router.put("/plates/{plate_id}")
async def confirm_plate(plate_id: int, body: PlateUpdate, db: AsyncSession = Depends(get_db), _=_write):
    """Confirm a proposed plate, optionally correcting yields and their shapes."""
    plate = await db.get(FarmPlate, plate_id)
    if plate is None:
        raise HTTPException(status_code=404, detail="Plate not found")
    wanted = {y.shape_id for y in body.yields or [] if y.shape_id is not None}
    found = set((await db.execute(select(FarmShape.id).where(FarmShape.id.in_(wanted)))).scalars())
    if missing := wanted - found:
        raise HTTPException(status_code=422, detail=f"Unknown shapes: {sorted(missing)}")
    confirm(plate, [y.model_dump() for y in body.yields] if body.yields is not None else None)
    await db.commit()
    await db.refresh(plate, ["yields"])
    return _plate_dict(plate, await PrinterModelResolver.load(db))


# ---------------------------------------------------------------- pieces (same geometry across plates)


class PieceLink(BaseModel):
    yield_ids: list[int] = Field(min_length=1, max_length=5000)
    shape_id: int | None = None


@router.get("/pieces")
async def list_pieces(db: AsyncSession = Depends(get_db), _=_read):
    """Distinct pieces on every plate, recognised by geometry whatever the object is called.

    Linking a piece to its shape once covers all its plates (``PUT /pieces``). Pieces
    with objects still to link come first.
    """
    rows = (
        await db.execute(
            select(FarmPlateYield, FarmPlate.library_file_id, FarmPlate.plate_index)
            .join(FarmPlate, FarmPlate.id == FarmPlateYield.plate_id)
            .order_by(FarmPlateYield.id)
        )
    ).all()
    files = dict(
        (await db.execute(select(LibraryFile.id, LibraryFile.filename).where(LibraryFile.deleted_at.is_(None)))).all()
    )
    excluded = await excluded_files(db)
    rows = [r for r in rows if r[0].fingerprint and r[1] in files and r[1] not in excluded]
    pieces = []
    for members in group([r[0].fingerprint for r in rows]):
        items = [rows[i] for i in members]
        names = Counter(y.object_name for y, _, _ in items)
        shapes = Counter(y.shape_id for y, _, _ in items if y.shape_id is not None)
        y0, file_id, plate_index = items[0]
        pieces.append(
            {
                "yield_ids": [y.id for y, _, _ in items],
                "names": [n for n, _ in names.most_common(5)],
                "objects": sum(y.quantity for y, _, _ in items),
                "plates": len({y.plate_id for y, _, _ in items}),
                "files": sorted({files[f] for _, f, _ in items})[:3],
                "file_count": len({f for _, f, _ in items}),
                "shape_id": shapes.most_common(1)[0][0] if shapes else None,
                "mixed": len(shapes) > 1,
                "unlinked": sum(1 for y, _, _ in items if y.shape_id is None and y.component_code is None),
                "suggested_shape_id": None,
                "volume_mm3": round(y0.fingerprint[0], 1),
                "hand": int(y0.fingerprint[5]),
                "sample": {"library_file_id": file_id, "plate_index": plate_index},
            }
        )
    open_names = sorted({n for p in pieces if p["shape_id"] is None for n in p["names"]})
    proposals = await suggest_shapes(db, open_names)
    for p in pieces:
        if p["shape_id"] is None:
            p["suggested_shape_id"] = next((proposals[n] for n in p["names"] if n in proposals), None)
    pieces.sort(key=lambda p: (p["unlinked"] == 0, -p["plates"]))
    return pieces


@router.put("/pieces")
async def link_piece(body: PieceLink, db: AsyncSession = Depends(get_db), _=_write):
    """Link every object of one piece (``yield_ids`` from ``GET /pieces``) to a shape.

    Plates whose objects all have a shape are confirmed; other objects with the same
    geometry on those plates get the shape too.
    """
    if body.shape_id is not None and await db.get(FarmShape, body.shape_id) is None:
        raise HTTPException(status_code=422, detail="Unknown shape")
    yields = list((await db.execute(select(FarmPlateYield).where(FarmPlateYield.id.in_(body.yield_ids)))).scalars())
    for y in yields:
        y.shape_id = body.shape_id
    await db.flush()
    plate_ids = {y.plate_id for y in yields}
    plates = list((await db.execute(select(FarmPlate).where(FarmPlate.id.in_(plate_ids)))).scalars())
    confirmed = await link_by_geometry(db, plates)
    await db.commit()
    return {"linked": len(yields), "plates_confirmed": confirmed}


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
