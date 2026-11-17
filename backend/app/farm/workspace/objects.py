"""Objects and instances deleted, and their flags and names, in an edited copy (fork): OrcaSlicer's Delete, Delete
All, the plate's Delete All (Selection::erase, ObjectList::delete_from_model_and_list), Printable
(toggle_printable_state), Auto Drop and Rename (update_name_in_model).

- An instance is a build item of ``3D/3dmodel.model`` (the n-th of its object, instance n): Printable is its
  ``printable`` attribute ("1" / "0", as bbs_3mf writes it).
- Auto Drop is the ``auto_drop`` attribute of the object's ``<object>`` in ``model_settings.config``; a name is the
  object's (or part's) ``name`` metadata there. Renaming an object of one part renames the part too, as Orca does;
  a name with ``<>:/\\|?*"`` is refused (Plater::has_illegal_filename_characters).
- Deleting an instance drops its build item, its plate's ``<model_instance>`` and its ``<assemble_item>``; the
  object's later instances are numbered again from 0, as Orca's instance list is. An object losing its last
  instance is deleted: its ``<object>`` resource (unless another object's component names it), its block in
  ``model_settings.config``. Its meshes stay in their files (other objects may share them).
- Files that name objects by their place in the model (from 1, the order the build items first name them) are
  numbered again: ``layer_config_ranges.xml``, ``cut_information.xml``, ``layer_heights_profile.txt``,
  ``brim_ear_points.txt``; a deleted object's entries go.

Instance numbers are the file's (before any deletion): flags and names apply first, then every deletion at once.
Everything else of every file stays byte for byte, and the result is read back.
"""

from __future__ import annotations

import re
from typing import Literal

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.workspace import object_settings
from backend.app.farm.workspace.placement import (
    _COMPONENT,
    _ID,
    _ITEM,
    _KEY,
    _METADATA,
    _OBJECT,
    _OBJECT_ID,
    _PART,
    _TRANSFORM,
    _VALUE,
    InvalidPlacement,
    _attr,
    _build_items,
    _build_span,
    _element,
    check_matrix,
    format_matrix,
)
from backend.app.farm.workspace.plate_objects import (
    _ANY_UUID,
    _UUID,
    BUILD,
    InvalidCopy,
    _append_items,
    _fresh,
    _set,
    copy_objects,
)
from backend.app.farm.workspace.project import LAYER_RANGES_ENTRY
from backend.app.utils.xml_prefixes import declared

MAX_OPS = 2000
MAX_NAME = 255
ILLEGAL = '<>:/\\|?*"'
CUT_ENTRY = "Metadata/cut_information.xml"
HEIGHTS_ENTRY = "Metadata/layer_heights_profile.txt"
BRIM_EARS_ENTRY = "Metadata/brim_ear_points.txt"
XML_INDEXED = (LAYER_RANGES_ENTRY, CUT_ENTRY)
TEXT_INDEXED = (HEIGHTS_ENTRY, BRIM_EARS_ENTRY)
INDEXED = XML_INDEXED + TEXT_INDEXED


def _attr_rx(name: bytes) -> re.Pattern:
    return re.compile(rb"\s" + name + rb"""\s*=\s*(?:"([^"]*)"|'([^']*)')""")


_PRINTABLE = _attr_rx(rb"printable")
_AUTO_DROP = _attr_rx(rb"auto_drop")
_PATH = _attr_rx(rb"p:path")
_OBJECT_ID_ATTR = _attr_rx(rb"object_id")
_INSTANCE_ID_ATTR = _attr_rx(rb"instance_id")
_INSTANCE_BLOCK = re.compile(rb"[ \t]*<model_instance>.*?</model_instance>[ \t]*\r?\n?", re.S)
_ASSEMBLE_ITEM = re.compile(rb"<assemble_item\b[^>]*>")
_INDEXED_BLOCK = re.compile(rb"[ \t]*<object\b[^>]*>.*?</object>[ \t]*\r?\n?", re.S)
_INDEXED_LINE = re.compile(rb"^object_id=(\d+)\|", re.M)


class InvalidObjectOp(ValueError):
    """An operation the copy refuses (the message says which and why)."""


