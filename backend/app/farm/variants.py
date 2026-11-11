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
import re
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


def paint_states(code: str) -> set[int]:
    """The filaments (1-based) one triangle's ``paint_color`` paints, as OrcaSlicer reads it (Model.cpp
    ``set_triangle_from_string``, TriangleSelector ``deserialize``): its hex digits from the last, a nibble each.
    A nibble whose two low bits are 0 is a leaf, its state the two high bits (3: one more nibble, state = 3 + it,
    or with 15 another one, state = 18 + it); else it splits into (low bits + 1) children, read depth first.
    State 0 is unpainted (the part's own filament). Malformed data: what was read before it."""
    try:
        nibbles = [int(c, 16) for c in reversed(code.strip())]
    except ValueError:
        return set()
    states: set[int] = set()
    i, pending = 0, 1
    while pending and i < len(nibbles):
        n = nibbles[i]
        i += 1
        if n & 0b11:
            pending += n & 0b11  # this node becomes (sides + 1) children
            continue
        pending -= 1
        state = n >> 2
        if state == 3:
            if i >= len(nibbles):
                break
            state, i = nibbles[i] + 3, i + 1
            if state == 18:
                if i >= len(nibbles):
                    break
                state, i = nibbles[i] + 18, i + 1
        if state:
            states.add(state)
    return states


_MODEL_OBJECT = re.compile(rb"""<object\b[^>]*\bid\s*=\s*["'](\d+)["'][^>]*>""")
_COMPONENT = re.compile(rb"""<component\b[^>]*>""")
_PATH = re.compile(rb"""\bp:path\s*=\s*["']([^"']+)["']""")
_OBJECTID = re.compile(rb"""\bobjectid\s*=\s*["'](\d+)["']""")
_PAINT = re.compile(rb"""\bpaint_color\s*=\s*["']([0-9A-Fa-f]+)["']""")


def _object_body(data: bytes, object_id: str) -> bytes:
    """The ``<object id=...>`` element of a model file, as bytes (empty when it has none)."""
    for m in _MODEL_OBJECT.finditer(data):
        if m.group(1).decode() == object_id:
            end = data.find(b"</object>", m.end())
            return data[m.start() : end if end >= 0 else len(data)]
    return b""


def paint_filaments(zf: zipfile.ZipFile, object_ids: list[str]) -> set[int]:
    """The filaments the paint of these objects (``3D/3dmodel.model`` ids) uses: their meshes' ``paint_color``,
    inline or in the files their components point to (``p:path``)."""
    try:
        main = zf.read("3D/3dmodel.model")
    except KeyError:
        return set()
    files: dict[str, bytes] = {"3D/3dmodel.model": main}
    used: set[int] = set()
    for oid in dict.fromkeys(object_ids):
        body = _object_body(main, oid)
        meshes = [body]
        for comp in _COMPONENT.finditer(body):
            path, target = _PATH.search(comp.group(0)), _OBJECTID.search(comp.group(0))
            if target is None:
                continue
            name = path.group(1).decode().lstrip("/") if path else "3D/3dmodel.model"
            if name not in files:
                try:
                    files[name] = zf.read(name)
                except KeyError:
                    files[name] = b""
            meshes.append(_object_body(files[name], target.group(1).decode()))
        for mesh in meshes:
            for code in set(_PAINT.findall(mesh)):
                used |= paint_states(code.decode())
    return used


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

    A painted plate adds the filaments its paint uses (``paint_filaments``). On a dual-nozzle project left on
    automatic grouping the rows say no nozzle: each filament may take a slot of either nozzle, and the slice is then
    forced onto the nozzles chosen (``free_nozzle``, ``force_nozzle``). None when the project can't be read.
    """
    from backend.app.utils.threemf_tools import extract_nozzle_mapping_from_3mf

    with _zip(source) as zf:
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY))
        except (KeyError, ValueError):
            return None
        auto = _dual_auto(cfg)
        nozzles = {} if auto else extract_nozzle_mapping_from_3mf(zf) or {}
        try:
            objects = _plate_objects(ET.fromstring(zf.read(MODEL_SETTINGS_ENTRY)), plate)
        except KeyError:
            objects = []
        painted = paint_filaments(zf, [o.get("id") for o in objects]) if _painted(zf) else set()
    used = {filament} if filament else plate_filaments(source, plate) | painted
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
    """A dual-nozzle project on automatic grouping: the slot chosen for each filament of the plate (supports
    included) picks that filament's nozzle, and the slice is forced so (``force_nozzle``)."""
    with _zip(source) as zf:
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY))
        except (KeyError, ValueError):
            return False
    return _dual_auto(cfg) and bool(plate_slots(source, plate, filament))


