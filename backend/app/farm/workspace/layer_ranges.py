"""An object's height ranges in an edited copy (fork): OrcaSlicer's Height Range Modifier (GUI_ObjectLayers.cpp).

Orca keeps them in ``Metadata/layer_config_ranges.xml`` (bbs_3mf ``_add_layer_config_ranges_file_to_archive``):
``<objects>`` with an ``<object id="N">`` per object that has ranges, N its place among the model's objects from 1
(the order its build items first name them: bbs_3mf creates the model objects as it reads ``<build>``), each with
``<range min_z max_z>`` and an ``<option opt_key>`` per setting of that range (``layer_height``, ``extruder`` and
the region settings: TabPrintLayer). A copy rewrites the edited objects' blocks and nothing else; it adds the file
when the project has none, and the result is read back.
"""

from __future__ import annotations

import re

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.workspace import object_settings
from backend.app.farm.workspace.placement import InvalidPlacement, _build_items, _number

MAX_RANGES = 100  # per object
MAX_Z = 10_000.0  # mm


class InvalidLayerRanges(ValueError):
    """Ranges the copy refuses (the message says which and why)."""


class LayerRange(BaseModel):
    min_z: float = Field(ge=0, le=MAX_Z)
    max_z: float = Field(ge=0, le=MAX_Z)
    options: dict[str, str | int | float | bool] = Field(default_factory=dict, max_length=300)


class RangeEdit(BaseModel):
    """An object's height ranges as they are to be: all of them (none: the object has no ranges any more)."""

    object_id: str = Field(pattern=r"^[0-9]{1,9}$")
    ranges: list[LayerRange] = Field(default_factory=list, max_length=MAX_RANGES)


def object_order(model: bytes) -> list[str]:
    """The model's objects in the order Orca numbers them: their build items' first mention."""
    try:
        return list(dict.fromkeys(item.get("objectid", "") for item in _build_items(model)))
    except InvalidPlacement as exc:
        raise InvalidLayerRanges(f"the model's objects can't be read: {exc}") from exc


def parse(xml: bytes | None, order: list[str]) -> dict[str, list[dict]]:
    """Each object's ranges in the file: ``{min_z, max_z, options}``, by object id."""
    if not xml:
        return {}
    try:
        root = SafeET.fromstring(xml)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidLayerRanges(f"layer_config_ranges.xml is not valid XML: {exc}") from exc
    out: dict[str, list[dict]] = {}
    for obj in root.iter("object"):
        index = int(obj.get("id") or 0)
        if not 1 <= index <= len(order):
            continue  # Orca drops the ranges of an object that isn't there
        ranges = [
            {
                "min_z": float(r.get("min_z") or 0),
                "max_z": float(r.get("max_z") or 0),
                "options": {o.get("opt_key", ""): (o.text or "") for o in r if o.tag == "option"},
            }
            for r in obj
            if r.tag == "range"
        ]
        if ranges:
            out[order[index - 1]] = sorted(ranges, key=lambda r: (r["min_z"], r["max_z"]))
    return out


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _block(index: int, ranges: list[LayerRange], options: list[dict[str, str]]) -> bytes:
    """An object's block as Orca writes it (one space more each level, options in key order)."""
    lines = [f' <object id="{index}">']
    for r, opts in zip(ranges, options, strict=True):
        lines.append(f'  <range min_z="{_number(r.min_z)}" max_z="{_number(r.max_z)}">')
        lines += [f'   <option opt_key="{k}">{_escape(v)}</option>' for k, v in sorted(opts.items())]
        lines.append("  </range>")
    lines.append(" </object>")
    return ("\n".join(lines) + "\n").encode()