class ObjectOp(BaseModel):
    """``delete``: an instance (``instance``) or the whole object; ``printable``: an instance's, or every instance's;
    ``auto_drop``: the object's; ``rename``: the object's name or (``part_id``) a part's. Additions, made first:
    ``add_instance``: one more instance at ``matrix``; ``copy``: a new object ``new_id`` (from the document's
    ``next_object_id``) copying the object, with an instance at each of ``matrices``."""

    op: Literal["delete", "printable", "auto_drop", "rename", "add_instance", "copy"]
    object_id: str = Field(pattern=r"^[0-9]{1,9}$")
    instance: int | None = Field(default=None, ge=0, le=100_000)
    part_id: str | None = Field(default=None, pattern=r"^[0-9]{1,9}$")
    value: bool | str | None = None
    matrix: list[float] | None = Field(default=None, min_length=12, max_length=12)
    new_id: str | None = Field(default=None, pattern=r"^[0-9]{1,9}$")
    matrices: list[list[float]] | None = Field(default=None, min_length=1, max_length=1000)


ADDITIONS = ("add_instance", "copy")


def add(
    model: bytes, settings: bytes, ops: list[ObjectOp], indexed: dict[str, bytes | None] | None = None
) -> tuple[bytes, bytes, dict[str, bytes], list[dict]]:
    """The model and ``model_settings.config`` with the additions made, in order: a new instance as Plater's
    increase_instances makes it (a copy of the object's last build item, printable, at its transform); a new object as
    Model::add_object (plate_objects.copy_objects: its resource sharing the meshes, its settings, its build items).
    A copy also takes its object's lines of ``layer_heights_profile.txt`` and ``brim_ear_points.txt`` (its height
    ranges come as the copy's own edits). The plates they go on come as ``instance_plates``. What changed."""
    applied: list[dict] = []
    pairs: list[tuple[str, str]] = []
    for op in ops:
        try:
            if op.op == "add_instance":
                if op.matrix is None:
                    raise InvalidObjectOp("a new instance needs its transform")
                check_matrix(op.matrix)
                mine = _items(model).get(op.object_id)
                if not mine:
                    raise InvalidObjectOp(f"the model has no object {op.object_id}")
                uuids = {u.decode("ascii", "replace") for u in _ANY_UUID.findall(model)}
                tag = _set(mine[-1].group(0), _TRANSFORM, b"transform", format_matrix(op.matrix))
                tag = _set(tag, _PRINTABLE, b"printable", "1")
                if _UUID.search(tag):
                    tag = _set(tag, _UUID, b"p:UUID", _fresh(uuids, (int(op.object_id) << 8) + len(mine), BUILD))
                # after the object's own items, as Orca saves them: the objects keep their places (the indexed files)
                model = _append_items(model, [tag], after=mine[-1])
                applied.append({"key": "instance", "object_id": op.object_id, "instance": len(mine)})
            elif op.op == "copy":
                if not op.matrices or op.new_id is None:
                    raise InvalidObjectOp("a copy needs its new id and its instances' transforms")
                for m in op.matrices:
                    check_matrix(m)
                model, settings, _ = copy_objects(
                    model, settings, [(op.object_id, [tuple(m) for m in op.matrices])], ids=[op.new_id]
                )
                pairs.append((op.object_id, op.new_id))
                record = {"key": "copy", "object_id": op.object_id, "new_id": op.new_id}
                applied.append({**record, "instances": len(op.matrices), "copies": [[op.object_id, op.new_id]]})
        except (InvalidPlacement, InvalidCopy) as exc:
            raise InvalidObjectOp(str(exc)) from exc
    changed: dict[str, bytes] = {}
    if pairs:
        place = {oid: n + 1 for n, oid in enumerate(_order(model))}
        for name in TEXT_INDEXED:
            data = (indexed or {}).get(name)
            if not data:
                continue
            lines = {int(m.group(1)): m for m in _INDEXED_LINE.finditer(data)}
            extra = []
            for source, new_id in pairs:
                m = lines.get(place[source])
                if m:
                    end = data.find(b"\n", m.end())
                    rest = data[m.end() : end if end >= 0 else len(data)]
                    extra.append(f"object_id={place[new_id]}|".encode() + rest.rstrip(b"\r"))
            if extra:
                newline = b"\r\n" if b"\r\n" in data else b"\n"
                body = data if data.endswith(b"\n") else data + newline
                changed[name] = body + newline.join(extra) + newline
    return model, settings, changed, applied


