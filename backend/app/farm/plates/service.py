"""Plates proposed from the project 3MF, confirmed by the user.

A plate is file + plate number: printer model, materials, nozzle and yield come
from the 3MF; time and grams from the slice of that plate (phase 1), when present.
"""

from __future__ import annotations

import json
import logging
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
from backend.app.farm.nas.access import read_file, run as access_run
from backend.app.farm.plates.geometry import object_fingerprints, object_meshes, same_piece
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.plates.printer_map import PrinterModelResolver, canonical_model, models_in_path
from backend.app.farm.products.models import FarmShape
from backend.app.models.library import LibraryFile, LibraryFolder

MODEL_SETTINGS_ENTRY = "Metadata/model_settings.config"
SLICE_INFO_ENTRY = "Metadata/slice_info.config"

logger = logging.getLogger(__name__)


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
    fingerprints: dict[str, list[float]] = field(default_factory=dict)  # object name -> geometry


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


def _model_settings(zf: zipfile.ZipFile):
    # Orca writes embossed-text parts as <slic3rpe:shape> without declaring the prefix.
    return ET.fromstring(re.sub(rb"<(/?)([A-Za-z_][\w.-]*):", rb"<\1\2_", zf.read(MODEL_SETTINGS_ENTRY)))


def _model_objects(root) -> dict[str, tuple[str, set[int]]]:
    """``{object id: (name, extruders)}`` from model_settings."""
    objects: dict[str, tuple[str, set[int]]] = {}
    for obj in root.findall("object"):
        extruders = {_int(_meta(el, "extruder")) for el in [obj, *obj.findall("part")]} - {None}
        objects[obj.get("id", "")] = (_meta(obj, "name") or f"Object {obj.get('id')}", extruders)
    return objects


def object_mesh(fh, object_name: str):
    """The mesh of one object of a project 3MF (as :func:`parse_plates` names it), or None."""
    with zipfile.ZipFile(fh) as zf:
        if MODEL_SETTINGS_ENTRY not in zf.namelist():
            return None
        objects = _model_objects(_model_settings(zf))
        return object_meshes(zf, {oid: name for oid, (name, _) in objects.items()}).get(object_name)


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

        root = _model_settings(zf)
        objects = _model_objects(root)

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

        try:
            fingerprints = object_fingerprints(zf, {oid: name for oid, (name, _) in objects.items()})
        except Exception:  # an odd mesh must not cost the plates; the object just isn't recognised
            logger.warning("Cannot measure the objects of %s", path.name, exc_info=True)
            fingerprints = {}
        for plate in plates.values():
            plate.fingerprints = {n: fingerprints.get(n, []) for n in plate.objects}
    return sorted(plates.values(), key=lambda p: p.index), nozzle


async def _latest_slices(db: AsyncSession, lib_file: LibraryFile, sha256: str | None) -> dict[int, LibraryFile]:
    """Sliced outputs of this exact file version, by plate number.

    Any folder: NAS outputs live in the project's ``Printbuddy`` subfolder. An output sliced
    without a plate number is the project's default plate (1); "all plates" (0) is no single plate.
    """
    rows = (
        await db.execute(LibraryFile.active().where(LibraryFile.source_type == "sliced").order_by(LibraryFile.id))
    ).scalars()
    by_plate: dict[int, LibraryFile] = {}
    for out in rows:
        meta = out.file_metadata or {}
        if "extracted_from_library_file_id" in meta:
            continue  # a Klipper .gcode taken out of an output, not an output itself
        if meta.get("sliced_from_library_file_id") == lib_file.id and meta.get("sliced_from_sha256") == sha256:
            plate = 1 if meta.get("sliced_plate") is None else meta["sliced_plate"]
            if plate:
                by_plate[plate] = out  # later ids win
    return by_plate


