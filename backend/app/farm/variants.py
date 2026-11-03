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


def _object_filaments(obj: ET.Element) -> set[int]:
    """Filaments (1-based) an object prints with: its own, and its parts' where they set one."""
    own = _extruder(obj)
    default = int(own.get("value") or 1) if own is not None else 1
    used = {default}
    for part in obj.findall("part"):
        if part.get("subtype", "normal_part") != "normal_part":
            continue  # modifiers and negative volumes print nothing of their own
        meta = _extruder(part)
        if meta is not None and int(meta.get("value") or 0):
            used.add(int(meta.get("value")))
    return used


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