def check_name(name: object) -> str:
    """A name as Orca takes it: not empty, none of ``<>:/\\|?*"``, no control characters."""
    text = name if isinstance(name, str) else ""
    if not text.strip():
        raise InvalidObjectOp("a name can't be empty")
    if any(c in ILLEGAL for c in text):
        raise InvalidObjectOp(f"Invalid name, the following characters are not allowed: {ILLEGAL}")
    if any(ord(c) < 32 for c in text) or len(text) > MAX_NAME:
        raise InvalidObjectOp(f"a name has at most {MAX_NAME} characters and no control characters")
    return text


def _items(model: bytes) -> dict[str, list[re.Match]]:
    """Each object's build items, in instance order."""
    start, end = _build_span(model)
    out: dict[str, list[re.Match]] = {}
    for m in _ITEM.finditer(model, start, end):
        out.setdefault(_attr(m.group(0), _OBJECT_ID) or "", []).append(m)
    return out


def _set_attr(tag: bytes, rx: re.Pattern, name: str, value: str) -> bytes:
    """``tag`` with its attribute ``name`` set to ``value`` (added before the tag's end when it has none)."""
    found = rx.search(tag)
    attr = f' {name}="{value}"'.encode()
    if found:
        return tag[: found.start()] + attr + tag[found.end() :]
    close = len(tag) - (2 if tag.endswith(b"/>") else 1)
    return tag[:close].rstrip() + attr + tag[close:]


def _object_block(settings: bytes, object_id: str) -> tuple[int, int]:
    try:
        return _element(settings, _OBJECT, b"</object>", object_id, 0, len(settings))
    except InvalidPlacement as exc:
        raise InvalidObjectOp(f"object {object_id} has no settings block") from exc


def _parts(settings: bytes, start: int, end: int) -> list[tuple[str, int, int]]:
    out = []
    pos = start
    while (m := _PART.search(settings, pos, end)) is not None:
        p_end = m.end() if m.group(0).endswith(b"/>") else settings.find(b"</part>", m.end(), end) + len(b"</part>")
        out.append((_attr(m.group(0), _ID) or "", m.start(), p_end))
        pos = p_end
    return out


def _set_name(settings: bytes, start: int, end: int, name: str) -> bytes:
    """The element's own ``name`` metadata (before any part, for an object) set to ``name``."""
    open_end = settings.find(b">", start) + 1
    first_part = _PART.search(settings, open_end, end)
    stop = first_part.start() if first_part and settings[start:open_end].startswith(b"<object") else end
    mine = [m for m in _METADATA.finditer(settings, open_end, stop) if _attr(m.group(0), _KEY) == "name"]
    value = f' value="{object_settings._escape(name)}"'.encode()
    if mine:
        tag = mine[0].group(0)
        v = _VALUE.search(tag)
        new = tag[: v.start()] + value + tag[v.end() :] if v else tag[:-2] + value + b"/>"
        return settings[: mine[0].start()] + new + settings[mine[0].end() :]
    newline = b"\r\n" if b"\r\n" in settings else b"\n"
    indent = settings[settings.rfind(b"\n", 0, start) + 1 : start] + b"  "
    return settings[:open_end] + newline + indent + b'<metadata key="name"' + value + b"/>" + settings[open_end:]


def _flag(model: bytes, settings: bytes, op: ObjectOp, items: dict[str, list[re.Match]]) -> tuple[bytes, bytes, dict]:
    """A printable, auto drop or rename operation; what changed."""
    if op.op == "printable":
        mine = items.get(op.object_id, [])
        if op.instance is not None and op.instance >= len(mine):
            raise InvalidObjectOp(f"object {op.object_id} has no instance {op.instance}")
        if not isinstance(op.value, bool):
            raise InvalidObjectOp("printable is on or off")
        picked = [mine[op.instance]] if op.instance is not None else mine
        for m in sorted(picked, key=lambda m: -m.start()):
            tag = _set_attr(m.group(0), _PRINTABLE, "printable", "1" if op.value else "0")
            model = model[: m.start()] + tag + model[m.end() :]
        record = {"key": "printable", "object_id": op.object_id, "instance": op.instance, "after": op.value}
        return model, settings, record
    start, end = _object_block(settings, op.object_id)
    if op.op == "auto_drop":
        if not isinstance(op.value, bool):
            raise InvalidObjectOp("auto drop is on or off")
        open_end = settings.find(b">", start) + 1
        tag = _set_attr(settings[start:open_end], _AUTO_DROP, "auto_drop", "1" if op.value else "0")
        record = {"key": "auto_drop", "object_id": op.object_id, "after": op.value}
        return model, settings[:start] + tag + settings[open_end:], record
    name = check_name(op.value)
    parts = _parts(settings, start, end)
    if op.part_id is not None:
        found = [(a, b) for pid, a, b in parts if pid == op.part_id]
        if len(found) != 1:
            raise InvalidObjectOp(f"object {op.object_id} has no part {op.part_id}")
        settings = _set_name(settings, *found[0], name)
    else:
        if len(parts) == 1:  # update_name_in_model: an object of one part renames the part too
            settings = _set_name(settings, parts[0][1], parts[0][2], name)
            start, end = _object_block(settings, op.object_id)
        settings = _set_name(settings, start, end, name)
    return model, settings, {"key": "rename", "object_id": op.object_id, "part_id": op.part_id, "after": name}


