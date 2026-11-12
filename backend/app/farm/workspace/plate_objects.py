"""Objects copied onto a duplicated plate (fork): OrcaSlicer's Duplicate Current Plate (PartPlateList::duplicate_plate).

Orca copies each object on the plate whole (``Model::add_object``: its parts, its own settings, all its instances),
puts every instance of the copy on the new plate and moves them by the distance between the two plates. The copy
writes each one as Orca's own share-mesh save does (bbs_3mf ``SaveStrategy::ShareMesh``): a new ``<object>`` of
``3D/3dmodel.model`` whose components point at the original's meshes (the same ``p:path`` and ``objectid``, a
``-71cb-`` object UUID), a build item per instance, and in ``model_settings.config`` the original's ``<object>``
block under the new id (the parts keep their ids: they name the shared meshes). An object whose mesh is in its own
``<object>`` is copied with its mesh. The meshes' files, the assembly view and the cut information are left as they
are: a copy has no assembly place of its own (the slicer takes its instance's) and is cut from nothing.
"""

from __future__ import annotations

import re

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from backend.app.farm.workspace.placement import (
    _ID,
    _ITEM,
    _OBJECT,
    _OBJECT_ID,
    _TRANSFORM,
    InvalidPlacement,
    _attr,
    _build_items,
    _build_span,
    _numbers,
    format_matrix,
)
from backend.app.utils.xml_prefixes import declared

