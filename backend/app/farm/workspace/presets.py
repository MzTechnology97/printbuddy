"""Fork (printfarm): the presets the 3MF Workspace's preset headers and its Compare presets dialog offer.

``GET /farm/workspace/presets?section=``: the user presets (Printbuddy's local presets) of a section (process,
filament, printer). ``GET /farm/workspace/presets/values``: one preset's values with its inheritance resolved, a
user preset by id or a system preset by name (OrcaSlicer's ``inherits`` chain, through the cached base profiles
``services/orca_profiles`` already uses for local presets). Nothing here writes: saving a user preset is
``POST /local-presets/``.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.local_preset import LocalPreset
from backend.app.models.user import User
from backend.app.services import orca_profiles

router = APIRouter(prefix="/presets", tags=["farm-workspace"])

Section = Literal["process", "filament", "printer"]


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
    return {
        "section": section,
        "user": [
            {"id": r.id, "name": r.name, "inherits": r.inherits or _setting(r).get("inherits") or None} for r in rows
        ],
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
    """A preset's values with its parents merged in (child keys replace parent keys, as OrcaSlicer)."""
    if source == "user":
        row = await db.get(LocalPreset, id) if id is not None else None
        if row is None or row.preset_type != section:
            raise HTTPException(status_code=404, detail="Preset not found")
        data, label = _setting(row), row.name
    else:
        if not name:
            raise HTTPException(status_code=422, detail="name is required for a system preset")
        data, label = {"inherits": name}, name
    if source == "user" and not data.get("inherits") and row.inherits:
        data = {**data, "inherits": row.inherits}
    # the first parent up the chain that can't be found: the values above it are unknown, not the preset's
    missing, parent, depth = None, data.get("inherits"), 0
    while parent and depth < orca_profiles.MAX_INHERITANCE_DEPTH:
        base = await orca_profiles.fetch_and_cache_base_profile(parent, section, db)
        if base is None:
            missing = parent
            break
        parent, depth = base.get("inherits"), depth + 1
    if source == "system" and missing == name:
        raise HTTPException(status_code=404, detail="System preset not found")
    resolved = await orca_profiles.resolve_preset(dict(data), section, db)
    values = {k: v for k, v in resolved.items() if k not in ("inherits", "name", "from", "type", "instantiation")}
    return {
        "section": section,
        "name": label,
        "inherits": data.get("inherits") or None,
        "missing_parent": missing,
        "values": values,
    }