def _renumber_xml(data: bytes, index: dict[int, int | None]) -> bytes:
    out, last = [], 0
    for m in _INDEXED_BLOCK.finditer(data):
        found = _ID.search(m.group(0))
        n = int(found.group(1) or found.group(2) or b"0") if found else 0
        if n not in index:
            continue
        out.append(data[last : m.start()])
        if index[n] is not None:
            block = m.group(0)
            out.append(block[: found.start()] + f' id="{index[n]}"'.encode() + block[found.end() :])
        last = m.end()
    out.append(data[last:])
    return b"".join(out)


def _renumber_text(data: bytes, index: dict[int, int | None]) -> bytes:
    out = []
    for line in data.splitlines(keepends=True):
        m = _INDEXED_LINE.match(line)
        if m and int(m.group(1)) in index:
            if index[int(m.group(1))] is None:
                continue
            line = f"object_id={index[int(m.group(1))]}|".encode() + line[m.end() :]
        out.append(line)
    return b"".join(out)


def _delete(
    model: bytes, settings: bytes, gone: dict[str, set[int]], items: dict[str, list[re.Match]]
) -> tuple[bytes, bytes, set[str]]:
    """The instances in ``gone`` deleted, an object with none left too; which objects went."""
    whole = {oid for oid, inst in gone.items() if len(inst) == len(items.get(oid, []))}
    # the model: the build items, and a deleted object's resource unless a component of the model's own names it
    drop = [m for oid, inst in gone.items() for k, m in enumerate(items[oid]) if k in inst]
    for m in sorted(drop, key=lambda m: -m.start()):
        a, b = object_settings._line(model, m.start(), m.end())
        model = model[:a] + model[b:]
    resources = model.rfind(b"</resources>")
    named = {
        _attr(c.group(0), _OBJECT_ID)
        for c in _COMPONENT.finditer(model, 0, resources)
        if (_attr(c.group(0), _PATH) or "/3D/3dmodel.model") == "/3D/3dmodel.model"
    }
    for oid in sorted(whole - named, key=int, reverse=True):
        try:
            a, b = _element(model, _OBJECT, b"</object>", oid, 0, model.rfind(b"</resources>"))
        except InvalidPlacement as exc:
            raise InvalidObjectOp(str(exc)) from exc
        a, b = object_settings._line(model, a, b)
        model = model[:a] + model[b:]
    # the settings: a deleted object's block, the plates' and the assembly's instances (the others renumbered)
    for oid in sorted(whole, key=int, reverse=True):
        try:
            a, b = _element(settings, _OBJECT, b"</object>", oid, 0, len(settings))
        except InvalidPlacement:
            continue  # an object without settings of its own
        a, b = object_settings._line(settings, a, b)
        settings = settings[:a] + settings[b:]

    def renumbered(oid: str, inst: int) -> int | None:
        if inst in gone.get(oid, ()):
            return None
        return inst - sum(1 for k in gone.get(oid, ()) if k < inst)

    out, last = [], 0
    for m in _INSTANCE_BLOCK.finditer(settings):
        meta = {_attr(x.group(0), _KEY): x for x in _METADATA.finditer(m.group(0))}
        oid = _attr(meta["object_id"].group(0), _VALUE) if "object_id" in meta else None
        inst_tag = meta.get("instance_id")
        inst = _attr(inst_tag.group(0), _VALUE) if inst_tag else None
        if oid not in gone or inst is None or not inst.isdigit():
            continue
        new = renumbered(oid, int(inst))
        out.append(settings[last : m.start()])
        if new is not None:
            tag = inst_tag.group(0)
            v = _VALUE.search(tag)
            out.append(m.group(0).replace(tag, tag[: v.start()] + f' value="{new}"'.encode() + tag[v.end() :], 1))
        last = m.end()
    out.append(settings[last:])
    settings = b"".join(out)
    for m in sorted(_ASSEMBLE_ITEM.finditer(settings), key=lambda m: -m.start()):
        tag = m.group(0)
        oid, inst = _attr(tag, _OBJECT_ID_ATTR), _attr(tag, _INSTANCE_ID_ATTR)
        if oid not in gone or inst is None or not inst.isdigit():
            continue
        new = renumbered(oid, int(inst))
        if new is None:
            a, b = object_settings._line(settings, m.start(), m.end())
            settings = settings[:a] + settings[b:]
        else:
            tag = _set_attr(tag, _INSTANCE_ID_ATTR, "instance_id", str(new))
            settings = settings[: m.start()] + tag + settings[m.end() :]
    return model, settings, whole