MAX_COPIES = 2000  # objects copied in one save: 36 plates of a full bed
_UUID = re.compile(rb"""\sp:UUID\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_ANY_UUID = re.compile(rb"""p:UUID\s*=\s*["']([^"']*)["']""")
_COMPONENT = re.compile(rb"<component\b[^>]*>")
# bbs_3mf's UUID suffixes: an object sharing meshes, a component, a build item
OBJECT_SHARED = "-71cb-4c03-9d28-80fed5dfa1dc"
COMPONENT = "-b206-40ff-9872-83e8017abed1"
BUILD = "-b1ec-4553-aec9-835e5b724bb4"


class InvalidCopy(ValueError):
    """A plate's objects that can't be copied (the message says why)."""


def _line_span(data: bytes, start: int, end: int) -> tuple[int, int]:
    """The element's whole lines when it starts a line (indentation and line break), else just itself."""
    a = data.rfind(b"\n", 0, start) + 1
    if data[a:start].strip():
        return start, end
    if data[end : end + 2] == b"\r\n":
        return a, end + 2
    return a, end + 1 if data[end : end + 1] == b"\n" else end


def _objects(data: bytes, start: int, end: int) -> dict[str, tuple[int, int]]:
    """Each ``<object id=…>…</object>`` between ``start`` and ``end``: its id -> its whole lines."""
    out: dict[str, tuple[int, int]] = {}
    pos = start
    while (m := _OBJECT.search(data, pos, end)) is not None:
        oid = _attr(m.group(0), _ID)
        close = data.find(b"</object>", m.end(), end)
        if oid is None or close < 0 or m.group(0).endswith(b"/>"):
            raise InvalidCopy("the objects can't be read one by one")
        out[oid] = _line_span(data, m.start(), close + len(b"</object>"))
        pos = close + len(b"</object>")
    return out


def _set(tag: bytes, rx: re.Pattern, name: bytes, value: str) -> bytes:
    """``tag`` with attribute ``name`` (matched by ``rx``) set to ``value``, added after the tag's name when absent."""
    new = b" " + name + b'="' + value.encode("ascii") + b'"'
    m = rx.search(tag)
    if m:
        return tag[: m.start()] + new + tag[m.end() :]
    at = re.match(rb"<[\w:]+", tag).end()
    return tag[:at] + new + tag[at:]


def _fresh(used: set[str], n: int, suffix: str) -> str:
    """bbs_3mf's ``%08x`` UUID from ``n``, the next one free."""
    while f"{n:08x}{suffix}" in used:
        n += 1
    used.add(f"{n:08x}{suffix}")
    return f"{n:08x}{suffix}"


def copy_objects(
    model: bytes, settings: bytes, copies: list[tuple[str, list[tuple[float, ...]]]]
) -> tuple[bytes, bytes, list[str]]:
    """``model`` and ``model_settings.config`` with a copy of each ``(object id, its instances' transforms)``; the
    copies' new ids, in order. Every other byte of both stays, and both are read back."""
    if len(copies) > MAX_COPIES:
        raise InvalidCopy(f"too many objects to copy at once (at most {MAX_COPIES})")
    if not copies:
        return model, settings, []
    resources_end = model.rfind(b"</resources>")
    if resources_end < 0:
        raise InvalidCopy("the model has no resources")
    try:
        items_before = _build_items(model)
        build = _build_span(model)
    except InvalidPlacement as exc:
        raise InvalidCopy(str(exc)) from exc
    objects = _objects(model, 0, resources_end)
    settings_objects = _objects(settings, 0, len(settings))
    ids = set(objects) | set(settings_objects) | {c.decode() for c in re.findall(rb'objectid\s*=\s*["\'](\d+)', model)}
    next_id = max((int(i) for i in ids if i.isdigit()), default=0) + 1
    uuids = {u.decode("ascii", "replace") for u in _ANY_UUID.findall(model)}
    item_tags = [m.group(0) for m in _ITEM.finditer(model, *build)]

    new_objects, new_settings, new_items, new_ids = [], [], [], []
    for object_id, transforms in copies:
        if object_id not in objects:
            raise InvalidCopy(f"object {object_id} is not in the model")
        template = next((t for t in item_tags if _attr(t, _OBJECT_ID) == object_id), None)
        if template is None:
            raise InvalidCopy(f"object {object_id} has no build item to copy")
        new_id = str(next_id)
        next_id += 1
        a, b = objects[object_id]
        block = model[a:b]
        head = _OBJECT.search(block)
        tag = _set(head.group(0), _ID, b"id", new_id)
        if _UUID.search(tag):
            tag = _set(tag, _UUID, b"p:UUID", _fresh(uuids, int(new_id), OBJECT_SHARED))
        block = block[: head.start()] + tag + block[head.end() :]
        # its components' own UUIDs are new; their paths and mesh ids stay: the copy shares the original's meshes
        out, pos = [], 0
        for k, m in enumerate(_COMPONENT.finditer(block)):
            ctag = m.group(0)
            if _UUID.search(ctag):
                ctag = _set(ctag, _UUID, b"p:UUID", _fresh(uuids, (int(new_id) << 16) + k, COMPONENT))
            out += [block[pos : m.start()], ctag]
            pos = m.end()
        new_objects.append(b"".join(out) + block[pos:])
        if object_id in settings_objects:
            a, b = settings_objects[object_id]
            sblock = settings[a:b]
            shead = _OBJECT.search(sblock)
            new_settings.append(
                sblock[: shead.start()] + _set(shead.group(0), _ID, b"id", new_id) + sblock[shead.end() :]
            )
        for k, spec in enumerate(transforms):
            itag = _set(template, _OBJECT_ID, b"objectid", new_id)
            itag = _set(itag, _TRANSFORM, b"transform", format_matrix(list(spec)))
            if _UUID.search(itag):
                itag = _set(itag, _UUID, b"p:UUID", _fresh(uuids, (int(new_id) << 8) + k, BUILD))
            new_items.append(itag)
        new_ids.append(new_id)

    at = max(b for _, b in objects.values())
    model = model[:at] + b"".join(new_objects) + model[at:]
    model = _append_items(model, new_items)
    if new_settings:
        at = max(b for _, b in settings_objects.values())
        settings = settings[:at] + b"".join(new_settings) + settings[at:]
    _check(model, settings, items_before, copies, new_ids)
    return model, settings, new_ids


def _append_items(model: bytes, items: list[bytes]) -> bytes:
    """``items`` after the build's last item, each on its own line as that one is."""
    last = next(reversed(list(_ITEM.finditer(model, *_build_span(model)))), None)
    if last is None:
        raise InvalidCopy("the model has no build items")
    a, b = _line_span(model, last.start(), last.end())
    if a == last.start():  # the items share one line
        return model[: last.end()] + b"".join(items) + model[last.end() :]
    indent, newline = model[a : last.start()], model[last.end() : b]
    return model[:b] + b"".join(indent + i + newline for i in items) + model[b:]


def _check(model: bytes, settings: bytes, items_before: list[dict], copies, new_ids: list[str]) -> None:
    """The build holds the items it had and the copies' after them; each copy's object and settings are there."""
    try:
        items = _build_items(model)
    except InvalidPlacement as exc:
        raise InvalidCopy(f"the copied objects don't read back: {exc}") from exc
    added = items[len(items_before) :]
    if items[: len(items_before)] != items_before or len(added) != sum(len(t) for _, t in copies):
        raise InvalidCopy("the model's build items changed beyond the copies")
    expected = [(new_id, spec) for new_id, (_, ts) in zip(new_ids, copies, strict=True) for spec in ts]
    for item, (new_id, spec) in zip(added, expected, strict=True):
        if item.get("objectid") != new_id or _numbers(item.get("transform")) != _numbers(format_matrix(list(spec))):
            raise InvalidCopy(f"object {new_id}'s instances don't read back")
    objects = _objects(model, 0, model.rfind(b"</resources>"))
    try:
        root = SafeET.fromstring(declared(settings))
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidCopy(f"model_settings.config is not valid XML after the copies: {exc}") from exc
    in_settings = {el.get("id") for el in root if el.tag == "object"}
    for new_id, (object_id, _) in zip(new_ids, copies, strict=True):
        if new_id not in objects or (object_id in in_settings and new_id not in in_settings):
            raise InvalidCopy(f"object {new_id} doesn't read back")
