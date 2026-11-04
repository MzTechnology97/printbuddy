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
from backend.app.farm.plates.geometry import MAIN_MODEL, object_fingerprints, object_meshes, same_piece
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.plates.printer_map import PrinterModelResolver, canonical_model, models_in_path
from backend.app.farm.products.folders import component_files, excluded_files, may_print
from backend.app.farm.products.models import FarmComponent, FarmShape, component_shapes
from backend.app.models.library import LibraryFile, LibraryFolder

MODEL_SETTINGS_ENTRY = "Metadata/model_settings.config"
SLICE_INFO_ENTRY = "Metadata/slice_info.config"
PARSE_VERSION = 2  # 2: an object's copies left out of model_settings count; older plates are read again
ORCA_COPY_RE = re.compile(r"_id_\d+_copy_\d+$")

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


def _build_instances(zf: zipfile.ZipFile) -> dict[str, int]:
    """Printable instances per object id in the 3MF build (one build item each)."""
    if MAIN_MODEL not in zf.namelist():
        return {}
    counts: dict[str, int] = {}
    with zf.open(MAIN_MODEL) as fh:
        for _, elem in ET.iterparse(fh):
            if elem.tag.rsplit("}", 1)[-1] == "item" and elem.get("printable") != "0":
                oid = elem.get("objectid", "")
                counts[oid] = counts.get(oid, 0) + 1
            elem.clear()  # inline meshes can be large
    return counts


def _label(name: str) -> str:
    """An object name as a slicer's object label writes it ("Maniglia Portiera" -> "Maniglia_Portiera")."""
    return re.sub(r"[^0-9A-Za-z]", "_", name)


def match_object(label: str, known) -> str | None:
    """The object of ``known`` a printer names ``label`` when excluding it, or None.

    Bambu names the object; Orca writes a label, "<name>_id_<object>_copy_<n>", that Klipper upper-cases.
    None also when the label fits two objects ("Base 1" and "Base-1" both write "Base_1"): never a guess.
    """
    if label in known:
        return label
    key = _label(ORCA_COPY_RE.sub("", label.casefold())).casefold()
    found = {n for n in known if _label(n).casefold() == key}
    return found.pop() if len(found) == 1 else None


