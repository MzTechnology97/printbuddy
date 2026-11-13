"""The plates of an edited copy (fork): OrcaSlicer's Add plate, Remove the current plate, Move plate to the front and
Duplicate Current Plate, and the plate an instance moved onto.

A plate is a ``<plate>`` block of ``Metadata/model_settings.config``: its ``plater_id`` (1-based, its place), its own
settings and the instances on it (``model_instance``). The copy adds an empty block, removes one or moves one to the
front, then numbers the blocks again in their order; each block keeps every other byte, so its settings, instances
and thumbnails go with it. Removing a plate never removes its objects: their instances stay in the model, outside
every plate, as PartPlateList::delete_plate leaves them (the workspace moves them there; placement.py writes it).

The project's per-plate wipe tower positions (``wipe_tower_x`` / ``wipe_tower_y``) follow Orca's lists: a removed
plate's entry goes (``delete_plate``), a new plate gets one (``set_default_wipe_tower_pos_for_plate``'s resize to
the plate count with the first value), and moving a plate leaves them as they are, as ``move_plate_to_index`` does.

A duplicated plate is a new plate (``create_plate``) with a copy of each object on the plate it duplicates, moved by
the distance between the two (``duplicate_plate``; plate_objects.py writes the copies); like Orca, it has none of
that plate's own settings. An instance moved onto another plate is listed on that one (``assign``), as
``notify_instance_update`` puts it where it now lies; the workspace says which.
"""

from __future__ import annotations

import math
import re
from typing import Annotated, Literal

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.workspace.object_settings import _line, _value_of
from backend.app.farm.workspace.placement import (
    _KEY,
    _METADATA,
    _VALUE,
    MAX_TRANSFORMS,
    InstanceTransform,
    InvalidPlacement,
    _attr,
    _build_items,
    _numbers,
    check_matrix,
    place,
)
from backend.app.farm.workspace.plate_objects import MAX_COPIES, InvalidCopy, copy_objects
from backend.app.utils.xml_prefixes import declared

MAX_PLATES = 36  # PartPlate.hpp MAX_PLATE_COUNT
LOGICAL_GAP = 1 / 5  # PartPlate.cpp LOGICAL_PART_PLATE_GAP: the space between plates, a fifth of the bed
MAX_OPS = 200
WIPE_TOWER = ("wipe_tower_x", "wipe_tower_y")
_PLATE = re.compile(rb"<plate\b[^>]*>")


class InvalidPlateStructure(ValueError):
    """A change the copy refuses (the message says which and why)."""


class PlateOp(BaseModel):
    """``add``: an empty plate after the last; ``remove``: plate ``plate`` (not the last one left); ``front``: plate
    ``plate`` moved to the front; ``duplicate``: a new plate after the last with a copy of plate ``plate``'s objects
    (``objects``: those it held then, as the workspace saw them; none given: those the file lists on it), each instance
    copied from where it was then (``instances``: those placed by then; the others where the file puts them). Plates
    are numbered as they are when the op comes, after the ops before it."""

    op: Literal["add", "remove", "front", "duplicate"]
    plate: int | None = Field(default=None, ge=1, le=MAX_PLATES)
    objects: list[Annotated[str, Field(pattern=r"^[0-9]{1,9}$")]] | None = Field(default=None, max_length=MAX_COPIES)
    instances: list[InstanceTransform] = Field(default_factory=list, max_length=MAX_TRANSFORMS)


class InstancePlate(BaseModel):
    """The plate an instance lies on after it was moved, numbered as the file's plates are (before any plate is added,
    removed or moved); 0: no plate. ``new_plate``: a plate the plate changes add (Add plate, Duplicate), numbered as
    the plates are after them; the instance is where it was put, there, and doesn't move with any plate."""

    object_id: str = Field(pattern=r"^[0-9]{1,9}$")
    instance: int = Field(ge=0, le=100_000)
    plate: int = Field(ge=0, le=MAX_PLATES)
    new_plate: bool = False


