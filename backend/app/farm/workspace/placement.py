"""Where an edited copy puts its instances and parts (fork): OrcaSlicer's Move, Rotate and Scale.

An instance is a build item of ``3D/3dmodel.model``: bbs_3mf writes one ``<item>`` per instance, in
instance order, so the n-th item of an object is its instance n (the 3D view counts them the same way).
Its ``transform`` attribute (a 3x4 matrix by rows, the translation last, as 3MF has it) is the only
thing a placement changes. The bytes around it, the meshes, the parts and their own matrices stay as
they are, and the result is parsed back and compared item by item. Only ``<build>…</build>`` is parsed
(inside the model's own root tag, for its namespaces): OrcaSlicer keeps every mesh in the same file.

A part is a ``<component>`` of its object in ``3D/3dmodel.model`` (its ``objectid`` is the part's id in
``model_settings.config``); placing it rewrites that component's ``transform`` and the part's ``matrix``
metadata in ``model_settings.config``, nothing else of either file (``place_parts``).

Shortcut: the assembly view's own placement (``assemble_item`` in ``model_settings.config``) and the
plate thumbnails are left as they were. The slicer uses neither to slice, and remakes the thumbnails at
its next slice.
"""

from __future__ import annotations

import math
import re

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

MAX_TRANSFORMS = 1000
MAX_OFFSET = 100_000.0  # mm from the origin: far beyond any bed, short of nonsense
MIN_SCALE, MAX_SCALE = 1e-3, 1e3  # per axis
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)

_ROOT = re.compile(rb"<model\b[^>]*>")
_BUILD = re.compile(rb"<build\b[^>]*>")
_ITEM = re.compile(rb"<item\b[^>]*>")
_OBJECT_ID = re.compile(rb"""\sobjectid\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_TRANSFORM = re.compile(rb"""\stransform\s*=\s*(?:"([^"]*)"|'([^']*)')""")


class InvalidPlacement(ValueError):
    pass


class InstanceTransform(BaseModel):
    """One instance's new transform: the 12 numbers of 3MF's ``transform``."""

    object_id: str = Field(pattern=r"^[0-9]{1,9}$")
    instance: int = Field(ge=0, le=100_000)
    matrix: list[float] = Field(min_length=12, max_length=12)


def check_matrix(m: list[float]) -> None:
    """A placement the slicer takes: finite, not mirrored or flattened, each axis scaled 0.001 to 1000 times,
    within ``MAX_OFFSET``. Mirroring is a tool of its own in Orca, not one of these."""
    if not all(math.isfinite(v) for v in m):
        raise InvalidPlacement("the transform has a value that is not a number")
    a = [m[0:3], m[3:6], m[6:9]]  # 3MF: a point (row) times these rows, then the translation
    det = (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )
    if det <= 0:
        raise InvalidPlacement("the transform mirrors or flattens the object")
    for row in a:
        if not MIN_SCALE <= math.sqrt(sum(v * v for v in row)) <= MAX_SCALE:
            raise InvalidPlacement(f"a scale is outside {MIN_SCALE:g} to {MAX_SCALE:g} times")
    if any(abs(v) > MAX_OFFSET for v in m[9:]):
        raise InvalidPlacement(f"the position is more than {MAX_OFFSET:g} mm away")


def _number(v: float) -> str:
    text = f"{v:.9f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def format_matrix(m: list[float]) -> str:
    return " ".join(_number(v) for v in m)


def _numbers(text: str | None) -> tuple[float, ...]:
    if not text:
        return IDENTITY
    values = tuple(float(v) for v in text.split())
    if len(values) != 12:
        raise InvalidPlacement("a build item's transform is not 12 numbers")
    return values


def _build_span(model: bytes) -> tuple[int, int]:
    """Where ``<build>…</build>`` is in the model (after ``</resources>``, so a mesh can't hold it)."""
    start = _BUILD.search(model, max(0, model.rfind(b"</resources>")))
    end = model.find(b"</build>", start.end()) if start else -1
    if start is None or end < 0:
        raise InvalidPlacement("the model has no build items to place")
    return start.start(), end + len(b"</build>")


