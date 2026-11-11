"""Colour variants of a plate: slice it with the 3MF's filament of the component's colour.

A project often holds one filament per colour it is sold in, each with its own profile (flow,
volumetric speed, temperatures), while every object sits on one of them. To print a plate in
another colour its objects are moved, in a temporary copy, to the filament of that colour
(``extruder`` in ``Metadata/model_settings.config``); the file on the NAS is never touched.
A plate whose objects use several filaments is left as it is: only one-colour plates have variants.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from backend.app.farm.detect import PROJECT_SETTINGS_ENTRY

logger = logging.getLogger(__name__)

MODEL_SETTINGS_ENTRY = "Metadata/model_settings.config"
SLICE_INFO_ENTRY = "Metadata/slice_info.config"


class MissingColour(Exception):
    """The 3MF has no filament of the variant's colour: never slice it with another colour's profile."""


def _zip(source: bytes | Path) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(source) if isinstance(source, bytes) else source)


def _plate_objects(root: ET.Element, plate: int) -> list[ET.Element]:
    """The ``<object>`` elements placed on this plate (1-based)."""
    ids: set[str] = set()
    for plate_elem in root.findall("plate"):
        meta = {m.get("key"): m.get("value") for m in plate_elem.findall("metadata")}
        if meta.get("plater_id") == str(plate):
            ids |= {
                m.get("value")
                for inst in plate_elem.findall("model_instance")
                for m in inst.findall("metadata")
                if m.get("key") == "object_id"
            }
    return [obj for obj in root.findall("object") if obj.get("id") in ids]


def _extruder(elem: ET.Element) -> ET.Element | None:
    return elem.find("metadata[@key='extruder']")


def _extruder_value(elem: ET.Element) -> int:
    """The ``extruder`` an element sets; 0 when it sets none (or 0: "the default")."""
    meta = _extruder(elem)
    return int(meta.get("value") or 0) if meta is not None else 0


def _object_filaments(obj: ET.Element) -> set[int]:
    """Filaments (1-based) an object prints with, as the slicers resolve them (``ModelVolume::extruder_id``):
    each part's own, else the object's, else filament 1."""
    own = _extruder_value(obj) or 1
    parts = [
        p for p in obj.findall("part") if p.get("subtype", "normal_part") == "normal_part"
    ]  # modifiers print nothing
    return {_extruder_value(p) or own for p in parts} or {own}


def _painted(zf: zipfile.ZipFile) -> bool:
    """Faces painted with filaments in the meshes (multi-colour, whatever the extruders say)."""
    return any(
        name.startswith("3D/") and name.endswith(".model") and b"paint_color" in zf.read(name) for name in zf.namelist()
    )


def plate_filaments(source: bytes | Path, plate: int) -> set[int]:
    """The filaments the objects of a plate print with; a painted model counts as several."""
    with _zip(source) as zf:
        try:
            root = ET.fromstring(zf.read(MODEL_SETTINGS_ENTRY))
        except KeyError:
            return set()
        used = set().union(*(_object_filaments(o) for o in _plate_objects(root, plate)))
        if len(used) == 1 and _painted(zf):
            used.add(0)
    return used


def _dual_auto(cfg: dict) -> bool:
    """A dual-nozzle project left on automatic grouping: the slicer picks each filament's nozzle."""
    return len(cfg.get("physical_extruder_map") or []) > 1 and str(cfg.get("filament_map_mode", "")).lower().startswith(
        "auto"
    )


def plate_slots(source: bytes | Path, plate: int, filament: int) -> list[dict] | None:
    """The filaments the plate will be sliced with, read from the unsliced project, as the print dialog's
    slot rows (``extract_filament_requirements`` without grams): ``filament`` when the colour variant moves
    the plate there, else its own, plus the support filaments when supports are on.

    None when the slice can't be foreseen: a painted plate (its colours aren't in the extruders), or a
    dual-nozzle project left on automatic grouping with several filaments (the slicer picks each one's nozzle).
    With one filament there the row has no nozzle: either nozzle's slots may take it.
    """
    from backend.app.utils.threemf_tools import extract_nozzle_mapping_from_3mf

    with _zip(source) as zf:
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY))
        except (KeyError, ValueError):
            return None
        if _painted(zf):
            return None
        auto = _dual_auto(cfg)
        nozzles = {} if auto else extract_nozzle_mapping_from_3mf(zf) or {}
        try:
            objects = _plate_objects(ET.fromstring(zf.read(MODEL_SETTINGS_ENTRY)), plate)
        except KeyError:
            objects = []
    used = {filament} if filament else plate_filaments(source, plate)
    on = {"1", "true"}
    supports = str(cfg.get("enable_support", "")).lower() in on or any(
        str(m.get("value", "")).lower() in on for o in objects for m in o.findall("metadata[@key='enable_support']")
    )
    if supports:  # over-listing a filament is harmless, missing one sends it unmapped
        for key in ("support_filament", "support_interface_filament"):
            try:
                used.add(int(cfg.get(key) or 0))  # 0: the object's own filament
            except (TypeError, ValueError):
                pass
    used.discard(0)
    if auto and len(used) != 1:
        return None
    types, colours = cfg.get("filament_type") or [], cfg.get("filament_colour") or []
    slots = []
    for slot in sorted(used):
        colour = str(colours[slot - 1]).strip().lstrip("#")[:6] if slot <= len(colours) else ""
        row = {
            "slot_id": slot,
            "type": str(types[slot - 1]) if slot <= len(types) else "",
            "color": f"#{colour}" if colour else "",
            "tray_info_idx": "",
            "used_grams": 0,  # known after the slice
        }
        if nozzles:
            row["nozzle_id"] = nozzles.get(slot)
        slots.append(row)
    return slots or None


