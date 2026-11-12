"""OrcaSlicer's own Arrange for the workspace (fork), through the slicer sidecar's ``/arrange`` (slicer-api/orca).

The workspace sends the project as its draft has it (``prepare``: every instance on a plate given an ``identify_id``,
Bambu Studio's per-instance id, when it has none); Orca's CLI arranges it and exports it again with its own object
numbering but the same ``identify_id``s (``read_result``). Each arranged instance's new transform goes back to the
workspace as a placement, so the copy writes it with the placement writer and the file's own metadata stays.

An object whose mesh the CLI moved about its origin (re-centred) gets its transform corrected by that shift, read
from the two files' object-space bounds.
"""

from __future__ import annotations

import io
import re
import zipfile

import defusedxml.ElementTree as SafeET
import numpy as np
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.plates.geometry import _collect, _read_models
from backend.app.farm.workspace import project
from backend.app.farm.workspace.placement import InvalidPlacement, _build_items, _numbers
from backend.app.farm.workspace.plate_structure import MAX_PLATES, _blocks, _entries, _identify_id
from backend.app.utils.xml_prefixes import declared

MAX_ARRANGED_BYTES = 200 * 1024 * 1024  # the sidecar's answer: a project like the one sent


class InvalidArrange(ValueError):
    """An arrange the workspace can't take (the message says why)."""


class ArrangeOptions(BaseModel):
    """Orca's arrange dialog (_render_arrange_menu): its options but Spacing, which the CLI fixes at "auto" (0)."""

    plate: int = Field(default=0, ge=0, le=MAX_PLATES)  # 0: every plate (Arrange all objects); N: plate N
    allow_rotations: bool = False
    align_to_y_axis: bool = False
    allow_multi_material: bool = True
    avoid_calibration_region: bool = False

    def form(self) -> dict[str, str]:
        """The sidecar's form fields."""
        on = lambda v: "true" if v else "false"  # noqa: E731
        return {
            "plate": str(self.plate),
            "allowRotations": on(self.allow_rotations),
            "alignToYAxis": on(self.align_to_y_axis),
            "allowMultiMaterial": on(self.allow_multi_material),
            "avoidCalibrationRegion": on(self.avoid_calibration_region),
        }


Key = tuple[str, int]  # an instance: (object id, its instance number)


def _plate_instances(settings: bytes) -> dict[Key, tuple[int, str | None]]:
    """Each instance a plate lists -> (its plate from 1, its identify_id)."""
    try:
        root = SafeET.fromstring(declared(settings))
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidArrange(f"model_settings.config is not valid XML: {exc}") from exc
    out: dict[Key, tuple[int, str | None]] = {}
    for n, plate in enumerate((el for el in root if el.tag == "plate"), 1):
        for inst in plate.iter("model_instance"):
            meta = {m.get("key"): m.get("value") for m in inst if m.tag == "metadata"}
            if meta.get("object_id"):
                out[(meta["object_id"], int(meta.get("instance_id") or 0))] = (n, meta.get("identify_id") or None)
    return out


def _with_identify(entry: bytes, value: int) -> bytes:
    """A ``<model_instance>`` with an ``identify_id`` line before its end, written as its other lines are."""
    line = f'<metadata key="identify_id" value="{value}"/>'.encode()
    close = entry.rfind(b"</model_instance>")
    start = entry.rfind(b"\n", 0, close) + 1
    if start == 0 or entry[start:close].strip():  # on one line with the rest
        return entry[:close] + line + entry[close:]
    meta = re.search(rb"\n([ \t]*)<metadata", entry)
    indent = meta.group(1) if meta else entry[start:close] + b"  "
    newline = b"\r\n" if b"\r\n" in entry else b"\n"
    return entry[:start] + indent + line + newline + entry[start:]


def prepare(data: bytes) -> tuple[bytes, dict[str, Key]]:
    """The project with an ``identify_id`` on every instance a plate lists (new ones after the file's highest), and
    which instance each one is."""
    zf = project.open_archive(data)
    settings = project._read_small(zf, project.MODEL_SETTINGS_ENTRY)
    if settings is None:
        raise InvalidArrange("the project has no plates to arrange on")
    start, end, blocks = _blocks(settings)
    next_id = _identify_id(settings)
    stamped = []
    for blk in blocks:
        out, last = [], 0
        for _key, a, b in _entries(blk):
            entry = blk[a:b]
            if b"identify_id" not in entry:
                next_id += 1
                entry = _with_identify(entry, next_id)
            out += [blk[last:a], entry]
            last = b
        stamped.append(b"".join(out) + blk[last:])
    new_settings = settings[:start] + b"".join(stamped) + settings[end:]
    listed = _plate_instances(new_settings)
    identities = {ident: key for key, (_, ident) in listed.items() if ident}
    if len(identities) != len(listed):
        raise InvalidArrange("two instances share one identify_id")
    if new_settings == settings:
        return data, identities
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for info in zf.infolist():
            zout.writestr(info, new_settings if info.filename == project.MODEL_SETTINGS_ENTRY else zf.read(info))
    return buf.getvalue(), identities