async def refresh_plates(db: AsyncSession, lib_file: LibraryFile, state: FarmLibraryFile) -> list[FarmPlate]:
    """Bring the stored plates in line with the current file version.

    A plate whose pieces changed goes back to ``proposed``; shapes (and old component
    codes) set for an object name are kept, and so are those of a renamed object with the
    same geometry. Objects still without a shape get it from the same geometry on other
    plates, and a plate whose objects all have one is confirmed.
    """
    plates = {
        p.plate_index: p
        for p in (await db.execute(select(FarmPlate).where(FarmPlate.library_file_id == lib_file.id))).scalars()
    }
    stale = not plates or any(p.source_sha256 != state.sha256 for p in plates.values())
    unmeasured = any(y.fingerprint is None for p in plates.values() for y in p.yields)  # stored before geometry
    if state.status == "ok" and (stale or unmeasured):
        try:
            parsed, nozzle = await access_run(read_file, lib_file.file_path, parse_plates)
        except (ET.ParseError, zipfile.BadZipFile, KeyError, OSError) as exc:
            # Keep the stored plates: an unreadable version must not wipe confirmed ones.
            raise PlateReadError(f"Cannot read plates from {lib_file.filename}: {exc}") from exc
        touched = []
        for pp in parsed:
            plate = plates.pop(pp.index, None)
            if plate is None:
                plate = FarmPlate(library_file_id=lib_file.id, plate_index=pp.index, yields=[])
                db.add(plate)
            touched.append(plate)
            plate.name = pp.name
            plate.printer_model = state.printer_model
            plate.materials = pp.materials
            plate.nozzle_diameter = nozzle
            plate.source_sha256 = state.sha256
            plate.print_time_seconds = pp.print_time_seconds
            plate.filament_grams = pp.filament_grams
            plate.sliced_file_id = None
            if {y.object_name: y.quantity for y in plate.yields} != pp.objects:
                old = list(plate.yields)
                links = {y.object_name: (y.shape_id, y.component_code) for y in old}
                yields = []
                for name, qty in pp.objects.items():
                    fp = pp.fingerprints.get(name, [])
                    shape_id, code = links.get(name, (None, None))
                    if shape_id is None and code is None:  # renamed in the slicer: same geometry, same link
                        twin = next((y for y in old if same_piece(fp, y.fingerprint)), None)
                        shape_id, code = (twin.shape_id, twin.component_code) if twin else (None, None)
                    yields.append(
                        FarmPlateYield(object_name=name, quantity=qty, shape_id=shape_id, component_code=code,
                                       fingerprint=fp)
                    )
                plate.yields = yields
                plate.status = "proposed"
                plate.confirmed_at = None
            else:
                for y in plate.yields:
                    y.fingerprint = pp.fingerprints.get(y.object_name, [])
        for gone in plates.values():  # plate removed from the project
            await db.delete(gone)
        await db.flush()
        await link_by_geometry(db, touched)

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
        measured = {y.object_name: y.fingerprint for y in plate.yields}
        plate.yields = [
            FarmPlateYield(
                object_name=y["object_name"],
                quantity=y["quantity"],
                shape_id=y.get("shape_id"),
                component_code=(y.get("component_code") or "").strip().upper() or None,
                fingerprint=measured.get(y["object_name"], []),
            )
            for y in yields
        ]
    plate.status = "confirmed"
    plate.confirmed_at = datetime.now(timezone.utc).replace(tzinfo=None)


async def link_by_geometry(db: AsyncSession, plates: list[FarmPlate]) -> int:
    """Shape for the objects still without one, when the same geometry already has exactly one.

    A proposed plate whose objects all have a shape is confirmed: the quantities come from
    the 3MF, so nothing is left to check. Returns the plates confirmed.
    """
    open_yields = [
        y for p in plates for y in p.yields if y.shape_id is None and y.component_code is None and y.fingerprint
    ]
    if open_yields:
        known = (
            await db.execute(
                select(FarmPlateYield.fingerprint, FarmPlateYield.shape_id).where(FarmPlateYield.shape_id.isnot(None))
            )
        ).all()
        for y in open_yields:
            shapes = {shape_id for fp, shape_id in known if same_piece(y.fingerprint, fp)}
            if len(shapes) == 1:
                y.shape_id = shapes.pop()
    confirmed = 0
    for p in plates:
        if p.status == "proposed" and p.yields and all(y.shape_id or y.component_code for y in p.yields):
            confirm(p, None)
            confirmed += 1
    return confirmed


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


