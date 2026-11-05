"""The slicers' filaments as local presets, refreshed when the slicer server's copy changes (fork).

Every night the PC's OrcaSlicer and Bambu Studio set-up is copied to the slicer server, which puts in
``<data>/slicer-filaments`` (``export-filaments.sh`` there):

    orca/user/*.json        the Orca filaments of the preset folder Orca uses on the PC
    bambu/user/*.json       the Bambu Studio filaments of the PC
    bambu/system/**.json    Bambu Studio's own catalogue (all its vendors), the version installed
    manifest.json           written last: {"hash": <hash of the files above>}

They become local presets, the list Inventory picks a spool's filament from (and its brand from). New
filaments are added, changed ones updated in place (a spool keeps the same preset id), gone ones removed
unless a spool still uses one. Presets imported or added by hand are never touched, and a name already
there from them is left to them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.core.config import settings
from backend.app.models.local_preset import LocalPreset
from backend.app.models.spool import Spool
from backend.app.services import orca_profiles

logger = logging.getLogger(__name__)

# folder in the export -> source shown for the preset; earlier folders win a name found in two
SOURCES = {"orca/user": "OrcaSlicer PC", "bambu/user": "Bambu Studio PC", "bambu/system": "Bambu Studio"}
HASH_KEY = "farm_slicer_filaments_hash"
MAX_DEPTH = 10


def export_dir() -> Path:
    return Path(settings.base_dir) / "slicer-filaments"


def _load(folder: Path) -> dict[str, dict]:
    presets: dict[str, dict] = {}
    for path in sorted(folder.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("name"), str) and data["name"]:
            presets[data["name"]] = data
    return presets


def _resolve(data: dict, bases: dict[str, dict], depth: int = 0) -> dict:
    """Shallow merge with the inherited presets, as the slicers do (the child's keys win)."""
    parent = bases.get(data.get("inherits") or "")
    if parent is None or depth >= MAX_DEPTH:
        return data
    return {**_resolve(parent, bases, depth + 1), **data}


async def _resolved(data: dict, bases: dict[str, dict], db: AsyncSession) -> dict:
    resolved = _resolve(data, bases)
    if resolved is data and data.get("inherits"):
        # An Orca base ("... @System") is not in Bambu Studio's catalogue: upstream's resolver looks it up.
        try:
            resolved = await orca_profiles.resolve_preset(data, "filament", db)
        except Exception as exc:
            logger.debug("Filament %s: base %s not resolved: %s", data.get("name"), data.get("inherits"), exc)
    return resolved


def _fields(name: str, data: dict, resolved: dict) -> dict:
    core = orca_profiles.extract_core_fields(resolved)
    return {
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


def _wanted(root: Path) -> dict[tuple[str, str], dict]:
    wanted: dict[tuple[str, str], dict] = {}
    seen: set[str] = set()
    for folder, source in SOURCES.items():
        for name, data in _load(root / folder).items():
            if name in seen:
                continue
            if folder.endswith("system") and str(data.get("instantiation", "true")).lower() == "false":
                continue  # a base the catalogue builds on, not a filament one can pick
            seen.add(name)
            wanted[(source, name)] = data
    return wanted


async def sync(db: AsyncSession, root: Path | None = None) -> dict | None:
    """Import the exported filaments when they changed; the counts, or None when there was nothing new."""
    root = root or export_dir()
    try:
        stamp = str(json.loads((root / "manifest.json").read_text(encoding="utf-8"))["hash"])
    except (OSError, ValueError, KeyError, TypeError):
        return None  # nothing exported (yet), or an export being written
    if stamp == await get_setting(db, HASH_KEY):
        return None

    bases: dict[str, dict] = {}
    for folder in ("bambu/system", "bambu/user", "orca/user"):
        bases.update(_load(root / folder))
    wanted = _wanted(root)
    synced = set(SOURCES.values())
    rows = (await db.execute(select(LocalPreset))).scalars().all()
    others = {r.name for r in rows if r.source not in synced}
    existing = {(r.source, r.name): r for r in rows if r.source in synced}

    added = updated = removed = 0
    for (source, name), data in wanted.items():
        if name in others:
            continue  # already there, imported or added by hand: left as it is
        fields = _fields(name, data, await _resolved(data, bases, db))
        row = existing.get((source, name))
        if row is None:
            db.add(LocalPreset(name=name, preset_type="filament", source=source, **fields))
            added += 1
        elif row.setting != fields["setting"]:
            for key, value in fields.items():
                setattr(row, key, value)
            updated += 1

    used = {s for (s,) in await db.execute(select(Spool.slicer_filament)) if s}
    for key, row in existing.items():
        if key not in wanted and str(row.id) not in used:
            await db.delete(row)
            removed += 1

    await set_setting(db, HASH_KEY, stamp)
    await db.commit()
    counts = {"added": added, "updated": updated, "removed": removed, "total": len(wanted)}
    logger.info("Slicer filaments imported: %s", counts)
    return counts