def _order(model: bytes) -> list[str]:
    try:
        return list(dict.fromkeys(i.get("objectid", "") for i in _build_items(model)))
    except InvalidPlacement as exc:
        raise InvalidObjectOp(str(exc)) from exc


def apply(
    model: bytes, settings: bytes, ops: list[ObjectOp], indexed: dict[str, bytes | None] | None = None
) -> tuple[bytes, bytes, dict[str, bytes], list[dict]]:
    """The model and ``model_settings.config`` with the operations made; the object-indexed files (``indexed``: name
    -> bytes, as the project has them) renumbered when objects went or changed places; what changed."""
    if len(ops) > MAX_OPS:
        raise InvalidObjectOp(f"too many object changes at once (at most {MAX_OPS})")
    try:
        items = _items(model)
    except InvalidPlacement as exc:
        raise InvalidObjectOp(str(exc)) from exc
    order_before = _order(model)
    applied: list[dict] = []
    for op in (o for o in ops if o.op != "delete" and o.op not in ADDITIONS):  # the additions: add(), made first
        if op.object_id not in items:
            raise InvalidObjectOp(f"the model has no object {op.object_id}")
        model, settings, record = _flag(model, settings, op, items)
        items = _items(model)
        applied.append(record)
    gone: dict[str, set[int]] = {}
    for op in (o for o in ops if o.op == "delete"):
        count = len(items.get(op.object_id, []))
        if not count or (op.instance is not None and op.instance >= count):
            raise InvalidObjectOp(f"object {op.object_id} has no instance {op.instance}")
        gone.setdefault(op.object_id, set()).update(range(count) if op.instance is None else {op.instance})
    changed: dict[str, bytes] = {}
    if gone:
        expected = {oid: len(m) - len(gone.get(oid, ())) for oid, m in items.items()}
        model, settings, whole = _delete(model, settings, gone, items)
        place = {oid: n + 1 for n, oid in enumerate(_order(model))}
        index = {n + 1: place.get(oid) for n, oid in enumerate(order_before)}
        if any(n != m for n, m in index.items()):
            for name, data in (indexed or {}).items():
                if data:
                    new = _renumber_xml(data, index) if name in XML_INDEXED else _renumber_text(data, index)
                    if new != data:
                        changed[name] = new
        if {k: v for k, v in expected.items() if v} != {oid: len(m) for oid, m in _items(model).items()}:
            raise InvalidObjectOp("the build items don't read back as deleted")
        for oid in sorted(gone, key=int):
            applied.append({"key": "delete", "object_id": oid, "instances": sorted(gone[oid]), "object": oid in whole})
    try:
        root = SafeET.fromstring(declared(settings))
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidObjectOp(f"model_settings.config doesn't read back: {exc}") from exc
    # no plate names an instance that isn't there any more
    counts = {oid: len(m) for oid, m in _items(model).items()}
    for inst in root.iter("model_instance"):
        meta = {m.get("key"): m.get("value") for m in inst.findall("metadata")}
        oid, k = meta.get("object_id"), meta.get("instance_id") or "0"
        if oid in gone and not (k.isdigit() and int(k) < counts.get(oid, 0)):
            raise InvalidObjectOp(f"a plate still names instance {k} of object {oid}")
    return model, settings, changed, applied
