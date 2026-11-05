"""The slicers' filaments as local presets, refreshed when the slicer server's copy changes (fork).

Every night the PC's OrcaSlicer and Bambu Studio set-up is copied to the slicer server, which puts in
``<data>/slicer-filaments`` (``export-filaments.sh`` there):

    orca/user/*.json        the Orca filaments of the preset folder Orca uses on the PC
    bambu/user/*.json       the Bambu Studio filaments of the PC
    bambu/system/**.json    Bambu Studio's own catalogue (all its vendors), the version installed
    manifest.json           written last: {"hash": ..., "counts": {<folder>: <number of .json files>}}

They become local presets, the list Inventory picks a spool's filament from (and its brand from). New
filaments are added, changed ones updated in place (spools and AMS slots keep the same preset id, also
when a filament moves from one slicer to the other), gone ones removed unless a spool or a slot still
uses one. Presets imported or added by hand are never touched, and a name already there from them is
left to them.

An export that does not match its manifest (a copy cut short, an unreadable file) changes nothing and
is tried again at the next check. One that would remove more than half of the imported filaments
removes none, with a warning. The import runs in its own task: the farm dispatcher never waits for it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from time import monotonic

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.core import database
from backend.app.core.config import settings
from backend.app.models.local_preset import LocalPreset
from backend.app.models.slot_preset import SlotPresetMapping
from backend.app.models.spool import Spool
from backend.app.services import orca_profiles

logger = logging.getLogger(__name__)

# folder in the export -> source shown for the preset; earlier folders win a name found in two
SOURCES = {"orca/user": "OrcaSlicer PC", "bambu/user": "Bambu Studio PC", "bambu/system": "Bambu Studio"}
HASH_KEY = "farm_slicer_filaments_hash"
MAX_DEPTH = 10
MAX_FILE_BYTES = 2_000_000
GITHUB_LOOKUPS = 30  # distinct Orca bases looked up on GitHub in one import
SLOW_MISS_SECONDS = 5.0  # a miss this slow means GitHub is not answering: no more lookups this time
BATCH = 200  # rows written per commit

_task: asyncio.Task | None = None


class ExportError(Exception):
    """The export does not match its manifest: nothing is changed, it is read again later."""


def export_dir() -> Path:
    return Path(settings.base_dir) / "slicer-filaments"


def _manifest(root: Path) -> tuple[str, dict]:
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        return str(manifest["hash"]), {k: int(v) for k, v in manifest["counts"].items()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise ExportError(f"manifest: {exc}") from exc


def _load(folder: Path) -> tuple[dict[str, dict], int]:
    """The presets of a folder by name, and how many .json files it holds."""
    presets: dict[str, dict] = {}
    if not folder.is_dir():
        return presets, 0
    top = folder.resolve()
    files = 0
    for path in sorted(folder.rglob("*.json")):
        files += 1
        if path.is_symlink() or not path.resolve().is_relative_to(top) or path.stat().st_size > MAX_FILE_BYTES:
            raise ExportError(f"{path.name}: not a plain file of the export")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ExportError(f"{path.name}: {exc}") from exc
        if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"]:
            presets[data["name"]] = data
    return presets, files


def _read(root: Path, counts: dict) -> dict[str, dict[str, dict]]:
    """Blocking (run in a thread): each folder's presets, checked against the manifest's counts."""
    loaded = {}
    for folder in SOURCES:
        presets, files = _load(root / folder)
        if files != counts.get(folder):
            raise ExportError(f"{folder}: {files} files, the manifest says {counts.get(folder)}")
        loaded[folder] = presets
    return loaded


def _wanted(loaded: dict[str, dict[str, dict]]) -> dict[str, tuple[str, dict]]:
    wanted: dict[str, tuple[str, dict]] = {}
    for folder in SOURCES:
        for name, data in loaded[folder].items():
            if name in wanted:
                continue
            if folder == "bambu/system" and str(data.get("instantiation", "true")).lower() == "false":
                continue  # a base the catalogue builds on, not a filament one can pick
            wanted[name] = (folder, data)
    return wanted


def _resolve(data: dict, bases: dict[str, dict], depth: int = 0) -> tuple[dict, str | None]:
    """Shallow merge with the inherited presets, as the slicers do (the child's keys win).

    Also the first base not found, so that it can be looked up elsewhere.
    """
    inherits = data.get("inherits") or None
    if not inherits or depth >= MAX_DEPTH:
        return data, None
    parent = bases.get(inherits)
    if parent is None:
        return data, inherits
    merged, missing = _resolve(parent, bases, depth + 1)
    return {**merged, **data}, missing


async def _github_base(name: str, db: AsyncSession) -> dict | None:
    """An Orca base ("... @System") from upstream's resolver (GitHub, cached in the DB)."""
    try:
        base = await orca_profiles.resolve_preset({"inherits": name}, "filament", db)
        await db.commit()  # its cache: nothing left pending while the next lookup waits on the network
    except Exception as exc:
        await db.rollback()
        logger.debug("Filament base %s not found: %s", name, exc)
        return None
    return base if len(base) > 1 else None


async def _resolve_all(wanted: dict[str, tuple[str, dict]], loaded: dict) -> dict[str, dict]:
    """Every filament merged with its bases; all network lookups happen here, before any write."""
    system = loaded["bambu/system"]
    # a slicer's own presets never cover the other's bases of the same name
    bases = {"orca": {**system, **loaded["orca/user"]}, "bambu": {**system, **loaded["bambu/user"]}}
    looked_up: dict[str, dict | None] = {}
    lookups = GITHUB_LOOKUPS
    resolved: dict[str, dict] = {}
    async with database.async_session() as db:
        for name, (folder, data) in wanted.items():
            merged, missing = _resolve(data, bases[folder.split("/")[0]])
            if missing:
                if missing not in looked_up:
                    base = None
                    if lookups > 0:
                        lookups -= 1
                        started = monotonic()
                        base = await _github_base(missing, db)
                        if base is None and monotonic() - started > SLOW_MISS_SECONDS:
                            lookups = 0  # GitHub is not answering: the rest keep their own fields
                    looked_up[missing] = base
                if looked_up[missing]:
                    merged = {**looked_up[missing], **merged}
            resolved[name] = merged
    return resolved


def _fields(name: str, folder: str, data: dict, resolved: dict) -> dict:
    core = orca_profiles.extract_core_fields(resolved)
    return {
        "source": SOURCES[folder],
        "filament_type": core.get("filament_type") or orca_profiles._parse_material_from_name(name),
        "filament_vendor": core.get("filament_vendor") or orca_profiles._parse_vendor_from_name(name),
        "nozzle_temp_min": core.get("nozzle_temp_min"),
        "nozzle_temp_max": core.get("nozzle_temp_max"),
        "pressure_advance": core.get("pressure_advance"),
        "default_filament_colour": core.get("default_filament_colour"),
        "filament_cost": core.get("filament_cost"),
        "filament_density": core.get("filament_density"),
        "compatible_printers": core.get("compatible_printers"),
        "setting": json.dumps(resolved, separators=(",", ":"), sort_keys=True),
        "inherits": data.get("inherits"),
        "version": data.get("version"),
    }


async def _in_use(db: AsyncSession) -> set[str]:
    """Local preset ids a spool or an AMS slot points at."""
    spools = {s for (s,) in await db.execute(select(Spool.slicer_filament)) if s}
    slots = await db.execute(select(SlotPresetMapping.preset_id).where(SlotPresetMapping.preset_source == "local"))
    return spools | {p for (p,) in slots if p}


async def _write(db: AsyncSession, wanted: dict, resolved: dict, stamp: str) -> dict:
    synced = set(SOURCES.values())
    rows = (await db.execute(select(LocalPreset))).scalars().all()
    others = {r.name for r in rows if r.source not in synced}
    existing: dict[str, LocalPreset] = {}
    doubles: list[LocalPreset] = []
    for r in rows:
        if r.source not in synced:
            continue
        if r.name in existing:
            doubles.append(r)
        else:
            existing[r.name] = r

    added = updated = removed = 0
    for name, (folder, data) in wanted.items():
        if name in others:
            continue  # already there, imported or added by hand: left as it is
        fields = _fields(name, folder, data, resolved[name])
        row = existing.get(name)  # by name: a filament moved to the other slicer keeps its id
        if row is None:
            db.add(LocalPreset(name=name, preset_type="filament", **fields))
            added += 1
        elif any(getattr(row, k) != v for k, v in fields.items()):
            for key, value in fields.items():
                setattr(row, key, value)
            updated += 1
        else:
            continue
        if (added + updated) % BATCH == 0:
            await db.commit()

    gone = [r for n, r in existing.items() if n not in wanted] + doubles
    if gone and len(wanted) < len(existing) / 2:
        logger.warning(
            "Slicer filaments: the export has %d filaments for %d imported, none removed", len(wanted), len(existing)
        )
    elif gone:
        used = await _in_use(db)
        for row in gone:
            if str(row.id) not in used:
                await db.delete(row)
                removed += 1

    await set_setting(db, HASH_KEY, stamp)  # last: an import cut short is done again next time
    await db.commit()
    return {"added": added, "updated": updated, "removed": removed, "total": len(wanted)}


async def sync(root: Path | None = None) -> dict | None:
    """Import the exported filaments when they changed; the counts, or None when there was nothing to do."""
    root = root or export_dir()
    if not (root / "manifest.json").exists():
        return None  # nothing exported (yet)
    try:
        stamp, counts = await asyncio.to_thread(_manifest, root)
        async with database.async_session() as db:
            if stamp == await get_setting(db, HASH_KEY):
                return None
        loaded = await asyncio.to_thread(_read, root, counts)
    except ExportError as exc:
        logger.warning("Slicer filaments not imported, the export is incomplete: %s", exc)
        return None
    wanted = _wanted(loaded)
    resolved = await _resolve_all(wanted, loaded)
    async with database.async_session() as db:
        counts = await _write(db, wanted, resolved, stamp)
    logger.info("Slicer filaments imported: %s", counts)
    return counts


async def _run() -> None:
    try:
        await sync()
    except Exception:
        logger.exception("Slicer filaments import failed")


def start_check() -> None:
    """Look for a new export in a task of its own, one at a time."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(), name="farm-slicer-filaments")


def stop_check() -> None:
    if _task is not None and not _task.done():
        _task.cancel()
