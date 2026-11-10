"""Fork (printfarm): the presets the 3MF Workspace's preset headers and its Compare presets dialog offer.

``GET /farm/workspace/presets?section=``: the user presets (Printbuddy's local presets) of a section (process,
filament, printer), and the system presets the base-profile cache holds. ``GET /farm/workspace/presets/values``:
one preset's values with its inheritance resolved, a user preset by id or a cached system preset by name
(OrcaSlicer's ``inherits`` chain).

Everything here reads the base-profile cache that ``services/orca_profiles`` fills when local presets are imported:
no network, and the reads write nothing. A system preset is named only as the cache has it; any other name (a path,
a name not cached) is refused before anything is looked up. Saving a user preset is
``POST /farm/workspace/presets/user``: its name and values are checked as Orca checks them, and it is stored as given
(its parent and the values it changes), in Printbuddy's local presets.

A preset switch (OrcaSlicer's preset combos) resolves from the base-profile cache only, never half: a preset whose
chain isn't all there isn't offered, and what is offered is exactly what the saved copy gets (``switch_target``,
then ``settings.switch_preset``). ``POST /farm/workspace/presets/switch`` shows the switched values before a save.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.workspace import settings as ws_settings
from backend.app.models.local_preset import LocalPreset
from backend.app.models.orca_base_cache import OrcaBaseProfile
from backend.app.models.user import User
from backend.app.services import orca_profiles

router = APIRouter(prefix="/presets", tags=["farm-workspace"])

Section = Literal["process", "filament", "printer"]
# a preset's own bookkeeping, not project settings
META = frozenset(
    {
        "type",
        "name",
        "inherits",
        "from",
        "instantiation",
        "setting_id",
        "filament_id",
        "base_id",
        "description",
        "is_custom_defined",
        "version",
        "renamed_from",
    }
)
CACHE_TYPES = {"process": ("process",), "filament": ("filament",), "printer": ("printer", "machine")}


class PresetRef(BaseModel):
    """A preset to switch a section to: a user preset by id, a system one by name; ``index``: the filament slot."""

    section: Section
    index: int = Field(default=0, ge=0, le=63)
    source: Literal["user", "system"]
    id: int | None = None
    name: str | None = Field(default=None, max_length=300)


class UserPresetSave(BaseModel):
    """A user preset as OrcaSlicer stores one (``inherits`` and the keys that differ, or detached, every value);
    ``id``: the user preset it overwrites."""

    section: Section
    name: str = Field(min_length=1, max_length=150)
    setting: dict[str, str | list[str]] = Field(max_length=2000)
    id: int | None = None


# check_preset_name (SavePresetDialog.cpp): what the dialog refuses, refused here too
ILLEGAL_NAME = re.compile(r'[<>\[\]:/\\|?*"\x00-\x1f\x7f]')


class SwitchRequest(BaseModel):
    settings: dict[str, str | list[str]]
    presets: list[PresetRef] = Field(max_length=66)


def _setting(row: LocalPreset) -> dict:
    data = row.setting
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


@router.get("")
async def list_presets(
    section: Section,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_READ)),
) -> dict:
    """The user presets of a section, by name, with the preset each inherits from."""
    rows = (
        await db.execute(select(LocalPreset).where(LocalPreset.preset_type == section).order_by(LocalPreset.name))
    ).scalars()
    cache = await _cache(db)
    return {
        "section": section,
        "user": [
            {"id": r.id, "name": r.name, "inherits": r.inherits or _setting(r).get("inherits") or None} for r in rows
        ],
        # the names a user preset may not take (Orca: "Overwriting a system profile is not allowed")
        "system": sorted(n for n, (kind, data) in cache.items() if _instantiable(kind, data, section)),
    }


@router.get("/values")
async def preset_values(
    section: Section,
    source: Literal["user", "system"],
    id: int | None = None,
    name: str | None = Query(default=None, max_length=300),
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_READ)),
) -> dict:
    """A preset's values with its cached parents merged in (child keys replace parent keys, as OrcaSlicer)."""
    cache = await _cache(db)
    if source == "user":
        row = await db.get(LocalPreset, id) if id is not None else None
        if row is None or row.preset_type != section:
            raise HTTPException(status_code=404, detail="Preset not found")
        data, label = _setting(row), row.name
        if not data.get("inherits") and row.inherits:
            data = {**data, "inherits": row.inherits}
    else:
        data, label = _system(name, section, cache), name
    # the first parent up the chain that isn't cached: the values above it are unknown, not the preset's
    resolved, missing = _merge(data, cache)
    values = {k: v for k, v in resolved.items() if k not in ("inherits", "name", "from", "type", "instantiation")}
    return {
        "section": section,
        "name": label,
        "inherits": data.get("inherits") or None,
        "missing_parent": missing,
        "values": values,
    }


