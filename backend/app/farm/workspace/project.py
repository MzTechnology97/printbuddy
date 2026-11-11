"""Reading a 3MF for the workspace, and writing its edited copy (fork).

Everything here works on bytes in memory and never touches the library: the routes read the file
and save the copy. An archive is refused before anything is parsed when it is too big, has too
many entries, inflates too much (ZIP bomb), or names an entry outside the archive (Zip Slip).

The copy rewrites ``Metadata/project_settings.config``, and ``3D/3dmodel.model`` when instances are
placed anew (placement.py). Every other entry (geometry, plates, per-object settings, paint data,
thumbnails, ``slice_info.config``, unknown metadata) is copied with its name, order, date, compression
and bytes, and the result is checked entry by entry (CRC and size) before it is returned.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
import zlib
from pathlib import Path, PurePosixPath

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException

from backend.app.farm.detect import (
    MODEL_ENTRY,
    PROJECT_SETTINGS_ENTRY,
    SLICEABLE,
    SLICER_CAD,
    _read_header_metadata,
    detect_slicer,
)
from backend.app.utils.xml_prefixes import declared

MODEL_SETTINGS_ENTRY = "Metadata/model_settings.config"

MAX_ARCHIVE_BYTES = 128 * 1024 * 1024  # the source is read whole; farm 3MFs are well under 50 MB
MAX_ENTRIES = 10_000
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_RATIO = 200  # inflation of one big entry beyond this is a ZIP bomb, not a mesh
MAX_CONFIG_BYTES = 32 * 1024 * 1024  # project / model settings, read whole
# 3D/3dmodel.model, read whole only to place instances anew (Orca keeps its meshes in it; only <build> is parsed)
MAX_MODEL_BYTES = 256 * 1024 * 1024
_COPY_CHUNK = 1024 * 1024

# What the workspace can do with a file
KIND_PROJECT = "project"  # OrcaSlicer / Bambu Studio project: settings editable
KIND_SLICED = "sliced"  # .gcode.3mf: already sliced, read only
KIND_MODEL = "model"  # CAD / plain model 3MF: no slicing settings
KIND_OTHER_SLICER = "other_slicer"  # settings of a slicer without a sidecar: read only


class ArchiveError(Exception):
    """The file is not a 3MF the workspace can read safely (the message says why)."""


# What a damaged entry raises while it is read: bad CRC or header, truncated deflate, encrypted, unknown method.
_ENTRY_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError)


def _safe_name(name: str) -> bool:
    if not name or "\\" in name or "\x00" in name or name.startswith("/") or ":" in name.split("/")[0]:
        return False
    return ".." not in PurePosixPath(name).parts


def open_archive(data: bytes | Path) -> zipfile.ZipFile:
    """The archive (bytes, or a file), after the size, entry and path checks (``ArchiveError`` otherwise)."""
    size = data.stat().st_size if isinstance(data, Path) else len(data)
    if size > MAX_ARCHIVE_BYTES:
        raise ArchiveError(f"the file is larger than {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB")
    try:
        zf = zipfile.ZipFile(data if isinstance(data, Path) else io.BytesIO(data))
        infos = zf.infolist()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, ValueError) as exc:
        raise ArchiveError(f"not a valid 3MF (zip) archive: {exc}") from exc
    if len(infos) > MAX_ENTRIES:
        raise ArchiveError(f"too many entries in the archive ({len(infos)})")
    seen: set[str] = set()
    total = 0
    for info in infos:
        if not _safe_name(info.filename):
            raise ArchiveError(f"unsafe path inside the archive: {info.filename[:100]!r}")
        if info.filename in seen:
            raise ArchiveError(f"duplicate entry in the archive: {info.filename[:100]!r}")
        seen.add(info.filename)
        total += info.file_size
        if info.file_size > 16 * 1024 * 1024 and info.file_size > MAX_RATIO * max(info.compress_size, 1):
            raise ArchiveError(f"{info.filename[:100]!r} inflates too much (ZIP bomb?)")
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ArchiveError("the archive inflates beyond 2 GB")
    if MODEL_ENTRY not in seen:
        raise ArchiveError(f"missing {MODEL_ENTRY}: not a 3MF")
    return zf


def _read_small(zf: zipfile.ZipFile, name: str, limit: int = MAX_CONFIG_BYTES) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ArchiveError(f"{name} is too large")
    try:
        return zf.read(info)
    except _ENTRY_ERRORS as exc:
        raise ArchiveError(f"{name} cannot be read: {exc}") from exc


def cannot_place(zf: zipfile.ZipFile) -> str | None:
    """Why a copy of this archive can't place instances anew (None: it can), known before anything is moved."""
    try:
        info = zf.getinfo(MODEL_ENTRY)
    except KeyError:
        return f"it has no {MODEL_ENTRY}"
    if info.file_size > MAX_MODEL_BYTES:
        return f"its {MODEL_ENTRY} is larger than {MAX_MODEL_BYTES // (1024 * 1024)} MB"
    return None