def _checked(edit: RangeEdit, cfg: dict, slicer: str, filaments: int) -> list[dict[str, str]]:
    """Each range's options as the copy writes them; refused: a range ending below its start, ranges out of order,
    one without a layer height, a setting a range can't have (TabPrintLayer: layer height, filament and the region's
    settings)."""
    _, regions = object_settings.model_keys()
    ranges = edit.ranges
    out = []
    for i, r in enumerate(ranges):
        # as Orca's editor: an end below the start refused, overlaps kept (its slicing trims them, Slicing.cpp); the
        # ranges are a map sorted by (min, max), so two the same or out of order are refused
        if r.max_z < r.min_z:
            raise InvalidLayerRanges(f"object {edit.object_id}: range {i + 1} ends below where it starts")
        if i and (r.min_z, r.max_z) <= (ranges[i - 1].min_z, ranges[i - 1].max_z):
            raise InvalidLayerRanges(f"object {edit.object_id}: ranges {i} and {i + 1} are the same or out of order")
        if "layer_height" not in r.options:
            # Orca reads every range's layer_height unchecked (Slicing.cpp): without it both CLIs crash
            raise InvalidLayerRanges(f"object {edit.object_id}: range {i + 1} has no layer height")
        opts: dict[str, str] = {}
        for key, value in r.options.items():
            try:
                if key == "extruder":
                    opts[key] = object_settings.check_filament(value, True, filaments)
                elif key == "layer_height":
                    opts[key] = object_settings.check_value(key, value, None, cfg.get(key), False, slicer)
                elif key in regions:
                    opts[key] = object_settings.check_value(key, value, None, cfg.get(key), True, slicer)
                else:
                    raise InvalidLayerRanges(f"object {edit.object_id}: {key} is not a setting a height range can have")
            except object_settings.InvalidObjectSetting as exc:
                raise InvalidLayerRanges(f"object {edit.object_id}, range {i + 1}: {exc}") from exc
        out.append(opts)
    return out


_OBJECT_BLOCK = re.compile(rb"[ \t]*<object\b[^>]*>.*?</object>[ \t]*\r?\n?", re.S)
_ID = re.compile(rb"""\sid\s*=\s*["'](\d+)["']""")
EMPTY = b'<?xml version="1.0" encoding="utf-8"?>\n<objects>\n</objects>\n'


def apply(
    xml: bytes | None, model: bytes, edits: list[RangeEdit], cfg: dict, slicer: str, filaments: int
) -> tuple[bytes, list[dict]]:
    """``layer_config_ranges.xml`` (none: a new one) with the edited objects' ranges, every other byte kept; what
    changed: ``{key: "layer_ranges", object_id, ranges}`` (how many it has now)."""
    order = object_order(model)
    index = {oid: i + 1 for i, oid in enumerate(order)}
    blocks: dict[int, bytes] = {}
    for e in edits:
        if e.object_id not in index:
            raise InvalidLayerRanges(f"the model has no object {e.object_id}")
        if index[e.object_id] in blocks:
            raise InvalidLayerRanges(f"object {e.object_id}'s ranges are given twice")
        n = index[e.object_id]
        blocks[n] = _block(n, e.ranges, _checked(e, cfg, slicer, filaments)) if e.ranges else b""
    before = parse(xml, order)
    text = (xml or EMPTY).replace(b"<objects/>", b"<objects>\n</objects>")
    end = text.rfind(b"</objects>")
    if end < 0:
        raise InvalidLayerRanges("layer_config_ranges.xml has no <objects>")
    # an edited object's block replaced where it is (or dropped); a new one before the next object's, or at the end
    out, last, placed = [], 0, set()
    for m in _OBJECT_BLOCK.finditer(text, 0, end):
        found = _ID.search(m.group(0))
        n = int(found.group(1)) if found else 0
        out.append(text[last : m.start()])
        for k in sorted(k for k in blocks if k < n and k not in placed):
            out.append(blocks[k])
            placed.add(k)
        out.append(blocks[n] if n in blocks else m.group(0))
        placed.add(n)
        last = m.end()
    tail = b"".join(blocks[k] for k in sorted(blocks) if k not in placed)
    new = b"".join(out) + text[last:end] + tail + text[end:]
    # read back: the edited objects as asked, the others as they were
    after = parse(new, order)
    edited = {e.object_id for e in edits}
    if any(after.get(oid) != before.get(oid) for oid in order if oid not in edited):
        raise InvalidLayerRanges("layer_config_ranges.xml changed beyond the edited objects")
    for e in edits:
        want = [(float(_number(r.min_z)), float(_number(r.max_z))) for r in e.ranges]
        if [(r["min_z"], r["max_z"]) for r in after.get(e.object_id, [])] != want:
            raise InvalidLayerRanges(f"object {e.object_id}'s ranges don't read back")
    return new, [{"key": "layer_ranges", "object_id": e.object_id, "ranges": len(e.ranges)} for e in edits]