def _build_items(model: bytes) -> list[dict[str, str]]:
    """The build items' attributes, parsing ``<build>`` alone inside the model's root tag (its namespaces)."""
    root = _ROOT.search(model)
    if root is None:
        raise InvalidPlacement("3D/3dmodel.model has no <model> root")
    start, end = _build_span(model)
    try:
        build = SafeET.fromstring(root.group(0) + model[start:end] + b"</model>")[0]
    except (SafeET.ParseError, DefusedXmlException, IndexError) as exc:
        raise InvalidPlacement(f"the model's build items are not valid XML: {exc}") from exc
    return [dict(el.attrib) for el in build if el.tag.rsplit("}", 1)[-1] == "item"]


def _without_transform(attrs: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in attrs.items() if k != "transform"}


def place(model: bytes, transforms: list[InstanceTransform]) -> tuple[bytes, list[dict]]:
    """``model`` with each instance's ``transform`` replaced; the placements made, as the copy records them."""
    items_before = _build_items(model)
    start, end = _build_span(model)
    found: dict[tuple[str, int], re.Match] = {}
    count: dict[str, int] = {}
    for m in _ITEM.finditer(model, start, end):
        oid = _OBJECT_ID.search(m.group(0))
        if oid is None:
            continue
        key = (oid.group(1) or oid.group(2) or b"").decode("ascii", "replace")
        found[(key, count.get(key, 0))] = m
        count[key] = count.get(key, 0) + 1
    if len(found) != len(items_before):
        raise InvalidPlacement("the build items could not be read one by one")
    wanted: dict[tuple[str, int], list[float]] = {}
    for t in transforms:
        key = (t.object_id, t.instance)
        if key not in found:
            raise InvalidPlacement(f"object {t.object_id} has no instance {t.instance}")
        if key in wanted:
            raise InvalidPlacement(f"object {t.object_id} instance {t.instance} is placed twice")
        check_matrix(t.matrix)
        wanted[key] = t.matrix
    order = [key for key, _ in sorted(found.items(), key=lambda kv: kv[1].start())]
    out, last, placed = [], 0, []
    for key in order:
        if key not in wanted:
            continue
        m = found[key]
        tag = m.group(0)
        old = _TRANSFORM.search(tag)
        before = (old.group(1) or old.group(2) or b"").decode("ascii") if old else ""
        value = format_matrix(wanted[key])
        new_attr = f' transform="{value}"'.encode("ascii")
        tag = tag[: old.start()] + new_attr + tag[old.end() :] if old else tag[:5] + new_attr + tag[5:]
        out += [model[last : m.start()], tag]
        last = m.end()
        placed.append({"key": "transform", "object_id": key[0], "instance": key[1], "before": before, "after": value})
    out.append(model[last:])
    result = b"".join(out)
    after = _build_items(result)
    if len(after) != len(items_before):
        raise InvalidPlacement("the placed model does not hold the same build items")
    for key, a, b in zip(order, items_before, after, strict=True):
        if _without_transform(a) != _without_transform(b):
            raise InvalidPlacement(f"build item {key} changed beyond its transform")
        expected = tuple(float(_number(v)) for v in wanted[key]) if key in wanted else _numbers(a.get("transform"))
        if _numbers(b.get("transform")) != expected:
            raise InvalidPlacement(f"build item {key} does not read back as placed")
    return result, placed


# ---- parts --------------------------------------------------------------------------------------------------


class PartTransform(BaseModel):
    """One part's new transform: its component's 12 numbers, shared by every instance of the object (Orca's
    ModelVolume)."""

    object_id: str = Field(pattern=r"^[0-9]{1,9}$")
    part_id: str = Field(pattern=r"^[0-9]{1,9}$")
    matrix: list[float] = Field(min_length=12, max_length=12)


