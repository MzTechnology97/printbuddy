"""The plates of an edited copy (fork): OrcaSlicer's Add plate, Remove the current plate and Move plate to the front.

A plate is a ``<plate>`` block of ``Metadata/model_settings.config``: its ``plater_id`` (1-based, its place), its own
settings and the instances on it (``model_instance``). The copy adds an empty block, removes one or moves one to the
front, then numbers the blocks again in their order; each block keeps every other byte, so its settings, instances
and thumbnails go with it. Removing a plate never removes its objects: their instances stay in the model, outside
every plate, as PartPlateList::delete_plate leaves them (the workspace moves them there; placement.py writes it).

The project's per-plate wipe tower positions (``wipe_tower_x`` / ``wipe_tower_y``) follow Orca's lists: a removed
plate's entry goes (``delete_plate``), a new plate gets one (``set_default_wipe_tower_pos_for_plate``'s resize to
the plate count with the first value), and moving a plate leaves them as they are, as ``move_plate_to_index`` does.
"""

from __future__ import annotations

import math
import re
from typing import Literal

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.workspace.object_settings import _line, _value_of
from backend.app.farm.workspace.placement import (
    _KEY,
    _METADATA,
    _VALUE,
    InstanceTransform,
    InvalidPlacement,
    _attr,
    _build_items,
    _numbers,
    place,
)
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
    ``plate`` moved to the front. Plates are numbered as they are when the op comes, after the ops before it."""

    op: Literal["add", "remove", "front"]
    plate: int | None = Field(default=None, ge=1, le=MAX_PLATES)


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


def moves(
    count: int, ops: list[PlateOp], bed: tuple[float, float]
) -> tuple[dict[int, tuple[float, float]], tuple[float, float]]:
    """How far each of the file's plates (from 0) moves with its instances, and the instances on no plate.

    PartPlateList lays ``n`` plates in ``_columns(n)`` columns, each ``plate_stride`` (the bed plus a fifth) from the
    last, rows going to the front (``compute_origin``); the objects on no plate sit after them
    (``compute_origin_for_unprintable``). A plate's instances go with it (``set_pos_and_size``); a removed plate's go
    to the place of the objects on no plate (``delete_plate``), and those move with that place when a plate goes, not
    when one comes (``create_plate``: ``with_unprintable_move`` false)."""
    w, d = bed[0] * (1 + LOGICAL_GAP), bed[1] * (1 + LOGICAL_GAP)

    def origin(i: int, cols: int) -> tuple[float, float]:
        return (i % cols) * w, -(i // cols) * d

    def outside(n: int) -> tuple[float, float]:
        c = _columns(n)
        return origin(c * c + c - 1, c + 1) if n == c * c else origin(n, c)

    order: list[int | None] = list(range(count))
    delta = dict.fromkeys(range(count), (0.0, 0.0))
    off_plate: set[int] = set()  # the file's plates removed: their instances are on no plate now
    out_delta = (0.0, 0.0)
    add = lambda a, b, c: (a[0] + c[0] - b[0], a[1] + c[1] - b[1])  # noqa: E731  a moved from b to c
    for op in ops:
        n = len(order)
        before = {p: origin(i, _columns(n)) for i, p in enumerate(order) if p is not None}
        removed = None
        if op.op == "add":
            order.append(None)
        elif op.op == "remove":
            removed = order.pop(op.plate - 1)
        else:
            order.insert(0, order.pop(op.plate - 1))
        m = len(order)
        for i, p in enumerate(order):
            if p is not None:
                delta[p] = add(delta[p], before[p], origin(i, _columns(m)))
        if op.op == "remove":
            shift = add((0.0, 0.0), outside(n), outside(m))
            out_delta = add(out_delta, (0.0, 0.0), shift)
            for p in off_plate:
                delta[p] = add(delta[p], (0.0, 0.0), shift)
            if removed is not None:
                delta[removed] = add(delta[removed], before[removed], outside(m))
                off_plate.add(removed)
    return delta, out_delta


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


def _move_instances(model: bytes, settings: bytes, cfg: dict, ops: list[PlateOp], count: int) -> bytes:
    """``model`` with every instance moved as its plate moves (``moves``), each build item's other bytes kept."""
    delta, out_delta = moves(count, ops, _bed(cfg))
    where = _membership(settings)
    wanted = []
    for (object_id, instance), spec in _instances(model).items():
        p = where.get((object_id, instance))
        dx, dy = out_delta if p is None else delta[p]
        if abs(dx) > 1e-9 or abs(dy) > 1e-9:
            m = list(spec)
            m[9], m[10] = m[9] + dx, m[10] + dy
            wanted.append(InstanceTransform(object_id=object_id, instance=instance, matrix=m))
    if not wanted:
        return model
    try:
        return place(model, wanted)[0]
    except InvalidPlacement as exc:
        raise InvalidPlateStructure(f"the plates' objects can't be moved with them: {exc}") from exc


def _instances(model: bytes) -> dict[tuple[str, int], tuple[float, ...]]:
    """Each build item (object id, its instance number) -> its transform's 12 numbers."""
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


def apply(
    settings: bytes, cfg: dict, ops: list[PlateOp], model: bytes | None = None
) -> tuple[bytes, dict, bytes | None, list[dict]]:
    """``model_settings.config``, the project settings and the model (its instances moved with their plates, when
    given) with the plates added, removed and moved, and what changed: ``{key: "plate", op, plate, plates}``
    (``plates``: how many there are after it)."""
    original = settings
    if len(ops) > MAX_OPS:
        raise InvalidPlateStructure(f"too many plate changes at once (at most {MAX_OPS})")
    start, end, blocks = _blocks(settings)
    sources: list[int | None] = list(range(len(blocks)))  # each plate's block in the original (None: new)
    cfg = dict(cfg)
    towers = {k: list(cfg[k]) for k in WIPE_TOWER if isinstance(cfg.get(k), list)}
    applied = []
    for op in ops:
        count = len(sources)
        if op.op == "add":
            if count >= MAX_PLATES:
                raise InvalidPlateStructure(f"a project has at most {MAX_PLATES} plates")
            sources.append(None)
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
        applied.append({"key": "plate", "op": op.op, "plate": op.plate, "plates": len(sources)})
    new_blocks = [_numbered(blocks[s] if s is not None else _empty(blocks[-1]), n) for n, s in enumerate(sources, 1)]
    out = settings[:start] + b"".join(new_blocks) + settings[end:]
    # read back: the same document around the plates, each plate the block it came from but for its number
    _, new_end, check = _blocks(out)
    if out[:start] != settings[:start] or out[new_end:] != settings[end:] or len(check) != len(sources):
        raise InvalidPlateStructure("model_settings.config changed beyond its plates")
    for n, (block, s) in enumerate(zip(check, sources, strict=True), 1):
        if _value_of(_plater_id(block).group(0)) != str(n) or (s is not None and _dump(block) != _dump(blocks[s])):
            raise InvalidPlateStructure("a plate changed beyond its number")
    for k, values in towers.items():
        cfg[k] = values
    if model is not None:
        model = _move_instances(model, original, cfg, ops, len(blocks))
    return out, cfg, model, applied