def read_model(zf: zipfile.ZipFile) -> bytes:
    """``3D/3dmodel.model`` to place instances in; ``ArchiveError`` when ``cannot_place`` says why not."""
    why = cannot_place(zf)
    if why:
        raise ArchiveError(f"the objects can't be placed anew: {why}")
    return _read_small(zf, MODEL_ENTRY, MAX_MODEL_BYTES) or b""


def read_settings(zf: zipfile.ZipFile) -> dict | None:
    """``project_settings.config`` (None when the 3MF has none); ``ArchiveError`` when it isn't a JSON object."""
    raw = _read_small(zf, PROJECT_SETTINGS_ENTRY)
    if raw is None:
        return None
    try:
        cfg = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"{PROJECT_SETTINGS_ENTRY} is not valid JSON: {exc}") from exc
    if not isinstance(cfg, dict):
        raise ArchiveError(f"{PROJECT_SETTINGS_ENTRY} is not a JSON object")
    return cfg


def _meta(el) -> dict[str, str]:
    return {m.get("key"): m.get("value", "") for m in el.findall("metadata") if m.get("key")}


def read_structure(zf: zipfile.ZipFile, settings: dict | None) -> dict:
    """Plates and objects from ``model_settings.config``: name, filament, per-object setting overrides,
    each plate's metadata.

    An override is a key of the object (or of one of its parts) that is also a project setting: the
    slicer applies it to that object instead of the project value. ``warnings`` lists what could not
    be read; the file can then be inspected only.
    """
    raw = _read_small(zf, MODEL_SETTINGS_ENTRY)
    if raw is None:
        return {"plates": [], "objects": [], "warnings": []}
    try:
        root = SafeET.fromstring(declared(raw))  # Orca's <slic3rpe:text> declares no prefix
    except (SafeET.ParseError, DefusedXmlException) as exc:
        return {"plates": [], "objects": [], "warnings": [f"{MODEL_SETTINGS_ENTRY} is not valid XML: {exc}"]}
    from backend.app.farm.workspace.object_settings import model_keys  # imports placement, which this module doesn't
    from backend.app.farm.workspace.settings import IDENTITY

    # a project setting, or one an object can have of its own though the project's file doesn't hold it; never an
    # identity (an object's and its parts' "name" is theirs, not the preset's)
    keys = (set(settings or {}) - IDENTITY) | model_keys()[0]
    objects = []
    for obj in root.findall("object"):
        meta = _meta(obj)
        overrides = [{"key": k, "value": v, "part": None, "part_id": None} for k, v in meta.items() if k in keys]
        parts = []
        for part in obj.findall("part"):
            pmeta = _meta(part)
            parts.append({"id": part.get("id"), "name": pmeta.get("name"), "type": part.get("subtype")})
            overrides += [
                {"key": k, "value": v, "part": pmeta.get("name"), "part_id": part.get("id")}
                for k, v in pmeta.items()
                if k in keys
            ]
        objects.append(
            {
                "id": obj.get("id"),
                "name": meta.get("name"),
                "extruder": meta.get("extruder"),
                # bbs_3mf's <object auto_drop>: Orca puts the object back on the bed after a move (on unless "0")
                "auto_drop": obj.get("auto_drop", "1") != "0",
                "parts": parts,
                "overrides": overrides,
            }
        )
    plates = []
    for plate in root.findall("plate"):
        meta = _meta(plate)
        # (object, instance) pairs: an object placed twice is two instances, kept apart for the 3D editor
        instances = [_meta(inst) for inst in plate.findall("model_instance")]
        instances = [{"object_id": i.get("object_id"), "instance_id": i.get("instance_id")} for i in instances]
        try:
            index = int(meta.get("plater_id", ""))
        except ValueError:
            continue
        plates.append(
            {
                "index": index,
                "name": meta.get("plater_name") or None,
                "object_ids": [i["object_id"] for i in instances if i["object_id"]],
                "instances": [i for i in instances if i["object_id"]],
                # the plate's own settings (bed_type, print_sequence, spiral_mode...), shown read only for now
                "metadata": meta,
            }
        )
    return {"plates": sorted(plates, key=lambda p: p["index"]), "objects": objects, "warnings": []}