def _sliced_objects(plate_elem, by_identify: dict[str, str], known: set[str]) -> dict[str, int] | None:
    """Pieces per object name a slice printed on this plate (slice_info, one entry per copy).

    Skipped copies don't count. None when the slice lists none, or names one that isn't
    an object of the project: the plate keeps its count from model_settings and the build.
    """
    labels = {_label(n): n for n in known}
    counts: dict[str, int] = {}
    for entry in plate_elem.findall("object"):
        if (entry.get("skipped") or "").lower() == "true":
            continue
        label = entry.get("name") or ""
        # Bambu names the object; Orca writes a label, "<name>_id_<object>_copy_<n>"
        name = (
            by_identify.get(entry.get("identify_id"))
            or (label if label in known else None)
            or labels.get(_label(ORCA_COPY_RE.sub("", label)))
        )
        if name is None:
            logger.debug("Slice object %r is no object of the project: plate counted from model_settings", label)
            return None
        counts[name] = counts.get(name, 0) + 1
    return counts or None


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
        listed: dict[str, list[int]] = {}  # object id -> the plate of each instance model_settings lists
        by_identify: dict[str, str] = {}  # instance identify_id -> object name (slice_info names them so)
        for plate_elem in root.findall("plate"):
            index = _int(_meta(plate_elem, "plater_id"))
            if index is None:
                continue
            plate = ParsedPlate(index=index, name=(_meta(plate_elem, "plater_name") or "").strip() or None)
            extruders: set[int] = set()
            for inst in plate_elem.findall("model_instance"):
                oid = _meta(inst, "object_id") or ""
                name, obj_extruders = objects.get(oid, (None, set()))
                if name:
                    plate.objects[name] = plate.objects.get(name, 0) + 1
                    listed.setdefault(oid, []).append(index)
                    if identify := _meta(inst, "identify_id"):
                        by_identify[identify] = name
                    extruders |= obj_extruders
            if not plate.objects:
                continue  # empty plate in the project: prints nothing
            slots = sorted(extruders) or [1]
            materials = {filament_types[s - 1] for s in slots if 0 < s <= len(filament_types)}
            plate.materials = sorted(materials) or filament_types[:1]
            plates[index] = plate

        # Orca may list only an object's first instance on its plate, yet prints all the copies of it
        # there (a K2 plate counted 1 handle and printed 15): the unlisted ones count on that plate.
        # A slice's own list of the plate's objects, below, wins over this.
        # shortcut: a copy left off every plate still counts, and an object listed on several plates
        # keeps its listed instances; place copies by position if such an unsliced project shows up.
        built = _build_instances(zf)
        for oid, on in listed.items():
            extra = built.get(oid, 0) - len(on)
            if extra > 0 and len(set(on)) == 1:
                name = objects[oid][0]
                plates[on[0]].objects[name] += extra

        # Projects saved after slicing in the GUI carry per-plate estimates, and the objects printed.
        if SLICE_INFO_ENTRY in names:
            known = {name for name, _ in objects.values()}
            for plate_elem in ET.fromstring(zf.read(SLICE_INFO_ENTRY)).findall("plate"):
                plate = plates.get(_int(_meta(plate_elem, "index")) or -1)
                if plate is not None:
                    plate.print_time_seconds = _int(_meta(plate_elem, "prediction"))
                    weight = _meta(plate_elem, "weight")
                    plate.filament_grams = float(weight) if weight else None
                    sliced = _sliced_objects(plate_elem, by_identify, known)
                    if sliced and sliced != plate.objects:
                        logger.debug(
                            "%s plate %s: the slice printed %s, model_settings and build give %s",
                            path.name,
                            plate.index,
                            sliced,
                            plate.objects,
                        )
                        plate.objects = sliced

        try:
            fingerprints = object_fingerprints(zf, {oid: name for oid, (name, _) in objects.items()})
        except Exception:  # an odd mesh must not cost the plates; the object just isn't recognised
            logger.warning("Cannot measure the objects of %s", path.name, exc_info=True)
            fingerprints = {}
        for plate in plates.values():
            plate.fingerprints = {n: fingerprints.get(n, []) for n in plate.objects}
    return sorted(plates.values(), key=lambda p: p.index), nozzle


async def _latest_slices(
    db: AsyncSession, lib_file: LibraryFile, sha256: str | None, colour: str | None = None
) -> dict[int, LibraryFile]:
    """Sliced outputs of this exact file version, by plate number (the plain ones, or one colour variant).

    Any folder: NAS outputs live in the project's ``Printbuddy`` subfolder. An output sliced
    without a plate number is the project's default plate (1); "all plates" (0) is no single plate.
    ``colour`` is a variant's ``sliced_color`` (see ``farm.variants``).
    """
    rows = (
        await db.execute(LibraryFile.active().where(LibraryFile.source_type == "sliced").order_by(LibraryFile.id))
    ).scalars()
    by_plate: dict[int, LibraryFile] = {}
    for out in rows:
        meta = out.file_metadata or {}
        if "extracted_from_library_file_id" in meta:
            continue  # a Klipper .gcode taken out of an output, not an output itself
        if (
            meta.get("sliced_from_library_file_id") == lib_file.id
            and meta.get("sliced_from_sha256") == sha256
            and ((meta.get("sliced_color") or "").strip().lower() or None) == colour
        ):
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
    stale = not plates or any(
        p.source_sha256 != state.sha256 or (p.parse_version or 1) < PARSE_VERSION for p in plates.values()
    )
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
            plate.parse_version = PARSE_VERSION
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
                        FarmPlateYield(
                            object_name=name, quantity=qty, shape_id=shape_id, component_code=code, fingerprint=fp
                        )
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
            .where(FarmPlate.library_file_id.notin_(await excluded_files(db)))
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


