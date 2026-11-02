"""Plates proposed from the project 3MF, confirmed by the user.

A plate is file + plate number: printer model, materials, nozzle and yield come
from the 3MF; time and grams from the slice of that plate (phase 1), when present.
"""

from __future__ import annotations

import asyncio
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import defusedxml.ElementTree as ET
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.farm.detect import PROJECT_SETTINGS_ENTRY
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.plates.printer_map import PrinterModelResolver, canonical_model, models_in_path
from backend.app.models.library import LibraryFile, LibraryFolder

MODEL_SETTINGS_ENTRY = "Metadata/model_settings.config"
SLICE_INFO_ENTRY = "Metadata/slice_info.config"


class PlateReadError(Exception):
    """The project's plate definitions can't be read."""


@dataclass(slots=True)
class ParsedPlate:
    index: int
    name: str | None = None
    objects: dict[str, int] = field(default_factory=dict)  # object name -> instances on the plate
    materials: list[str] = field(default_factory=list)
    print_time_seconds: int | None = None
    filament_grams: float | None = None


def _meta(elem, key: str) -> str | None:
    for m in elem.findall("metadata"):
        if m.get("key") == key:
            return m.get("value")
    return None


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_plates(path: Path) -> tuple[list[ParsedPlate], float | None]:
    """Plates of a project 3MF and its nozzle diameter. Identical copies count separately."""
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
        cfg = {}
        if PROJECT_SETTINGS_ENTRY in names:
            try:
                cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                cfg = {}
        filament_types = [str(t) for t in (cfg.get("filament_type") or [])]
        nozzles = cfg.get("nozzle_diameter") or []
        try:
            nozzle = float(nozzles[0]) if nozzles else None
        except (TypeError, ValueError):
            nozzle = None
        if MODEL_SETTINGS_ENTRY not in names:
            return [], nozzle

        # Orca writes embossed-text parts as <slic3rpe:shape> without declaring the prefix.
        root = ET.fromstring(re.sub(rb"<(/?)([A-Za-z_][\w.-]*):", rb"<\1\2_", zf.read(MODEL_SETTINGS_ENTRY)))
        objects: dict[str, tuple[str, set[int]]] = {}
        for obj in root.findall("object"):
            extruders = {_int(_meta(el, "extruder")) for el in [obj, *obj.findall("part")]} - {None}
            objects[obj.get("id", "")] = (_meta(obj, "name") or f"Object {obj.get('id')}", extruders)

        plates: dict[int, ParsedPlate] = {}
        for plate_elem in root.findall("plate"):
            index = _int(_meta(plate_elem, "plater_id"))
            if index is None:
                continue
            plate = ParsedPlate(index=index, name=(_meta(plate_elem, "plater_name") or "").strip() or None)
            extruders: set[int] = set()
            for inst in plate_elem.findall("model_instance"):
                name, obj_extruders = objects.get(_meta(inst, "object_id") or "", (None, set()))
                if name:
                    plate.objects[name] = plate.objects.get(name, 0) + 1
                    extruders |= obj_extruders
            if not plate.objects:
                continue  # empty plate in the project: prints nothing
            slots = sorted(extruders) or [1]
            materials = {filament_types[s - 1] for s in slots if 0 < s <= len(filament_types)}
            plate.materials = sorted(materials) or filament_types[:1]
            plates[index] = plate

        # Projects saved after slicing in the GUI carry per-plate estimates.
        if SLICE_INFO_ENTRY in names:
            for plate_elem in ET.fromstring(zf.read(SLICE_INFO_ENTRY)).findall("plate"):
                plate = plates.get(_int(_meta(plate_elem, "index")) or -1)
                if plate is not None:
                    plate.print_time_seconds = _int(_meta(plate_elem, "prediction"))
                    weight = _meta(plate_elem, "weight")
                    plate.filament_grams = float(weight) if weight else None
    return sorted(plates.values(), key=lambda p: p.index), nozzle


async def _latest_slices(db: AsyncSession, lib_file: LibraryFile, sha256: str | None) -> dict[int, LibraryFile]:
    """Sliced outputs of this exact file version, by plate number."""
    rows = (
        await db.execute(
            LibraryFile.active()
            .where(LibraryFile.source_type == "sliced", LibraryFile.folder_id == lib_file.folder_id)
            .order_by(LibraryFile.id)
        )
    ).scalars()
    by_plate: dict[int, LibraryFile] = {}
    for out in rows:
        meta = out.file_metadata or {}
        if meta.get("sliced_from_library_file_id") == lib_file.id and meta.get("sliced_from_sha256") == sha256:
            if meta.get("sliced_plate"):
                by_plate[meta["sliced_plate"]] = out  # later ids win
    return by_plate