async def _cache(db: AsyncSession) -> dict[str, tuple[str, dict]]:
    """The cached base profiles, any age: name -> (type, preset)."""
    out = {}
    rows = await db.execute(select(OrcaBaseProfile.name, OrcaBaseProfile.profile_type, OrcaBaseProfile.setting))
    for name, kind, setting in rows.all():
        try:
            data = json.loads(setting)
        except ValueError:
            continue
        if isinstance(data, dict):
            out[name] = (kind, data)
    return out


def _merge(data: dict, cache: dict[str, tuple[str, dict]]) -> tuple[dict, str | None]:
    """``data`` with its ``inherits`` chain merged in (child keys win) as far as the cache has it, and the first
    parent it doesn't have (None: the whole chain)."""
    chain, parent, missing = [data], data.get("inherits"), None
    while parent:
        if parent not in cache or len(chain) > orca_profiles.MAX_INHERITANCE_DEPTH:
            missing = parent
            break
        chain.append(cache[parent][1])
        parent = chain[-1].get("inherits")
    merged: dict = {}
    for d in reversed(chain):
        merged.update(d)
    return merged, missing


def _resolved(data: dict, cache: dict[str, tuple[str, dict]]) -> dict | None:
    """``data`` with its whole ``inherits`` chain merged in, or None when a parent isn't cached."""
    merged, missing = _merge(data, cache)
    return None if missing else merged


def _system(name: str | None, section: str, cache: dict[str, tuple[str, dict]]) -> dict:
    """The cached system preset ``name`` of ``section``: 422 for what can't be a preset's name, 404 for one not
    cached (it is never fetched from here)."""
    if not name:
        raise HTTPException(status_code=422, detail="name is required for a system preset")
    if orca_profiles.UNSAFE_NAME.search(name):
        raise HTTPException(status_code=422, detail="Not a preset name")
    kind, data = cache.get(name, ("", {}))
    if not _instantiable(kind, data, section):
        raise HTTPException(status_code=404, detail="System preset not found")
    return data


def _instantiable(kind: str, data: dict, section: str) -> bool:
    return kind in CACHE_TYPES[section] and str(data.get("instantiation", "true")).lower() == "true"


def _target(section: str, name: str, inherits: str, data: dict, cache: dict, index: int = 0) -> dict | None:
    """What ``settings.switch_preset`` takes, or None when the preset (or its parent) isn't fully known."""
    values = _resolved(data, cache)
    parent = _resolved(cache[inherits][1], cache) if inherits in cache else None
    if values is None or (inherits and parent is None):
        return None
    return {
        "section": section,
        "index": index,
        "name": name,
        "inherits": inherits,  # empty for a system preset: it is its own parent
        "values": {k: v for k, v in values.items() if k not in META},
        "differs": sorted(k for k, v in values.items() if k not in META and parent.get(k) != v) if parent else [],
        "filament_id": values.get("filament_id"),
    }


async def switch_target(db: AsyncSession, ref: PresetRef, cache: dict | None = None) -> dict:
    """The resolved preset ``ref`` names (422 when it can't be fully known, 404 when it doesn't exist)."""
    cache = await _cache(db) if cache is None else cache
    if ref.source == "user":
        row = await db.get(LocalPreset, ref.id) if ref.id is not None else None
        if row is None or row.preset_type != ref.section:
            raise HTTPException(status_code=404, detail="Preset not found")
        data = _setting(row)
        inherits = data.get("inherits") or row.inherits or ""
        target = _target(ref.section, row.name, inherits, {**data, "inherits": inherits}, cache, ref.index)
    else:
        data = _system(ref.name, ref.section, cache)
        target = _target(ref.section, ref.name, "", data, cache, ref.index)
    if target is None:
        raise HTTPException(status_code=422, detail="This preset's inheritance isn't fully known: it can't be applied")
    return target


