"""A part deleted or its type changed in an edited copy (fork): OrcaSlicer's Delete of a part, Change Type and
Precise Seam Type (GUI_ObjectList.cpp ``del_subobject_from_object``, ``set_volume_type``; Model.cpp
``delete_volume``, ``sort_volumes``).

A part is a ``<component>`` of its object in ``3D/3dmodel.model`` (its ``objectid`` the part's id) and a
``<part id subtype>`` of the object in ``model_settings.config``; Orca makes the object's volumes in its components'
order. bbs_3mf writes a Precise Seam as ``subtype="modifier_part"`` with ``precise_seam_type`` metadata (so a slicer
without seams reads a modifier), its settings dormant under ``precise_seam_config:``.

- Delete: the part's component and its block go; the last solid part can't (Orca: "Deleting the last solid part is
  not allowed."). When one part is left, its transform goes into every instance and its settings to the object, as
  ``delete_volume`` and ``del_subobject_from_object`` do. Its mesh stays in its file (other objects may share it):
  the slicer reads only what a component names.
- Change Type: the subtype (and the seam's type and dormant settings) rewritten; the last solid part can't change,
  nor a text or SVG part become a support or a seam. The object's parts are then sorted as ``sort_volumes(true)``
  does: parts, negative parts, modifiers, blockers, enforcers, then seams (strong ones first); a part changing group
  goes last in its new one.

Refused: a cut object's parts (``cut_information.xml`` names its connectors by their place among the volumes) and an
object without components (its parts are ranges of one mesh). Everything else of both files stays byte for byte, and
the edited objects are read back.
"""

from __future__ import annotations

import re
from typing import Literal

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.detect import SLICER_ORCA
from backend.app.farm.workspace import object_settings
from backend.app.farm.workspace.placement import (
    _COMPONENT,
    _ID,
    _KEY,
    _METADATA,
    _OBJECT,
    _OBJECT_ID,
    _PART,
    _TRANSFORM,
    _VALUE,
    IDENTITY,
    InstanceTransform,
    InvalidPlacement,
    PartTransform,
    _as4,
    _attr,
    _build_items,
    _element,
    _move_matrix,
    _mul,
    _numbers,
    format_matrix,
    place,
)
from backend.app.utils.xml_prefixes import declared