async def refresh_plates(db: AsyncSession, lib_file: LibraryFile, state: FarmLibraryFile) -> list[FarmPlate]:
    """Bring the stored plates in line with the current file version.

    A plate whose pieces changed goes back to ``proposed``; component codes typed
    for an object name are kept.
    """
    plates = {
        p.plate_index: p
        for p in (await db.execute(select(FarmPlate).where(FarmPlate.library_file_id == lib_file.id))).scalars()
    }
    if state.status == "ok" and (not plates or any(p.source_sha256 != state.sha256 for p in plates.values())):
        try:
            parsed, nozzle = await asyncio.to_thread(parse_plates, Path(lib_file.file_path))
        except (ET.ParseError, zipfile.BadZipFile, KeyError, OSError) as exc:
            # Keep the stored plates: an unreadable version must not wipe confirmed ones.
            raise PlateReadError(f"Cannot read plates from {lib_file.filename}: {exc}") from exc
        for pp in parsed:
            plate = plates.pop(pp.index, None)
            if plate is None:
                plate = FarmPlate(library_file_id=lib_file.id, plate_index=pp.index, yields=[])
                db.add(plate)
            plate.name = pp.name
            plate.printer_model = state.printer_model
            plate.materials = pp.materials
            plate.nozzle_diameter = nozzle
            plate.source_sha256 = state.sha256
            plate.print_time_seconds = pp.print_time_seconds
            plate.filament_grams = pp.filament_grams
            plate.sliced_file_id = None
            if {y.object_name: y.quantity for y in plate.yields} != pp.objects:
                codes = {y.object_name: y.component_code for y in plate.yields}
                plate.yields = [
                    FarmPlateYield(object_name=name, quantity=qty, component_code=codes.get(name))
                    for name, qty in pp.objects.items()
                ]
                plate.status = "proposed"
                plate.confirmed_at = None
        for gone in plates.values():  # plate removed from the project
            await db.delete(gone)
        await db.flush()

    current = list(
        (
            await db.execute(
                select(FarmPlate).where(FarmPlate.library_file_id == lib_file.id).order_by(FarmPlate.plate_index)
            )
        ).scalars()
    )
    for plate_index, out in (await _latest_slices(db, lib_file, state.sha256)).items():
        plate = next((p for p in current if p.plate_index == plate_index), None)
        if plate is not None and plate.sliced_file_id != out.id:
            meta = out.file_metadata or {}
            plate.sliced_file_id = out.id
            plate.print_time_seconds = meta.get("print_time_seconds") or plate.print_time_seconds
            plate.filament_grams = meta.get("filament_used_g") or plate.filament_grams
    await db.commit()
    return current


def confirm(plate: FarmPlate, yields: list[dict] | None) -> None:
    if yields is not None:
        plate.yields = [
            FarmPlateYield(
                object_name=y["object_name"],
                quantity=y["quantity"],
                component_code=(y.get("component_code") or "").strip().upper() or None,
            )
            for y in yields
        ]
    plate.status = "confirmed"
    plate.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)


async def folder_hint_warning(
    db: AsyncSession, state: FarmLibraryFile, plate_model: str | None, resolver: PrinterModelResolver
) -> str | None:
    """Warn when the NAS path names a printer other than the one the 3MF was prepared for."""
    if not plate_model:
        return None
    root = await db.get(LibraryFolder, state.watch_folder_id)
    rel = state.path
    if root and root.external_path:
        rel = state.path.removeprefix(root.external_path)
    named = models_in_path(rel, resolver.known_names())
    target = resolver.resolve(plate_model).target_model
    if not named or named & {canonical_model(plate_model), canonical_model(target)}:
        return None
    return f"3MF prepared for {plate_model}, but the path names {', '.join(sorted(named))}"


async def plates_by_component(db: AsyncSession, codes: list[str]) -> dict[str, list[dict]]:
    """Confirmed plates producing each component code, for the products module.

    Each entry is the contract shared with Seller Hub (``parts[].plates``): ``yield``
    is the pieces of *that* component on the plate; ``printer_model`` is the
    Printbuddy model (the queue's ``target_model``), or the 3MF one when unmapped.
    """
    wanted = {c.strip().upper() for c in codes if c and c.strip()}
    result: dict[str, list[dict]] = {code: [] for code in wanted}
    if not wanted:
        return result
    plates = (
        await db.execute(
            select(FarmPlate)
            .where(FarmPlate.status == "confirmed", FarmPlate.yields.any(FarmPlateYield.component_code.in_(wanted)))
            .order_by(FarmPlate.library_file_id, FarmPlate.plate_index)
        )
    ).scalars()
    resolver = await PrinterModelResolver.load(db)
    for plate in plates:
        pieces: dict[str, int] = {}
        for y in plate.yields:
            if y.component_code in wanted:
                pieces[y.component_code] = pieces.get(y.component_code, 0) + y.quantity
        model = resolver.resolve(plate.printer_model).target_model or plate.printer_model or ""
        for code, qty in pieces.items():
            result[code].append(
                {
                    "library_file_id": plate.library_file_id,
                    "plate": plate.plate_index,
                    "printer_model": model,
                    "material": "+".join(plate.materials or []),
                    "yield": qty,
                }
            )
    return result