def plate_count(data: bytes) -> int:
    """How many plates a project has."""
    settings = project._read_small(project.open_archive(data), project.MODEL_SETTINGS_ENTRY) or b""
    return settings.count(b"<plate>")


def _items(model: bytes) -> dict[Key, tuple[float, ...]]:
    out: dict[Key, tuple[float, ...]] = {}
    count: dict[str, int] = {}
    try:
        for item in _build_items(model):
            oid = item.get("objectid", "")
            out[(oid, count.get(oid, 0))] = _numbers(item.get("transform"))
            count[oid] = count.get(oid, 0) + 1
    except InvalidPlacement as exc:
        raise InvalidArrange(f"the model's instances can't be read: {exc}") from exc
    return out


def _names(settings: bytes) -> dict[str, str]:
    """Each object's name (model_settings.config)."""
    try:
        root = SafeET.fromstring(declared(settings))
    except (SafeET.ParseError, DefusedXmlException):
        return {}
    return {
        el.get("id", ""): next((m.get("value", "") for m in el if m.tag == "metadata" and m.get("key") == "name"), "")
        for el in root
        if el.tag == "object"
    }


def _object_min(models: dict, object_id: str) -> np.ndarray | None:
    """The lowest corner of an object's meshes in its own coordinates (its parts placed), or None without one."""
    parts: list = []
    _collect(models, "/" + project.MODEL_ENTRY, object_id, np.eye(3), np.zeros(3), parts)
    points = [v for v, _ in parts if len(v)]
    return np.vstack(points).min(axis=0) if points else None


def read_result(sent: bytes, identities: dict[str, Key], arranged: bytes) -> tuple[list[dict], int]:
    """The arranged instances, ``{object_id, instance, matrix, plate}`` (the instance as ``sent`` numbers it, its new
    transform, its plate from 1), and how many plates the arranged project has. Only the instances the arranged
    project lists on a plate (every one, or one plate's) found in ``sent`` (by identify_id, else by a unique name)."""
    if len(arranged) > MAX_ARRANGED_BYTES:
        raise InvalidArrange("the arranged project is too large")
    try:
        out_zip = zipfile.ZipFile(io.BytesIO(arranged))
        out_settings = out_zip.read(project.MODEL_SETTINGS_ENTRY)
        out_model = out_zip.read(project.MODEL_ENTRY)
    except (zipfile.BadZipFile, KeyError) as exc:
        raise InvalidArrange(f"the slicer's answer is not a 3MF project: {exc}") from exc
    in_zip = project.open_archive(sent)
    in_items, out_items = _items(project.read_model(in_zip)), _items(out_model)
    listed = _plate_instances(out_settings)
    pairs = {out: identities.get(ident or "") for out, (_, ident) in listed.items()}
    # an instance on no plate has no identify_id: the CLI gives it one of its own. Matched by its object's name when
    # that name is one object's alone, on both sides, among those left over
    left_in = {k for k in in_items if k not in pairs.values()}
    names_in, names_out = _names(project._read_small(in_zip, project.MODEL_SETTINGS_ENTRY) or b""), _names(out_settings)
    for out, key in list(pairs.items()):
        if key is not None:
            continue
        name = names_out.get(out[0])
        ins = {o for o, _ in left_in if names_in.get(o) == name}
        outs = {o for (o, _), k in pairs.items() if k is None and names_out.get(o) == name}
        if name and len(ins) == 1 and len(outs) == 1 and (next(iter(ins)), out[1]) in left_in:
            pairs[out] = (next(iter(ins)), out[1])
    out_models = in_models = None
    result = []
    for (out_oid, out_inst), (plate, _) in listed.items():
        key = pairs.get((out_oid, out_inst))
        if key is None or key not in in_items or (out_oid, out_inst) not in out_items:
            continue
        m = list(out_items[(out_oid, out_inst)])
        # a mesh moved about its origin by the CLI: the same shift, through the new transform
        out_models = out_models or _read_models(out_zip)
        in_models = in_models or _read_models(in_zip)
        a, b = _object_min(in_models, key[0]), _object_min(out_models, out_oid)
        if a is not None and b is not None and np.abs(b - a).max() > 1e-4:
            d = b - a
            m[9:12] = [m[9 + j] + sum(d[i] * m[3 * i + j] for i in range(3)) for j in range(3)]
        result.append({"object_id": key[0], "instance": key[1], "matrix": m, "plate": plate})
    return result, out_settings.count(b"<plate>")