async def plates_for(db: AsyncSession, components: list[FarmComponent]) -> dict[int, list[dict]]:
    """Confirmed plates of each component (by id), in the shape of :func:`_confirmed_plates_by`.

    A plate of a shape serves every colour of it; a set's plate yields its complete sets. Only
    the 3MFs of the component's products count.
    """
    from backend.app.farm.products.shapes import candidate_plates, plate_yield, product_shapes

    result: dict[int, list[dict]] = {c.id: [] for c in components}
    files = await component_files(db)
    plates = await candidate_plates(db, components, files)
    if not plates:
        return result
    own = await product_shapes(db, {c.project_id for c in components if c.project_id is not None})
    resolver = await PrinterModelResolver.load(db)
    for plate in plates:
        model = resolver.resolve(plate.printer_model).target_model or plate.printer_model or ""
        for c in components:
            if not may_print(files, c.id, plate.library_file_id):
                continue
            if n := plate_yield(c, plate, own):
                result[c.id].append(
                    {
                        "library_file_id": plate.library_file_id,
                        "plate": plate.plate_index,
                        "printer_model": model,
                        "material": "+".join(plate.materials or []),
                        "yield": n,
                    }
                )
    return result


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


async def shape_geometries(db: AsyncSession, shape_ids: set[int]) -> dict[int, list[list[float]]]:
    """Measured fingerprints of the objects already linked to each shape."""
    out: dict[int, list[list[float]]] = {}
    if shape_ids:
        rows = await db.execute(
            select(FarmPlateYield.shape_id, FarmPlateYield.fingerprint).where(
                FarmPlateYield.shape_id.in_(shape_ids), FarmPlateYield.fingerprint.isnot(None)
            )
        )
        for shape_id, fp in rows.all():
            if fp:
                out.setdefault(shape_id, []).append(fp)
    return out


def fits_shape(fp: list[float] | None, measured: list[list[float]] | None) -> bool:
    """A name-based proposal holds only if the geometry agrees with the shape's measured objects.

    Generic CAD names ("Corpo1", "Body1", "Assemblaggio") are shared by unrelated objects; a shape
    with no measured object yet (named by hand) can't be checked, so the name decides.
    """
    return not fp or not measured or any(same_piece(fp, m) for m in measured)


async def name_scopes(db: AsyncSession) -> tuple[dict[int, set[int]], dict[int, set[int]]]:
    """Shapes a name may propose for each product, and the products of each 3MF project.

    A product's shapes are those of the pieces in its 3MF projects and of its components (a shape
    named by hand counts once a component of the product has it). Names are reused across
    products ("Corpo1"), so a name only proposes shapes of the product the object belongs to.
    """
    from backend.app.farm.products.folders import file_products
    from backend.app.farm.products.models import FarmProductPart

    files = await file_products(db)
    scopes: dict[int, set[int]] = {}
    rows = await db.execute(
        select(FarmPlate.library_file_id, FarmPlateYield.shape_id)
        .join(FarmPlate, FarmPlate.id == FarmPlateYield.plate_id)
        .where(FarmPlateYield.shape_id.isnot(None), FarmPlate.library_file_id.in_(files))
        .distinct()
    )
    for file_id, shape_id in rows.all():
        for project_id in files[file_id]:
            scopes.setdefault(project_id, set()).add(shape_id)
    parts = await db.execute(
        select(FarmProductPart.project_id, FarmComponent).join(
            FarmComponent, FarmComponent.id == FarmProductPart.component_id
        )
    )
    for project_id, comp in parts.all():
        scopes.setdefault(project_id, set()).update(component_shapes(comp))
    return scopes, files


def scope_of(files: list[int], scopes: dict[int, set[int]], products: dict[int, set[int]]) -> set[int]:
    """Shapes the products of these files may propose by name; none when no product holds them."""
    return {s for f in files for pid in products.get(f, ()) for s in scopes.get(pid, ())}


async def suggest_shapes(db: AsyncSession, names: list[str], scope: set[int] | None = None) -> dict[str, int]:
    """Shape to propose for each object name, from names already confirmed elsewhere and shape names.

    Same name after :func:`object_key`, or one name's words contained in the other's
    ("trurffle sopra k2" ⊇ "trurffle sopra"), sharing at least two words and not
    differing by a number ("portiera grande 2" is not "portiera grande"). Ambiguous
    names get no proposal. ``scope``: only these shapes may be proposed.
    """
    if scope is not None and not scope:
        return {}
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
    if scope is not None:
        known = {key: ids & scope for key, ids in known.items() if ids & scope}

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