def classify(filename: str, zf: zipfile.ZipFile, settings: dict | None) -> tuple[str, str, str | None]:
    """``(kind, slicer, application)`` of the 3MF."""
    meta = _read_header_metadata(zf)
    slicer = detect_slicer(meta)
    if filename.lower().endswith(".gcode.3mf") or any(
        n.startswith("Metadata/plate_") and n.endswith(".gcode") for n in zf.namelist()
    ):
        kind = KIND_SLICED
    elif settings is None or slicer == SLICER_CAD:
        kind = KIND_MODEL
    elif slicer not in SLICEABLE:
        kind = KIND_OTHER_SLICER
    else:
        kind = KIND_PROJECT
    return kind, slicer, meta.get("Application") or None


def write_copy(data: bytes, settings: dict, dest: Path, replaced: dict[str, bytes] | None = None) -> None:
    """Write to ``dest`` the archive with ``project_settings.config`` replaced by ``settings`` and each entry of
    ``replaced`` (an entry the archive has) by its bytes, every other entry unchanged, and check it reads back.
    ``ArchiveError`` for a damaged entry; ``dest`` is then partial."""
    replaced = replaced or {}
    zin = open_archive(data)
    if set(replaced) - set(zin.namelist()) or PROJECT_SETTINGS_ENTRY in replaced:
        raise ArchiveError("the copy can only replace entries the archive has")
    # settings with the same values keep their bytes (Bambu Studio's line breaks, say): a copy that only places
    # instances changes nothing else
    same_settings = read_settings(zin) == settings
    try:
        with zipfile.ZipFile(dest, "w") as zout:
            for info in zin.infolist():
                if info.filename == PROJECT_SETTINGS_ENTRY and not same_settings:
                    zout.writestr(info, json.dumps(settings, indent=4, ensure_ascii=False).encode("utf-8"))
                elif info.filename in replaced:
                    zout.writestr(info, replaced[info.filename])
                elif info.is_dir():
                    zout.writestr(info, b"")
                else:
                    with (
                        zin.open(info) as src,
                        zout.open(info, "w", force_zip64=info.file_size > zipfile.ZIP64_LIMIT) as dst,
                    ):
                        shutil.copyfileobj(src, dst, _COPY_CHUNK)
        _check_copy(zin, dest, settings, replaced)
    except _ENTRY_ERRORS as exc:
        raise ArchiveError(f"an entry of the archive cannot be read: {exc}") from exc


def _check_copy(original: zipfile.ZipFile, copy: Path, settings: dict, replaced: dict[str, bytes]) -> None:
    """The copy reads back: same entries in the same order, same bytes (CRC) but the settings, which parse, and
    the replaced entries, which hold what was written."""
    with open_archive(copy) as zc:
        if zc.testzip() is not None:
            raise ArchiveError("the copy failed its integrity check")
        before, after = original.infolist(), zc.infolist()
        if [i.filename for i in before] != [i.filename for i in after]:
            raise ArchiveError("the copy does not hold the same entries")
        for a, b in zip(before, after, strict=True):
            if a.filename in replaced:
                if zc.read(b) != replaced[a.filename]:
                    raise ArchiveError(f"{a.filename} does not read back in the copy")
            elif a.filename != PROJECT_SETTINGS_ENTRY and (a.CRC, a.file_size) != (b.CRC, b.file_size):
                raise ArchiveError(f"{a.filename} changed in the copy")
        if read_settings(zc) != settings:
            raise ArchiveError("the settings of the copy do not read back")