def free_nozzle(source: bytes | Path, plate: int, filament: int) -> bool:
    """A dual-nozzle project on automatic grouping whose plate prints with one filament, supports included:
    the slot chosen for it picks the nozzle, and the slice is forced there (``force_nozzle``)."""
    with _zip(source) as zf:
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY))
        except (KeyError, ValueError):
            return False
    return _dual_auto(cfg) and bool(plate_slots(source, plate, filament))


def force_nozzle(model_bytes: bytes, plate: int, nozzle: int) -> bytes:
    """A copy of the 3MF whose plate prints on ``nozzle`` (the printer's extruder id: 0 right, 1 left).

    The slicers' Manual filament grouping, on the plate and in the project: every filament on the logical
    extruder (``filament_map``, 1-based) that ``physical_extruder_map`` turns into that nozzle.
    """
    with zipfile.ZipFile(io.BytesIO(model_bytes)) as zin:
        cfg = json.loads(zin.read(PROJECT_SETTINGS_ENTRY))
        physical = [int(e) for e in cfg.get("physical_extruder_map") or []]
        if nozzle not in physical:
            raise ValueError(f"the project has no nozzle {nozzle} (physical_extruder_map {physical})")
        logical = str(physical.index(nozzle) + 1)
        count = len(cfg.get("filament_colour") or cfg.get("filament_map") or [0])
        cfg["filament_map_mode"] = "Manual"
        cfg["filament_map"] = [logical] * count
        root = ET.fromstring(zin.read(MODEL_SETTINGS_ENTRY))
        for plate_elem in root.findall("plate"):
            metas = plate_elem.findall("metadata")
            meta = {m.get("key"): m for m in metas}
            if "plater_id" not in meta or meta["plater_id"].get("value") != str(plate):
                continue
            for key, value in (("filament_map_mode", "Manual"), ("filament_maps", " ".join([logical] * count))):
                if key in meta:
                    meta[key].set("value", value)
                else:  # after the plate's other metadata, before its instances
                    elem = ET.Element("metadata", {"key": key, "value": value})
                    plate_elem.insert(list(plate_elem).index(metas[-1]) + 1, elem)
                    metas.append(elem)
        changed = {
            MODEL_SETTINGS_ENTRY: ET.tostring(root, encoding="UTF-8", xml_declaration=True),
            PROJECT_SETTINGS_ENTRY: json.dumps(cfg, indent=4).encode(),
        }
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                zout.writestr(info, changed.get(info.filename) or zin.read(info.filename))
    return out.getvalue()


def sliced_nozzles(output: bytes) -> dict[int, int | None]:
    """The nozzle each filament of a sliced 3MF prints with: slice_info's ``group_id`` through
    ``physical_extruder_map``, as the printer gets it; None for a filament without one."""
    with zipfile.ZipFile(io.BytesIO(output)) as zf:
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY))
            info = ET.fromstring(zf.read(SLICE_INFO_ENTRY))
        except (KeyError, ValueError, ET.ParseError):
            return {}
    physical = cfg.get("physical_extruder_map") or []
    found: dict[int, int | None] = {}
    for filament in info.iter("filament"):
        try:
            slot = int(filament.get("id") or "")
        except ValueError:
            continue
        try:
            found[slot] = int(physical[int(filament.get("group_id") or "")])
        except (ValueError, IndexError):
            found[slot] = None
    return found


def filament_colours(source: bytes | Path) -> list[str]:
    """``filament_colour`` of the project, as lowercase hex without ``#`` (index 0 = filament 1)."""
    with _zip(source) as zf:
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY))
        except (KeyError, ValueError):
            return []
    return [str(c).strip().lstrip("#").lower()[:6] for c in cfg.get("filament_colour") or []]


def pick_filament(colours: list[str], hex_: str, tolerance: float) -> int | None:
    """The filament (1-based) of this colour: an exact match, else the closest within ``tolerance``."""
    from backend.app.farm.plan.service import color_distance

    hex_ = hex_.lower()
    if hex_ in colours:
        return colours.index(hex_) + 1
    near = [(color_distance(c, hex_), i) for i, c in enumerate(colours, 1) if len(c) == 6]
    near = [(d, i) for d, i in near if d <= tolerance]
    return min(near)[1] if near else None


def variant_filament(source: bytes | Path, plate: int, colour_name: str, hex_: str, tolerance: float) -> int:
    """The filament to move the plate to for this colour, or 0 to slice the project as it is.

    0 also for a plate on several filaments (left alone, with a warning). Raises ``MissingColour``.
    """
    used = plate_filaments(source, plate)
    if len(used) > 1:
        logger.warning(
            "Colour variant %s of plate %s: the plate uses several filaments, sliced as it is", colour_name, plate
        )
        return 0
    filament = pick_filament(filament_colours(source), hex_, tolerance)
    if filament is None:
        raise MissingColour(f"il 3MF non ha un filamento {colour_name}: aggiungilo nel progetto")
    return 0 if used in (set(), {filament}) else filament


def move_plate_to(model_bytes: bytes, plate: int, filament: int) -> bytes:
    """A copy of the 3MF where the objects (and their parts) of the plate print with ``filament``."""
    with zipfile.ZipFile(io.BytesIO(model_bytes)) as zin:
        root = ET.fromstring(zin.read(MODEL_SETTINGS_ENTRY))
        for obj in _plate_objects(root, plate):
            for elem in [obj, *obj.findall("part")]:
                meta = _extruder(elem)
                if meta is not None:
                    meta.set("value", str(filament))
                elif elem is obj:
                    ET.SubElement(obj, "metadata", {"key": "extruder", "value": str(filament)})
        settings = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                zout.writestr(info, settings if info.filename == MODEL_SETTINGS_ENTRY else zin.read(info.filename))
    return out.getvalue()