def nozzle_key(choice: dict[int, int]) -> str:
    """Each filament's nozzle (slot -> the printer's extruder id: 0 right, 1 left) as the job, the queue and the
    output keep it: ``"1=1,2=0"``, the filaments in order."""
    return ",".join(f"{slot}={nozzle}" for slot, nozzle in sorted(choice.items()))


def as_nozzle_key(value) -> str | None:
    """A job's or an output's forced nozzles as ``nozzle_key``: one nozzle alone (as the first version kept it, for
    a plate on one filament) puts every filament there (``"*=n"``)."""
    if value is None or value == "":
        return None
    return f"*={value}" if isinstance(value, int) else str(value)


def job_nozzles(options: dict | None) -> str | None:
    """A farm job's chosen nozzles (``nozzle_key``, ``queue_options["nozzles"]``); a job queued with one nozzle for
    its one filament (the first version): all there."""
    options = options or {}
    return options.get("nozzles") or as_nozzle_key(options.get("nozzle"))


def nozzles_label(key: str) -> str:
    """The nozzles a slice is forced onto, as an output's name and the messages say them."""
    side = {0: "right", 1: "left"}
    choice = parse_nozzle_key(key)
    if len(set(choice.values())) == 1:
        return f"{side.get(next(iter(choice.values())), 'other')} nozzle"
    return "filament " + ", ".join(f"{slot} {side.get(n, n)}" for slot, n in sorted(choice.items(), key=str))


def parse_nozzle_key(key: str) -> dict[int | str, int]:
    """``nozzle_key`` read back; ``"*=n"`` puts every filament on nozzle n (a job queued when one filament was all
    a plate could choose for). ValueError when it isn't one."""
    out: dict[int | str, int] = {}
    for pair in key.split(","):
        slot, _, nozzle = pair.partition("=")
        out["*" if slot == "*" else int(slot)] = int(nozzle)
    if not out:
        raise ValueError("no nozzle chosen")
    return out


def force_nozzle(model_bytes: bytes, plate: int, key: str) -> bytes:
    """A copy of the 3MF whose plate prints each filament on its chosen nozzle (``nozzle_key``).

    The slicers' Manual filament grouping, on the plate and in the project: each filament on the logical extruder
    (``filament_map``, 1-based) that ``physical_extruder_map`` turns into its nozzle. A filament not chosen keeps
    its map (the plate's, else the project's): the plate doesn't print it.
    """
    choice = parse_nozzle_key(key)
    with zipfile.ZipFile(io.BytesIO(model_bytes)) as zin:
        cfg = json.loads(zin.read(PROJECT_SETTINGS_ENTRY))
        physical = [int(e) for e in cfg.get("physical_extruder_map") or []]
        for nozzle in choice.values():
            if nozzle not in physical:
                raise ValueError(f"the project has no nozzle {nozzle} (physical_extruder_map {physical})")
        count = len(cfg.get("filament_colour") or cfg.get("filament_map") or [0])
        root = ET.fromstring(zin.read(MODEL_SETTINGS_ENTRY))
        plate_elem = next(
            (
                el
                for el in root.findall("plate")
                if {m.get("key"): m.get("value") for m in el.findall("metadata")}.get("plater_id") == str(plate)
            ),
            None,
        )
        plate_maps = (
            ({m.get("key"): m.get("value") for m in plate_elem.findall("metadata")}.get("filament_maps") or "").split()
            if plate_elem is not None
            else []
        )
        project_maps = [str(v) for v in cfg.get("filament_map") or []]

        def logical(slot: int) -> str:
            nozzle = choice.get(slot, choice.get("*"))
            if nozzle is not None:
                return str(physical.index(nozzle) + 1)
            kept = plate_maps if len(plate_maps) >= slot else project_maps
            return kept[slot - 1] if len(kept) >= slot else "1"

        values = [logical(slot) for slot in range(1, count + 1)]
        cfg["filament_map_mode"] = "Manual"
        cfg["filament_map"] = values
        if plate_elem is not None:
            metas = plate_elem.findall("metadata")
            meta = {m.get("key"): m for m in metas}
            for key, value in (("filament_map_mode", "Manual"), ("filament_maps", " ".join(values))):
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


def wrong_nozzles(output: bytes, key: str) -> dict[int, int | None] | None:
    """The filaments of a slice not on the nozzle chosen for them (``nozzle_key``), with the nozzle they are on;
    None when every one is. A slice that doesn't say (no slice_info, no ``group_id``) is never on the right one."""
    choice = parse_nozzle_key(key)
    on = sliced_nozzles(output)
    if not on:
        return {0: None}
    wrong = {slot: n for slot, n in on.items() if n is None or n != choice.get(slot, choice.get("*"))}
    return wrong or None


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