MAX_OPS = 500
PART_TYPES = ("normal_part", "negative_part", "modifier_part", "support_blocker", "support_enforcer")
SEAM_TYPES = (
    "precise_seam_center",
    "precise_seam_left",
    "precise_seam_right",
    "precise_seam_enforced",
    "precise_seam_blocked",
    "precise_seam_neutral",
)
SEAM_KEY = "precise_seam_type"
DORMANT = "precise_seam_config:"
# a part's metadata that isn't a setting (bbs_3mf's keys); every other key is one of its settings
NOT_SETTINGS = frozenset(
    {
        "name",
        "matrix",
        "source_file",
        "source_object_id",
        "source_volume_id",
        "source_offset_x",
        "source_offset_y",
        "source_offset_z",
        "source_in_inches",
        "source_in_meters",
        "mesh_shared",
        "volume_type",
        "part_type",
        SEAM_KEY,
        "texture_displacement",
    }
)
CUT_ENTRY = "Metadata/cut_information.xml"
_SUBTYPE = re.compile(rb"""\ssubtype\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_EMBOSSED = re.compile(rb"<slic3rpe:(?:text|shape)\b")


class InvalidPartOp(ValueError):
    """A part operation the copy refuses (the message says which and why)."""


class PartOp(BaseModel):
    """``delete``: the part goes; ``type``: it becomes ``type`` (a seam's subtype among them)."""

    op: Literal["delete", "type"]
    object_id: str = Field(pattern=r"^[0-9]{1,9}$")
    part_id: str = Field(pattern=r"^[0-9]{1,9}$")
    type: (
        Literal[
            "normal_part",
            "negative_part",
            "modifier_part",
            "support_blocker",
            "support_enforcer",
            "precise_seam_center",
            "precise_seam_left",
            "precise_seam_right",
            "precise_seam_enforced",
            "precise_seam_blocked",
            "precise_seam_neutral",
        ]
        | None
    ) = None


def _group(t: str) -> int:
    """sort_volumes(true)'s order: the type's place, a seam by its group (strong before weak)."""
    if t in SEAM_TYPES:
        return len(PART_TYPES) + (0 if SEAM_TYPES.index(t) < 3 else 1)
    return PART_TYPES.index(t)


def _key(m: re.Match) -> str:
    return _attr(m.group(0), _KEY) or ""


def type_of(block: bytes) -> str:
    """A part's type from its block: its subtype, or its seam's when it is a modifier with ``precise_seam_type``."""
    open_end = block.find(b">") + 1
    subtype = _attr(block[:open_end], _SUBTYPE) or "normal_part"
    seam = next((_attr(m.group(0), _VALUE) for m in _METADATA.finditer(block) if _key(m) == SEAM_KEY), None)
    if subtype == "modifier_part" and seam in SEAM_TYPES:
        return seam
    return subtype if subtype in PART_TYPES else "normal_part"  # type_from_string: unknown is a model part


def cut_objects(cut_xml: bytes | None, order: list[str]) -> set[str]:
    """The cut objects ``cut_information.xml`` names (by their place in the model, from 1): those with a cut id
    (``ModelObject::is_cut``; Bambu Studio writes id 0 for every object never cut)."""
    if not cut_xml:
        return set()
    try:
        root = SafeET.fromstring(cut_xml)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidPartOp(f"cut_information.xml is not valid XML: {exc}") from exc
    out = set()
    for obj in root.iter("object"):
        n, cut = obj.get("id") or "", obj.find("cut_id")
        if n.isdigit() and 1 <= int(n) <= len(order) and cut is not None and (cut.get("id") or "0") != "0":
            out.add(order[int(n) - 1])
    return out


class _Object:
    """One object's components in the model and parts in the settings, as they are now."""

    def __init__(self, model: bytes, settings: bytes, object_id: str):
        try:
            m_start, m_end = _element(model, _OBJECT, b"</object>", object_id, 0, model.rfind(b"</resources>"))
            s_start, s_end = _element(settings, _OBJECT, b"</object>", object_id, 0, len(settings))
        except InvalidPlacement as exc:
            raise InvalidPartOp(str(exc)) from exc
        self.components = list(_COMPONENT.finditer(model, m_start, m_end))
        if not self.components:
            raise InvalidPartOp(f"object {object_id}'s parts are not components: they can't be changed in a copy")
        self.ids = [_attr(c.group(0), _OBJECT_ID) or "" for c in self.components]
        self.parts: list[tuple[str, int, int]] = []
        pos = s_start
        while (m := _PART.search(settings, pos, s_end)) is not None:
            closed = m.group(0).endswith(b"/>")
            end = m.end() if closed else settings.find(b"</part>", m.end(), s_end) + len(b"</part>")
            if end < m.end():
                raise InvalidPartOp(f"object {object_id} has a part that is not closed")
            self.parts.append((_attr(m.group(0), _ID) or "", m.start(), end))
            pos = end
        self.types = {pid: type_of(settings[a:b]) for pid, a, b in self.parts}

    def part(self, part_id: str) -> tuple[int, int]:
        found = [(a, b) for pid, a, b in self.parts if pid == part_id]
        if len(found) != 1 or self.ids.count(part_id) != 1:
            raise InvalidPartOp(f"the object has no part {part_id}")
        return found[0]


def _reorder(data: bytes, spans: list[tuple[int, int]], order: list[int]) -> bytes:
    """``data`` with the elements at ``spans`` (whole lines, in file order) rearranged as ``order``."""
    lines = [object_settings._line(data, a, b) for a, b in spans]
    out, last = [], 0
    for (a, b), i in zip(lines, order, strict=True):
        out += [data[last:a], data[lines[i][0] : lines[i][1]]]
        last = b
    out.append(data[last:])
    return b"".join(out)


def _sort(model: bytes, settings: bytes, object_id: str, moved: str | None) -> tuple[bytes, bytes]:
    """The object's components (and its parts' blocks, when they follow them) in sort_volumes(true)'s order, the
    part ``moved`` last of its group (it changed group: move_volume_to_end)."""
    obj = _Object(model, settings, object_id)
    order = [i for i, pid in enumerate(obj.ids) if pid != moved] + [i for i, pid in enumerate(obj.ids) if pid == moved]
    order.sort(key=lambda i: _group(obj.types.get(obj.ids[i], "normal_part")))  # stable, as std::stable_sort
    if order == list(range(len(obj.ids))):
        return model, settings
    model = _reorder(model, [(c.start(), c.end()) for c in obj.components], order)
    if [pid for pid, _, _ in obj.parts] == obj.ids:  # the blocks in the volumes' order: they follow
        settings = _reorder(settings, [(a, b) for _, a, b in obj.parts], order)
    return model, settings


def _set_type(settings: bytes, start: int, end: int, old: str, new: str) -> bytes:
    """The part's block with its subtype, seam type and (into or out of a seam) its settings' dormant keys."""
    block = settings[start:end]
    open_end = block.find(b">") + 1
    tag = block[:open_end]
    stored = f' subtype="{"modifier_part" if new in SEAM_TYPES else new}"'.encode()
    sub = _SUBTYPE.search(tag)
    tag = tag[: sub.start()] + stored + tag[sub.end() :] if sub else tag[:5] + stored + tag[5:]
    block = tag + block[open_end:]
    newline = b"\r\n" if b"\r\n" in settings else b"\n"
    seam_lines = [m for m in _METADATA.finditer(block) if _key(m) == SEAM_KEY]
    if new in SEAM_TYPES and seam_lines:
        m = seam_lines[0]
        v = _VALUE.search(m.group(0))
        block = block[: m.start() + v.start()] + f' value="{new}"'.encode() + block[m.start() + v.end() :]
    elif new in SEAM_TYPES:
        # right after the part's opening line, as bbs_3mf writes it, at its metadata's indentation
        first = _METADATA.search(block, len(tag))
        line_start = block.rfind(b"\n", 0, first.start()) + 1 if first else -1
        part_indent = settings[settings.rfind(b"\n", 0, start) + 1 : start]
        indent = (
            block[line_start : first.start()]
            if first and not block[line_start : first.start()].strip()
            else part_indent + b"  "
        )
        block = tag + newline + indent + f'<metadata key="{SEAM_KEY}" value="{new}"/>'.encode() + block[len(tag) :]
    else:
        for m in reversed(seam_lines):
            a, b = object_settings._line(block, m.start(), m.end())
            block = block[:a] + block[b:]
    if (old in SEAM_TYPES) != (new in SEAM_TYPES):
        # Orca writes a seam's settings as dormant ones, and reads them back as its own when it is a part again
        for m in reversed(list(_METADATA.finditer(block))):
            key = _key(m)
            if key in NOT_SETTINGS:
                continue
            if new in SEAM_TYPES:
                renamed = key if key.startswith(DORMANT) else DORMANT + key
            else:
                renamed = key.removeprefix(DORMANT)
            if renamed != key:
                k = _KEY.search(m.group(0))
                block = block[: m.start() + k.start()] + f' key="{renamed}"'.encode() + block[m.start() + k.end() :]
    return settings[:start] + block + settings[end:]


def _collapse(model: bytes, settings: bytes, object_id: str, cfg: dict, slicer: str) -> tuple[bytes, bytes]:
    """delete_volume with one volume left: its transform into every instance (the component then the identity);
    del_subobject_from_object: its settings the object's."""
    obj = _Object(model, settings, object_id)
    comp, part_id = obj.components[0], obj.ids[0]
    old = _TRANSFORM.search(comp.group(0))
    spec = _numbers((old.group(1) or old.group(2) or b"").decode("ascii") if old else None)
    if spec != IDENTITY:
        items = [i for i in _build_items(model) if i.get("objectid") == object_id]
        moved = []
        for n, item in enumerate(items):
            m = _mul(_as4(_numbers(item.get("transform"))), _as4(spec))
            matrix = [m[r][c] for c in range(4) for r in range(3)]  # back to 3MF's 12 numbers, by columns
            moved.append(InstanceTransform(object_id=object_id, instance=n, matrix=matrix))
        try:
            model, _ = place(model, moved)
            comp = _Object(model, settings, object_id).components[0]
            tag = comp.group(0)
            old = _TRANSFORM.search(tag)
            new_tag = tag[: old.start()] + f' transform="{format_matrix(list(IDENTITY))}"'.encode() + tag[old.end() :]
            model = model[: comp.start()] + new_tag + model[comp.end() :]
            identity = PartTransform(object_id=object_id, part_id=part_id, matrix=list(IDENTITY))
            settings, _ = _move_matrix(settings, identity, spec)
        except InvalidPlacement as exc:
            raise InvalidPartOp(f"object {object_id}'s last part can't go into its instances: {exc}") from exc
    # its own settings the object's (object->config.apply(volume->config), then the volume's reset)
    p_start, p_end = _Object(model, settings, object_id).part(part_id)
    own = [(_key(m), object_settings._value_of(m.group(0))) for m in _METADATA.finditer(settings, p_start, p_end)]
    own = [(k, v) for k, v in own if k not in NOT_SETTINGS and not k.startswith(DORMANT)]
    if own:
        # a part's filament 0 is "its object's": nothing to give the object
        edits = [
            object_settings.ObjectSetting(object_id=object_id, key=k, value=v)
            for k, v in own
            if not (k == "extruder" and (v or "0").strip() == "0")
        ]
        edits += [object_settings.ObjectSetting(object_id=object_id, part_id=part_id, key=k) for k, _ in own]
        try:
            settings, _ = object_settings.apply(settings, cfg, edits, slicer)
        except object_settings.InvalidObjectSetting as exc:
            raise InvalidPartOp(f"object {object_id}'s last part's settings can't be the object's: {exc}") from exc
    return model, settings


def apply(
    model: bytes, settings: bytes, ops: list[PartOp], cfg: dict, slicer: str, cut: set[str] | None = None
) -> tuple[bytes, bytes, list[dict]]:
    """The model and ``model_settings.config`` with the parts deleted and retyped, in order; what changed:
    ``{key: "part", op, object_id, part_id, before, after}`` (a deleted part's ``after``: None)."""
    if len(ops) > MAX_OPS:
        raise InvalidPartOp(f"too many part changes at once (at most {MAX_OPS})")
    applied: list[dict] = []
    expected: dict[str, dict[str, str]] = {}  # each edited object's parts and their types, as they are to be
    for op in ops:
        if op.object_id in (cut or set()):
            raise InvalidPartOp(f"object {op.object_id} is cut: its parts can't change in a copy")
        obj = _Object(model, settings, op.object_id)
        start, end = obj.part(op.part_id)
        state = expected.setdefault(op.object_id, {pid: obj.types[pid] for pid in obj.ids if pid in obj.types})
        old = obj.types[op.part_id]
        solid = sum(1 for pid in obj.ids if obj.types.get(pid) == "normal_part")
        if op.op == "delete":
            if old == "normal_part" and solid == 1:
                raise InvalidPartOp(f"object {op.object_id}: deleting the last solid part is not allowed")
            comp = obj.components[obj.ids.index(op.part_id)]
            a, b = object_settings._line(model, comp.start(), comp.end())
            model = model[:a] + model[b:]
            a, b = object_settings._line(settings, start, end)
            settings = settings[:a] + settings[b:]
            if len(_Object(model, settings, op.object_id).components) == 1:
                model, settings = _collapse(model, settings, op.object_id, cfg, slicer)
            state.pop(op.part_id)
            applied.append(
                {
                    "key": "part",
                    "op": "delete",
                    "object_id": op.object_id,
                    "part_id": op.part_id,
                    "before": old,
                    "after": None,
                }
            )
            continue
        new = op.type
        if new is None:
            raise InvalidPartOp(f"part {op.part_id}: no type given")
        if new in SEAM_TYPES and slicer != SLICER_ORCA:
            raise InvalidPartOp("a Precise Seam is OrcaSlicer's: this project's slicer has none")
        if new == old:
            continue
        if old == "normal_part" and solid == 1:
            raise InvalidPartOp(f"object {op.object_id}: the type of the last solid object part cannot be changed")
        if (new in ("support_blocker", "support_enforcer") or new in SEAM_TYPES) and _EMBOSSED.search(
            settings, start, end
        ):
            raise InvalidPartOp(f"part {op.part_id} is a text or SVG part: it can't be a support or a seam")
        settings = _set_type(settings, start, end, old, new)
        crossed = (old in SEAM_TYPES) != (new in SEAM_TYPES) or (old in SEAM_TYPES and _group(old) != _group(new))
        model, settings = _sort(model, settings, op.object_id, op.part_id if crossed else None)
        state[op.part_id] = new
        applied.append(
            {"key": "part", "op": "type", "object_id": op.object_id, "part_id": op.part_id, "before": old, "after": new}
        )
    try:
        SafeET.fromstring(declared(settings))
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidPartOp(f"model_settings.config doesn't read back: {exc}") from exc
    for object_id, state in expected.items():
        obj = _Object(model, settings, object_id)
        if obj.types != state or sorted(obj.ids) != sorted(state):
            raise InvalidPartOp(f"object {object_id}'s parts don't read back as changed")
        retyped = any(a["op"] == "type" and a["object_id"] == object_id for a in applied)
        groups = [_group(obj.types[pid]) for pid in obj.ids]
        if retyped and groups != sorted(groups):
            raise InvalidPartOp(f"object {object_id}'s parts are not in Orca's order")
    return model, settings, applied