@router.post("/user")
async def save_user_preset(
    body: UserPresetSave,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_UPDATE)),
) -> dict:
    """Save Save preset's user preset: the name checked as Orca checks it (no system preset's name), every value
    checked as an edit is, the parent a plain name. Stored as given (the parent and the keys that differ), never
    resolved over the network."""
    name = body.name
    cache = await _cache(db)
    if ILLEGAL_NAME.search(name) or name != name.strip() or "(modified)" in name:
        raise HTTPException(status_code=422, detail="Name is invalid")
    if name in cache or name in ("Default Setting", "Default Filament", "Default Printer"):
        raise HTTPException(status_code=422, detail="Overwriting a system profile is not allowed.")
    if errors := ws_settings.preset_errors(body.setting, body.section):
        raise HTTPException(
            status_code=422, detail=f"Some values were refused: {ws_settings.InvalidChange(errors)}"[:1000]
        )
    inherits = body.setting.get("inherits") or ""
    if inherits and orca_profiles.UNSAFE_NAME.search(inherits):
        raise HTTPException(status_code=422, detail="Not a preset name")
    same = await db.scalar(select(LocalPreset).where(LocalPreset.preset_type == body.section, LocalPreset.name == name))
    row = await db.get(LocalPreset, body.id) if body.id is not None else None
    if body.id is not None and (row is None or row.preset_type != body.section):
        raise HTTPException(status_code=404, detail="Preset not found")
    if same is not None and same is not row:
        raise HTTPException(status_code=409, detail=f'Preset "{name}" already exists.')
    core = orca_profiles.extract_core_fields(_merge(dict(body.setting), cache)[0])
    if row is None:
        row = LocalPreset(name=name, preset_type=body.section, source="manual")
        db.add(row)
    row.name, row.setting, row.inherits = name, json.dumps(body.setting), inherits or None
    for key, value in core.items():
        setattr(row, key, value)
    await db.commit()  # saved when the answer says so
    return {"id": row.id, "name": row.name}


@router.get("/offered")
async def offered_presets(
    section: Section,
    printer: str = Query(default="", max_length=300),
    printer_parent: str = Query(default="", max_length=300),
    model: str = Query(default="", max_length=300),
    variant: str = Query(default="", max_length=50),
    nozzle: str = Query(default="", max_length=200),
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_READ)),
) -> dict:
    """The presets a section can switch to on the project's printer (the identity in the query), user then system;
    ``reason`` when one isn't offered: ``unresolved`` (a parent not cached), ``incompatible`` (for other printers),
    ``condition`` (only a compatibility condition, not evaluated), ``other_printer`` (another model or nozzle)."""
    cache = await _cache(db)
    project = {
        "printer_settings_id": printer,
        "inherits_group": ["", printer_parent],
        "printer_model": model or None,  # absent, as in a project or preset without it
        "printer_variant": variant or None,
        "nozzle_diameter": nozzle.split(",") if nozzle else None,
    }

    def entry(source: str, name: str, target: dict | None, **extra) -> dict:
        reason = "unresolved" if target is None else ws_settings.switch_refusal(target, project)
        return {"source": source, "name": name, **extra, "reason": reason}

    rows = (
        await db.execute(select(LocalPreset).where(LocalPreset.preset_type == section).order_by(LocalPreset.name))
    ).scalars()
    out = []
    for row in rows:
        data = _setting(row)
        inherits = data.get("inherits") or row.inherits or ""
        target = _target(section, row.name, inherits, {**data, "inherits": inherits}, cache)
        out.append(entry("user", row.name, target, id=row.id, inherits=inherits or None))
    for name, (kind, data) in sorted(cache.items()):
        if _instantiable(kind, data, section):
            out.append(entry("system", name, _target(section, name, "", data, cache)))
    return {"section": section, "presets": out}


@router.post("/switch")
async def preview_switch(
    body: SwitchRequest,
    db: AsyncSession = Depends(get_db),
    _: User | None = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_READ)),
) -> dict:
    """The project's settings (``settings``, as the workspace shows them) after the switches, in order: the values
    that change. Nothing is saved; the copy does the same with the file's own settings."""
    cache = await _cache(db)
    settings, changes = dict(body.settings), []
    try:
        for ref in body.presets:
            settings, applied = ws_settings.switch_preset(settings, await switch_target(db, ref, cache))
            changes += applied
    except ws_settings.InvalidChange as exc:
        raise HTTPException(status_code=422, detail=f"This preset can't be applied here: {exc}"[:1000]) from exc
    return {"values": {k: v for k, v in settings.items() if body.settings.get(k) != v}, "changes": changes}