def _blocks(settings: bytes) -> tuple[int, int, list[bytes]]:
    """Where the plates' run starts and ends, and each plate's block (its whole lines)."""
    spans = []
    for m in _PLATE.finditer(settings):
        end = settings.find(b"</plate>", m.end())
        if end < 0:
            raise InvalidPlateStructure("a plate of model_settings.config is not closed")
        a, _ = _line(settings, m.start(), m.end())
        _, b = _line(settings, end, end + len(b"</plate>"))
        spans.append((a, b))
    if not spans:
        raise InvalidPlateStructure("model_settings.config has no plates")
    if any(spans[i][1] != spans[i + 1][0] for i in range(len(spans) - 1)):
        raise InvalidPlateStructure("the plates of model_settings.config are not written one after the other")
    return spans[0][0], spans[-1][1], [settings[a:b] for a, b in spans]


def _plater_id(block: bytes) -> re.Match:
    m = next((m for m in _METADATA.finditer(block) if _attr(m.group(0), _KEY) == "plater_id"), None)
    if m is None:
        raise InvalidPlateStructure("a plate of model_settings.config has no plater_id")
    return m


def _numbered(block: bytes, n: int) -> bytes:
    """The block with its ``plater_id`` set to ``n``."""
    m = _plater_id(block)
    tag = m.group(0)
    v = _VALUE.search(tag)
    return block[: m.start()] + tag[: v.start()] + f' value="{n}"'.encode() + tag[v.end() :] + block[m.end() :]


def _empty(model: bytes) -> bytes:
    """A new plate's block, written as ``model`` (another plate) is: its indentation and line breaks."""
    newline = b"\r\n" if b"\r\n" in model else b"\n"
    outer = model[: len(model) - len(model.lstrip(b" \t"))]
    meta = re.search(rb"\n([ \t]*)<metadata", model)
    inner = meta.group(1) if meta else outer + b"  "
    lines = [
        outer + b"<plate>",
        inner + b'<metadata key="plater_id" value="0"/>',
        inner + b'<metadata key="plater_name" value=""/>',
        inner + b'<metadata key="locked" value="false"/>',
        outer + b"</plate>",
    ]
    return newline.join(lines) + (newline if model.endswith(b"\n") else b"")


def _dump(block: bytes) -> list:
    """A plate's elements, its ``plater_id`` value left out (to compare a plate with where it came from)."""
    try:
        el = SafeET.fromstring(declared(b"<config>" + block + b"</config>"))[0]
    except (SafeET.ParseError, DefusedXmlException, IndexError) as exc:
        raise InvalidPlateStructure(f"a plate of model_settings.config is not valid XML: {exc}") from exc
    return [
        (
            x.tag,
            tuple(sorted((k, "" if x.get("key") == "plater_id" and k == "value" else v) for k, v in x.attrib.items())),
        )
        for x in el.iter()
    ]


def _columns(count: int) -> int:
    """PartPlate.hpp compute_colum_count."""
    root = math.sqrt(count)
    return round(root) + 1 if root > round(root) else round(root)


def _bed(cfg: dict) -> tuple[float, float]:
    """The plate's width and depth (PartPlateList's m_plate_width / depth: the printable area's bounding box)."""
    try:
        points = [tuple(float(v) for v in p.split("x")) for p in cfg.get("printable_area") or []]
    except ValueError as exc:
        raise InvalidPlateStructure("the printer's printable area can't be read") from exc
    if len(points) < 3 or any(len(p) != 2 for p in points):
        raise InvalidPlateStructure("the project has no printable area to lay its plates out by")
    return max(p[0] for p in points) - min(p[0] for p in points), max(p[1] for p in points) - min(p[1] for p in points)


Token = int | str  # a plate's instances: the file's plate (from 0), or a duplicate's copies ("d0", "d1"…)


