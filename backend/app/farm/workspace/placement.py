"""Where an edited copy puts its instances (fork): OrcaSlicer's Move, Rotate and Scale.

An instance is a build item of ``3D/3dmodel.model``: bbs_3mf writes one ``<item>`` per instance, in
instance order, so the n-th item of an object is its instance n (the 3D view counts them the same way).
Its ``transform`` attribute (a 3x4 matrix by rows, the translation last, as 3MF has it) is the only
thing a placement changes. The bytes around it, the meshes, the parts and their own matrices stay as
they are, and the result is parsed back and compared item by item.

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


def _build_items(model: bytes) -> list[dict[str, str]]:
    try:
        root = SafeET.fromstring(model)
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidPlacement(f"3D/3dmodel.model is not valid XML: {exc}") from exc
    build = next((el for el in root if el.tag.rsplit("}", 1)[-1] == "build"), None)
    items = [] if build is None else [el for el in build if el.tag.rsplit("}", 1)[-1] == "item"]
    return [dict(el.attrib) for el in items]


def _without_transform(attrs: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in attrs.items() if k != "transform"}


def place(model: bytes, transforms: list[InstanceTransform]) -> tuple[bytes, list[dict]]:
    """``model`` with each instance's ``transform`` replaced; the placements made, as the copy records them."""
    items_before = _build_items(model)
    start = re.search(rb"<build\b[^>]*>", model)
    end = model.find(b"</build>", start.end()) if start else -1
    if start is None or end < 0:
        raise InvalidPlacement("the model has no build items to place")
    found: dict[tuple[str, int], re.Match] = {}
    count: dict[str, int] = {}
    for m in _ITEM.finditer(model, start.end(), end):
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