_OBJECT = re.compile(rb"<object\b[^>]*>")
_PART = re.compile(rb"<part\b[^>]*>")
_ID = re.compile(rb"""\sid\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_COMPONENT = re.compile(rb"<component\b[^>]*>")
_METADATA = re.compile(rb"<metadata\b[^>]*>")
_KEY = re.compile(rb"""\skey\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_VALUE = re.compile(rb"""\svalue\s*=\s*(?:"([^"]*)"|'([^']*)')""")


def _attr(tag: bytes, rx: re.Pattern) -> str | None:
    m = rx.search(tag)
    return (m.group(1) or m.group(2) or b"").decode("ascii", "replace") if m else None


def _element(data: bytes, tag_rx: re.Pattern, close: bytes, wanted: str, start: int, end: int) -> tuple[int, int]:
    """Where the element of ``tag_rx`` with id ``wanted`` is, between ``start`` and ``end``; exactly one."""
    found = [m for m in tag_rx.finditer(data, start, end) if _attr(m.group(0), _ID) == wanted]
    if len(found) != 1:
        raise InvalidPlacement(f"{wanted} is not one element of the model")
    m = found[0]
    if m.group(0).endswith(b"/>"):
        return m.start(), m.end()
    stop = data.find(close, m.end(), end)
    if stop < 0:
        raise InvalidPlacement(f"element {wanted} is not closed")
    return m.start(), stop + len(close)


def _as4(m: list[float] | tuple[float, ...]) -> list[list[float]]:
    """3MF's 12 numbers as a 4x4 matrix on column vectors (they are stored by columns, as Orca reads them)."""
    return [[m[0], m[3], m[6], m[9]], [m[1], m[4], m[7], m[10]], [m[2], m[5], m[8], m[11]], [0.0, 0.0, 0.0, 1.0]]


def _mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _inverse(m: list[list[float]]) -> list[list[float]]:
    """An affine 4x4's inverse (its 3x3 by cofactors, then the translation)."""
    a = [row[:3] for row in m[:3]]
    det = (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )
    if abs(det) < 1e-15:
        raise InvalidPlacement("a part's transform flattens it")
    inv = [
        [
            (
                a[(c + 1) % 3][(r + 1) % 3] * a[(c + 2) % 3][(r + 2) % 3]
                - a[(c + 1) % 3][(r + 2) % 3] * a[(c + 2) % 3][(r + 1) % 3]
            )
            / det
            for c in range(3)
        ]
        for r in range(3)
    ]
    t = [-sum(inv[r][k] * m[k][3] for k in range(3)) for r in range(3)]
    return [inv[0] + [t[0]], inv[1] + [t[1]], inv[2] + [t[2]], [0.0, 0.0, 0.0, 1.0]]


def _part_settings(settings: bytes) -> list[tuple[str, tuple]]:
    """Every element of model_settings.config with its attributes, in order: what a part's move must leave be."""
    try:
        root = SafeET.fromstring(settings)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidPlacement(f"model_settings.config is not valid XML: {exc}") from exc
    return [(el.tag, tuple(sorted(el.attrib.items()))) for el in root.iter()]


def _components(model: bytes, start: int, end: int) -> list[dict[str, str]]:
    root = _ROOT.search(model)
    if root is None:
        raise InvalidPlacement("3D/3dmodel.model has no <model> root")
    try:
        obj = SafeET.fromstring(root.group(0) + model[start:end] + b"</model>")[0]
    except (SafeET.ParseError, DefusedXmlException, IndexError) as exc:
        raise InvalidPlacement(f"an object's components are not valid XML: {exc}") from exc
    return [dict(el.attrib) for el in obj.iter() if el.tag.rsplit("}", 1)[-1] == "component"]


def place_parts(
    model: bytes, settings: bytes | None, parts: list[PartTransform]
) -> tuple[bytes, bytes | None, list[dict]]:
    """``model`` with each part's component ``transform`` replaced, and ``model_settings.config`` with that part's
    ``matrix`` moved the same way (Orca writes it as the part's matrix times its source transform, so the new one is
    the change times the old one). Everything else of both stays byte for byte, and both are parsed back."""
    placed = []
    resources = model.rfind(b"</resources>")
    if resources < 0:
        raise InvalidPlacement("the model has no resources")
    seen: set[tuple[str, str]] = set()
    for p in parts:
        key = (p.object_id, p.part_id)
        if key in seen:
            raise InvalidPlacement(f"part {p.part_id} of object {p.object_id} is placed twice")
        seen.add(key)
        check_matrix(p.matrix)
        start, end = _element(model, _OBJECT, b"</object>", p.object_id, 0, resources)
        before_components = _components(model, start, end)
        comps = [m for m in _COMPONENT.finditer(model, start, end) if _attr(m.group(0), _OBJECT_ID) == p.part_id]
        if len(comps) != 1:
            raise InvalidPlacement(f"object {p.object_id} has no part {p.part_id} to place")
        tag = comps[0].group(0)
        old = _TRANSFORM.search(tag)
        before = (old.group(1) or old.group(2) or b"").decode("ascii") if old else ""
        value = format_matrix(p.matrix)
        new_attr = f' transform="{value}"'.encode("ascii")
        new_tag = tag[: old.start()] + new_attr + tag[old.end() :] if old else tag[:10] + new_attr + tag[10:]
        model = model[: comps[0].start()] + new_tag + model[comps[0].end() :]
        resources += len(new_tag) - len(tag)
        after_components = _components(model, start, end + len(new_tag) - len(tag))
        for a, b in zip(before_components, after_components, strict=True):
            same = _without_transform(a) == _without_transform(b)
            if not same or (a.get("objectid") != p.part_id and a.get("transform") != b.get("transform")):
                raise InvalidPlacement(f"object {p.object_id}'s components changed beyond part {p.part_id}")
        if tuple(float(_number(v)) for v in p.matrix) != _numbers(
            next(c for c in after_components if c.get("objectid") == p.part_id).get("transform")
        ):
            raise InvalidPlacement(f"part {p.part_id} does not read back as placed")
        record = {
            "key": "part_transform",
            "object_id": p.object_id,
            "part_id": p.part_id,
            "before": before,
            "after": value,
        }
        record["matrix"] = None
        if settings is not None:
            settings, record["matrix"] = _move_matrix(settings, p, _numbers(before) if before else IDENTITY)
        placed.append(record)
    return model, settings, placed


def _move_matrix(settings: bytes, p: PartTransform, old_spec: tuple[float, ...]) -> tuple[bytes, str | None]:
    """The part's ``matrix`` metadata times the change of its component transform; None when it has none."""
    before_all = _part_settings(settings)
    o_start, o_end = _element(settings, _OBJECT, b"</object>", p.object_id, 0, len(settings))
    p_start, p_end = _element(settings, _PART, b"</part>", p.part_id, o_start, o_end)
    meta = [m for m in _METADATA.finditer(settings, p_start, p_end) if _attr(m.group(0), _KEY) == "matrix"]
    if not meta:
        return settings, None
    if len(meta) > 1:
        raise InvalidPlacement(f"part {p.part_id} has more than one matrix")
    tag = meta[0].group(0)
    old_value = _attr(tag, _VALUE) or ""
    try:
        values = [float(v) for v in old_value.split()]
    except ValueError as exc:
        raise InvalidPlacement(f"part {p.part_id}'s matrix is not numbers") from exc
    if len(values) != 16 or not all(math.isfinite(v) for v in values):
        raise InvalidPlacement(f"part {p.part_id}'s matrix is not 16 numbers")
    old = [values[4 * r : 4 * r + 4] for r in range(4)]  # bbs_3mf writes it row by row
    change = _mul(_as4(p.matrix), _inverse(_as4(old_spec)))
    new = _mul(change, old)
    value = " ".join(_number(new[r][c]) for r in range(4) for c in range(4))
    v = _VALUE.search(tag)
    new_tag = tag[: v.start()] + f' value="{value}"'.encode("ascii") + tag[v.end() :]
    result = settings[: meta[0].start()] + new_tag + settings[meta[0].end() :]
    after_all = _part_settings(result)
    changed = [i for i, (a, b) in enumerate(zip(before_all, after_all, strict=True)) if a != b]
    if len(changed) != 1 or dict(after_all[changed[0]][1]).get("value") != value:
        raise InvalidPlacement(f"model_settings.config changed beyond part {p.part_id}'s matrix")
    return result, value