def moves(
    count: int, ops: list[PlateOp], bed: tuple[float, float]
) -> tuple[dict[Token, tuple[float, float]], tuple[float, float], dict[str, int]]:
    """How far each of the file's plates (from 0) moves with its instances, and the instances on no plate; each
    duplicate's copies (``d0``, ``d1``… in order) and the file's plate whose instances they copy.

    PartPlateList lays ``n`` plates in ``_columns(n)`` columns, each ``plate_stride`` (the bed plus a fifth) from the
    last, rows going to the front (``compute_origin``); the objects on no plate sit after them
    (``compute_origin_for_unprintable``). A plate's instances go with it (``set_pos_and_size``); a removed plate's go
    to the place of the objects on no plate (``delete_plate``), and those move with that place when a plate goes, not
    when one comes (``create_plate``: ``with_unprintable_move`` false). A duplicate is a new plate, then a copy of
    the plate's instances where they are, moved by the distance between the two plates (``duplicate_plate``)."""
    w, d = bed[0] * (1 + LOGICAL_GAP), bed[1] * (1 + LOGICAL_GAP)

    def origin(i: int, cols: int) -> tuple[float, float]:
        return (i % cols) * w, -(i // cols) * d

    def outside(n: int) -> tuple[float, float]:
        c = _columns(n)
        return origin(c * c + c - 1, c + 1) if n == c * c else origin(n, c)

    order: list[Token | None] = list(range(count))
    delta: dict[Token, tuple[float, float]] = dict.fromkeys(range(count), (0.0, 0.0))
    copied: dict[str, int] = {}
    off_plate: set[Token] = set()  # plates removed: their instances are on no plate now
    out_delta = (0.0, 0.0)
    add = lambda a, b, c: (a[0] + c[0] - b[0], a[1] + c[1] - b[1])  # noqa: E731  a moved from b to c
    for op in ops:
        n = len(order)
        before = {p: origin(i, _columns(n)) for i, p in enumerate(order) if p is not None}
        removed = new = None
        source = order[op.plate - 1] if op.op == "duplicate" else None
        if op.op in ("add", "duplicate"):
            if source is not None:  # duplicating a new plate, with nothing on it, adds one
                new = f"d{len(copied)}"
                copied[new] = source if isinstance(source, int) else copied[source]
            order.append(new)
        elif op.op == "remove":
            removed = order.pop(op.plate - 1)
        else:
            order.insert(0, order.pop(op.plate - 1))
        m = len(order)
        for i, p in enumerate(order):
            if p is not None and p != new:
                delta[p] = add(delta[p], before[p], origin(i, _columns(m)))
        if new is not None:
            delta[new] = add(delta[source], origin(order.index(source), _columns(m)), origin(m - 1, _columns(m)))
        if op.op == "remove":
            shift = add((0.0, 0.0), outside(n), outside(m))
            out_delta = add(out_delta, (0.0, 0.0), shift)
            for p in off_plate:
                delta[p] = add(delta[p], (0.0, 0.0), shift)
            if removed is not None:
                delta[removed] = add(delta[removed], before[removed], outside(m))
                off_plate.add(removed)
    return delta, out_delta, copied


def _membership(settings: bytes) -> dict[tuple[str, int], int]:
    """Each instance on a plate (object id, instance number) -> its plate (from 0), as the plates list them."""
    try:
        root = SafeET.fromstring(declared(settings))
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidPlateStructure(f"model_settings.config is not valid XML: {exc}") from exc
    out: dict[tuple[str, int], int] = {}
    for i, plate in enumerate(el for el in root if el.tag == "plate"):
        for inst in plate.iter("model_instance"):
            meta = {m.get("key"): m.get("value") for m in inst if m.tag == "metadata"}
            if meta.get("object_id"):
                out[(meta["object_id"], int(meta.get("instance_id") or 0))] = i
    return out


def _instances(model: bytes) -> dict[tuple[str, int], tuple[float, ...]]:
    """Each build item (object id, its instance number) -> its transform's 12 numbers, in the build's order."""
    out: dict[tuple[str, int], tuple[float, ...]] = {}
    count: dict[str, int] = {}
    try:
        for item in _build_items(model):
            oid = item.get("objectid", "")
            out[(oid, count.get(oid, 0))] = _numbers(item.get("transform"))
            count[oid] = count.get(oid, 0) + 1
    except InvalidPlacement as exc:
        raise InvalidPlateStructure(f"the model's instances can't be read: {exc}") from exc
    return out


def _shifted(spec: tuple[float, ...], d: tuple[float, float]) -> list[float]:
    m = list(spec)
    m[9], m[10] = m[9] + d[0], m[10] + d[1]
    return m


def _move_instances(
    model: bytes,
    where: dict[tuple[str, int], int],
    delta: dict,
    out_delta: tuple[float, float],
    keep: set[tuple[str, int]] = frozenset(),
) -> bytes:
    """``model`` with every instance (but those in ``keep``) moved as its plate moves (``moves``), each build item's
    other bytes kept."""
    wanted = []
    for (object_id, instance), spec in _instances(model).items():
        if (object_id, instance) in keep:
            continue
        p = where.get((object_id, instance))
        d = out_delta if p is None else delta[p]
        if abs(d[0]) > 1e-9 or abs(d[1]) > 1e-9:
            wanted.append(InstanceTransform(object_id=object_id, instance=instance, matrix=_shifted(spec, d)))
    if not wanted:
        return model
    try:
        return place(model, wanted)[0]
    except InvalidPlacement as exc:
        raise InvalidPlateStructure(f"the plates' objects can't be moved with them: {exc}") from exc


# ---- the instances a plate lists ------------------------------------------------------------------------------

_INSTANCE = re.compile(rb"<model_instance\b[^>]*>")


def _entries(block: bytes) -> list[tuple[tuple[str, int], int, int]]:
    """Each ``<model_instance>`` of a plate's block: (object id, instance number), and its whole lines."""
    out = []
    for m in _INSTANCE.finditer(block):
        close = block.find(b"</model_instance>", m.end())
        if close < 0:
            raise InvalidPlateStructure("an instance of a plate is not closed")
        a, b = _line(block, m.start(), close + len(b"</model_instance>"))
        meta = {_attr(t.group(0), _KEY): _value_of(t.group(0)) for t in _METADATA.finditer(block, m.end(), close)}
        out.append(((meta.get("object_id") or "", int(meta.get("instance_id") or 0)), a, b))
    return out


def _identify_id(settings: bytes) -> int:
    """The highest ``identify_id`` the plates give an instance (Bambu Studio's, to skip objects by); 0: none."""
    found = [
        int(v)
        for m in _METADATA.finditer(settings)
        if _attr(m.group(0), _KEY) == "identify_id" and (v := _value_of(m.group(0)) or "").isdigit()
    ]
    return max(found, default=0)


def _entry(template: bytes | None, plate: bytes, key: tuple[str, int], identify: int | None) -> bytes:
    """A ``<model_instance>`` for ``key``, written as ``template`` (another one) is, else inside ``plate``."""
    sample = template if template is not None else plate
    newline = b"\r\n" if b"\r\n" in sample else b"\n"
    meta = re.search(rb"\n([ \t]*)<metadata", sample)
    if template is not None:
        outer = template[: len(template) - len(template.lstrip(b" \t"))]
        inner = meta.group(1) if meta else outer + b"  "
        sep = newline if template.endswith(b"\n") else b""
    else:
        outer = meta.group(1) if meta else b""
        inner = outer + b"  " if meta else b""
        sep = newline if meta else b""
    lines = [
        outer + b"<model_instance>",
        inner + f'<metadata key="object_id" value="{key[0]}"/>'.encode(),
        inner + f'<metadata key="instance_id" value="{key[1]}"/>'.encode(),
    ]
    if identify is not None:
        lines.append(inner + f'<metadata key="identify_id" value="{identify}"/>'.encode())
    lines.append(outer + b"</model_instance>")
    return sep.join(lines) + sep


def _with_entries(block: bytes, entries: list[bytes]) -> bytes:
    """``block`` with ``entries`` after its last instance (before ``</plate>`` when it has none)."""
    if not entries:
        return block
    found = _entries(block)
    if found:
        at = found[-1][2]
    else:
        close = block.rfind(b"</plate>")
        at, _ = _line(block, close, close + len(b"</plate>"))
    return block[:at] + b"".join(entries) + block[at:]


def _own(block: bytes) -> list:
    """A plate's own elements (its metadata, not its instances), its ``plater_id`` value left out."""
    try:
        el = SafeET.fromstring(declared(b"<config>" + block + b"</config>"))[0]
    except (SafeET.ParseError, DefusedXmlException, IndexError) as exc:
        raise InvalidPlateStructure(f"a plate of model_settings.config is not valid XML: {exc}") from exc
    return [
        (
            x.tag,
            tuple(sorted((k, "" if x.get("key") == "plater_id" and k == "value" else v) for k, v in x.attrib.items())),
        )
        for x in el
        if x.tag != "model_instance"
    ]


def assign(settings: bytes, model: bytes, moved: list[InstancePlate]) -> tuple[bytes, list[dict]]:
    """``model_settings.config`` with each instance moved listed on the plate it now lies on (0: on none), every other
    byte kept; what changed: ``{key: "plate_object", object_id, instance, before, after}`` (plates from 1, 0 none)."""
    start, end, blocks = _blocks(settings)
    known = _instances(model)
    where = _membership(settings)
    changes: dict[tuple[str, int], int] = {}
    seen: set[tuple[str, int]] = set()
    for m in moved:
        key = (m.object_id, m.instance)
        if key not in known:
            raise InvalidPlateStructure(f"object {m.object_id} has no instance {m.instance}")
        if key in seen:
            raise InvalidPlateStructure(f"object {m.object_id} instance {m.instance} is moved twice")
        if m.plate > len(blocks):
            raise InvalidPlateStructure(f"the project has no plate {m.plate}")
        seen.add(key)
        if where.get(key, -1) != m.plate - 1:
            changes[key] = m.plate - 1
    if not changes:
        return settings, []
    template = next((blk[a:b] for blk in blocks for _, a, b in _entries(blk)), None)
    identify = _identify_id(settings)
    taken: dict[tuple[str, int], bytes] = {}
    kept = []
    for blk in blocks:
        out, last = [], 0
        for key, a, b in _entries(blk):
            if key in changes:
                taken[key] = blk[a:b]  # its own bytes go with it (Bambu Studio's identify_id)
                out.append(blk[last:a])
                last = b
        kept.append(b"".join(out) + blk[last:])
    added: list[list[bytes]] = [[] for _ in blocks]
    for key, p in changes.items():
        if p < 0:
            continue
        if key not in taken and identify:
            identify += 1  # an instance from no plate: a new identify_id, when the file gives them
        added[p].append(taken.get(key) or _entry(template, kept[p], key, identify or None))
    out = settings[:start] + b"".join(_with_entries(b, e) for b, e in zip(kept, added, strict=True)) + settings[end:]
    # read back: the moved instances on their plates, the others where they were, the plates' own the same
    expected = {k: p for k, p in where.items() if k not in changes} | {k: p for k, p in changes.items() if p >= 0}
    _, new_end, check = _blocks(out)
    if _membership(out) != expected or out[:start] != settings[:start] or out[new_end:] != settings[end:]:
        raise InvalidPlateStructure("the plates don't list the moved instances where they lie")
    if any(_own(a) != _own(b) for a, b in zip(blocks, check, strict=True)):
        raise InvalidPlateStructure("a plate changed beyond its instances")
    return out, [
        {"key": "plate_object", "object_id": k[0], "instance": k[1], "before": where.get(k, -1) + 1, "after": p + 1}
        for k, p in changes.items()
    ]


# ---- the plates added, removed, moved and duplicated ---------------------------------------------------------


def _copies(
    model: bytes,
    where: dict[tuple[str, int], int],
    delta: dict,
    copied: dict[str, int],
    chosen: dict[str, list[str] | None],
    then: dict[str, dict[tuple[str, int], tuple[float, ...]]],
) -> list[tuple[str, str, list[tuple[float, ...]]]]:
    """Each duplicate's copies: (duplicate, object id, its instances' transforms), the objects in the model's order:
    those ``chosen`` for it, else those on its plate; each instance from where it was ``then`` (else as ``model``).

    ``duplicate_plate`` copies each object on the plate with all its instances, every one moved by the same distance
    (an instance on another plate too)."""
    instances = _instances(model)
    objects = {oid for oid, _ in instances}
    out = []
    for dup, plate in copied.items():
        wanted = chosen.get(dup)
        if wanted is not None and not set(wanted) <= objects:
            raise InvalidPlateStructure(f"object {sorted(set(wanted) - objects)[0]} is not in the model to copy")
        on_plate = (
            oid
            for (oid, inst) in instances
            if (oid in wanted if wanted is not None else where.get((oid, inst)) == plate)
        )
        for oid in dict.fromkeys(on_plate):
            was = then.get(dup, {})
            specs = [tuple(_shifted(was.get(k, s), delta[dup])) for k, s in instances.items() if k[0] == oid]
            out.append((dup, oid, specs))
    return out


def apply(
    settings: bytes,
    cfg: dict,
    ops: list[PlateOp],
    model: bytes | None = None,
    original: bytes | None = None,
    onto: list[InstancePlate] = (),
) -> tuple[bytes, dict, bytes | None, list[dict]]:
    """``model_settings.config``, the project settings and the model (its instances moved with their plates and the
    duplicates' copies added, when given) with the plates added, removed, moved and duplicated, and what changed:
    ``{key: "plate", op, plate, plates}`` (``plates``: how many there are after it; a duplicate's ``objects``: how
    many it copied). ``original``: the model before its placements, where a duplicate's instances not placed by then
    were. ``onto``: instances (on no plate before) put on a plate the changes add, listed there as they are."""
    if len(ops) > MAX_OPS:
        raise InvalidPlateStructure(f"too many plate changes at once (at most {MAX_OPS})")
    _, _, blocks = _blocks(settings)
    count0 = len(blocks)
    sources: list[Token | None] = list(range(count0))  # each plate: the original's block, a duplicate, None (new)
    cfg = dict(cfg)
    towers = {k: list(cfg[k]) for k in WIPE_TOWER if isinstance(cfg.get(k), list)}
    applied: list[dict] = []
    duplicates: list[str | None] = []  # each duplicate's copies (None: it duplicated an empty plate)
    chosen: dict[str, list[str] | None] = {}
    then: dict[str, dict[tuple[str, int], tuple[float, ...]]] = {}
    for op in ops:
        count = len(sources)
        record = {"key": "plate", "op": op.op, "plate": op.plate}
        if op.op in ("add", "duplicate"):
            if count >= MAX_PLATES:
                raise InvalidPlateStructure(f"a project has at most {MAX_PLATES} plates")
            new = None
            if op.op == "duplicate":
                if op.plate is None or not 1 <= op.plate <= count:
                    raise InvalidPlateStructure(f"the project has no plate {op.plate}")
                if sources[op.plate - 1] is not None:  # named as ``moves`` names them
                    new = f"d{sum(1 for d in duplicates if d is not None)}"
                    chosen[new] = op.objects
                    for t in op.instances:
                        check_matrix(t.matrix)
                    then[new] = {(t.object_id, t.instance): tuple(t.matrix) for t in op.instances}
                elif op.objects:
                    raise InvalidPlateStructure(f"plate {op.plate} is a new plate: it has no objects to copy")
                duplicates.append(new)
            sources.append(new)
            for k, values in towers.items():
                if values:
                    towers[k] = (values + [values[0]] * (count + 1))[: count + 1]
        else:
            if op.plate is None or not 1 <= op.plate <= count:
                raise InvalidPlateStructure(f"the project has no plate {op.plate}")
            i = op.plate - 1
            if op.op == "remove":
                if count == 1:
                    raise InvalidPlateStructure("the last plate can't be removed")
                del sources[i]
                for values in towers.values():
                    if i < len(values):
                        del values[i]
            elif i > 0:
                sources.insert(0, sources.pop(i))
            else:
                raise InvalidPlateStructure("plate 1 is already at the front")
        applied.append({**record, "plates": len(sources)})
    for k, values in towers.items():
        cfg[k] = values

    entries: dict[str, list[bytes]] = {}
    copies: list[tuple[str, str, list[tuple[float, ...]]]] = []
    where = _membership(settings)
    put: dict[int, list[tuple[str, int]]] = {}  # instances put on a new plate, by its number after the changes
    for m in onto:
        key = (m.object_id, m.instance)
        if not m.new_plate or not 1 <= m.plate <= len(sources) or isinstance(sources[m.plate - 1], int):
            raise InvalidPlateStructure(f"plate {m.plate} is not a plate these changes add")
        if key in where or any(key in v for v in put.values()):
            raise InvalidPlateStructure(f"object {m.object_id} instance {m.instance} is on another plate")
        if model is not None and key not in _instances(model):
            raise InvalidPlateStructure(f"object {m.object_id} has no instance {m.instance}")
        put.setdefault(m.plate, []).append(key)
    template = next((blk[a:b] for blk in blocks for _, a, b in _entries(blk)), None)
    identify = _identify_id(settings)
    if model is not None:
        delta, out_delta, copied = moves(count0, ops, _bed(cfg))
        if original is not None:  # the instances not placed when the plate was duplicated: where the file has them
            first = _instances(original)
            then = {d: first | placed for d, placed in then.items()}
        copies = _copies(model, where, delta, copied, chosen, then)  # from before their plates move
        model = _move_instances(model, where, delta, out_delta, {k for keys in put.values() for k in keys})
        if copies:
            try:
                model, settings, new_ids = copy_objects(model, settings, [(oid, specs) for _, oid, specs in copies])
            except (InvalidCopy, InvalidPlacement) as exc:
                raise InvalidPlateStructure(f"the plate's objects can't be copied: {exc}") from exc
            for (dup, _, specs), new_id in zip(copies, new_ids, strict=True):
                for k in range(len(specs)):
                    identify = identify + 1 if identify else 0
                    entries.setdefault(dup, []).append(_entry(template, blocks[-1], (new_id, k), identify or None))
    elif any(isinstance(s, str) for s in sources):
        raise InvalidPlateStructure("a plate's objects can't be copied without the model")
    pairs = list(zip(copies, new_ids, strict=True)) if copies else []
    for dup, done in zip(duplicates, (a for a in applied if a["op"] == "duplicate"), strict=True):
        done["objects"] = sum(1 for d, _, _ in copies if d == dup)
        # which object each copy is of (its height ranges go with it, as Model::add_object copies them)
        done["copies"] = [[oid, new_id] for (d, oid, _), new_id in pairs if d == dup]

    start, end, blocks = _blocks(settings)  # the copies' objects are before the plates: the same blocks
    empty = _empty(blocks[-1])
    new_blocks = []
    added: dict[int, int] = {}  # each new plate: how many instances it lists
    for n, s in enumerate(sources, 1):
        if isinstance(s, int):
            new_blocks.append(_numbered(blocks[s], n))
            continue
        listed = entries.get(s, []) if s else []
        for key in put.get(n, []):
            identify = identify + 1 if identify else 0
            listed = [*listed, _entry(template, blocks[-1], key, identify or None)]
        added[n] = len(listed)
        new_blocks.append(_numbered(_with_entries(empty, listed), n))
    out = settings[:start] + b"".join(new_blocks) + settings[end:]
    # read back: the same document around the plates, each plate the block it came from but for its number, a
    # duplicate listing its copies
    _, new_end, check = _blocks(out)
    if out[:start] != settings[:start] or out[new_end:] != settings[end:] or len(check) != len(sources):
        raise InvalidPlateStructure("model_settings.config changed beyond its plates")
    for n, (block, s) in enumerate(zip(check, sources, strict=True), 1):
        if _value_of(_plater_id(block).group(0)) != str(n) or (isinstance(s, int) and _dump(block) != _dump(blocks[s])):
            raise InvalidPlateStructure("a plate changed beyond its number")
        if not isinstance(s, int) and len(_entries(block)) != added[n]:
            raise InvalidPlateStructure("a new plate doesn't list its instances")
    listed = _membership(out)
    if any(listed.get(k) != n - 1 for n, keys in put.items() for k in keys):
        raise InvalidPlateStructure("an instance isn't listed on the new plate it was put on")
    return out, cfg, model, applied