async def _confirmed_plates_by(db: AsyncSession, attr: str, wanted: set) -> dict:
    """Confirmed plates producing each wanted value of a yield column (``shape_id`` or ``component_code``).

    Each entry is the contract shared with Seller Hub (``parts[].plates``): ``yield``
    is the pieces of *that* shape/component on the plate; ``printer_model`` is the
    Printbuddy model (the queue's ``target_model``), or the 3MF one when unmapped.
    """
    result: dict = {key: [] for key in wanted}
    if not wanted:
        return result
    column = getattr(FarmPlateYield, attr)
    plates = (
        await db.execute(
            select(FarmPlate)
            .where(FarmPlate.status == "confirmed", FarmPlate.yields.any(column.in_(wanted)))
            .order_by(FarmPlate.library_file_id, FarmPlate.plate_index)
        )
    ).scalars()
    resolver = await PrinterModelResolver.load(db)
    for plate in plates:
        pieces: dict = {}
        for y in plate.yields:
            key = getattr(y, attr)
            if key in wanted:
                pieces[key] = pieces.get(key, 0) + y.quantity
        model = resolver.resolve(plate.printer_model).target_model or plate.printer_model or ""
        for key, qty in pieces.items():
            result[key].append(
                {
                    "library_file_id": plate.library_file_id,
                    "plate": plate.plate_index,
                    "printer_model": model,
                    "material": "+".join(plate.materials or []),
                    "yield": qty,
                }
            )
    return result


async def plates_by_shape(db: AsyncSession, shape_ids: list[int]) -> dict[int, list[dict]]:
    """Confirmed plates producing each shape: one plate serves every colour of the shape."""
    return await _confirmed_plates_by(db, "shape_id", set(shape_ids))


async def plates_by_component(db: AsyncSession, codes: list[str]) -> dict[str, list[dict]]:
    """Confirmed plates whose yields name a component code (plates confirmed before shapes)."""
    return await _confirmed_plates_by(db, "component_code", {c.strip().upper() for c in codes if c and c.strip()})


# ---------------------------------------------------------------- shape suggestions

# Slicer copies: "Hook (1) (1)", "Perno -1 -1"; a trailing " 2" is a different piece, so it stays.
_COPY_SUFFIX = re.compile(r"\s*(\(\d+\)|-\s*\d+)$")


def object_key(name: str) -> str:
    """Object name without copy suffixes, file extension, case and separators."""
    key = re.sub(r"\.(stl|3mf|obj|step)$", "", name.strip().casefold())
    while (stripped := _COPY_SUFFIX.sub("", key)) != key:
        key = stripped
    return " ".join(re.sub(r"[_\-]+", " ", key).split())


async def suggest_shapes(db: AsyncSession, names: list[str]) -> dict[str, int]:
    """Shape to propose for each object name, from names already confirmed elsewhere and shape names.

    Same name after :func:`object_key`, or one name's words contained in the other's
    ("trurffle sopra k2" ⊇ "trurffle sopra"), sharing at least two words and not
    differing by a number ("portiera grande 2" is not "portiera grande"). Ambiguous
    names get no proposal.
    """
    known: dict[str, set[int]] = {}
    confirmed = await db.execute(
        select(FarmPlateYield.object_name, FarmPlateYield.shape_id)
        .join(FarmPlate)
        .where(FarmPlate.status == "confirmed", FarmPlateYield.shape_id.isnot(None))
    )
    for obj, shape_id in confirmed.all():
        known.setdefault(object_key(obj), set()).add(shape_id)
    for shape in (await db.execute(select(FarmShape))).scalars():
        known.setdefault(object_key(shape.name), set()).add(shape.id)

    proposals: dict[str, int] = {}
    for name in names:
        key = object_key(name)
        if len(known.get(key, ())) == 1:
            proposals[name] = next(iter(known[key]))
            continue
        words, best, ids = set(key.split()), 0, set()
        for other, shape_ids in known.items():
            other_words = set(other.split())
            common = len(words & other_words)
            if common < 2 or not (other_words <= words or words <= other_words):
                continue
            if any(w.isdigit() for w in words ^ other_words):
                continue
            if common > best:
                best, ids = common, set(shape_ids)
            elif common == best:
                ids |= shape_ids
        if len(ids) == 1:
            proposals[name] = ids.pop()
    return proposals
