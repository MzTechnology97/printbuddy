"""The colours of the filament brands in use, from the Open Filament Database, in the colour catalog (fork).

The brands come from the filament presets (``local_presets.filament_vendor``: the slicers' filaments
imported every night, and any imported by hand). Each brand the Open Filament Database knows is added
with every colour of every filament it lists (manufacturer, filament as material, colour name, hex), so
searching "IEMAI" in the colour catalog finds them. Colours already there (same manufacturer, colour and
material, any case) are left alone and nothing is ever deleted: entries added or edited by hand stay.

It runs again when the set of brands changes, and once a week for colours added upstream. Brands the
database does not know (e.g. "Retro D") are listed in the result, nothing else happens to them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.core import database
from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.local_preset import LocalPreset
from backend.app.services.open_filament_database import OpenFilamentDatabaseClient, OpenFilamentDatabaseError

logger = logging.getLogger(__name__)

STATE_KEY = "farm_brand_colors"  # {"brands": [slugs], "at": iso, "added": n, ...}: the last run
REFRESH_DAYS = 7
CHECK_EVERY_SECONDS = 3600
FIRST_CHECK_SECONDS = 120  # after start-up, once the rest is up
NOT_BRANDS = {"", "generic", "default", "custom", "unknown", "orcaslicer", "bambustudio", "orca"}
_SUFFIXES = ("filaments", "filament", "tech", "3d", "lab")  # "eSUN 3D" = "Esun", "QIDI Tech" = "Qidi"
_HEX = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _keys(name: str) -> set[str]:
    """The spellings a brand name is matched by: as is, and without a trailing "3D", "Tech", "Lab"..."""
    n = _norm(name)
    keys = {n} if n else set()
    for suffix in _SUFFIXES:
        if n.endswith(suffix) and len(n) > len(suffix) + 1:
            keys.add(n[: -len(suffix)])
    return keys


def match_brands(vendors: set[str], brands: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """``vendor -> Open Filament Database brand`` for the vendors it knows, and the vendors it does not."""
    index: dict[str, dict] = {}
    for brand in brands:
        for label in (brand.get("name") or "", brand.get("slug") or ""):
            for key in _keys(label):
                index.setdefault(key, brand)
    matched: dict[str, dict] = {}
    unmatched: list[str] = []
    for vendor in sorted(vendors):
        if _norm(vendor) in NOT_BRANDS:
            continue
        brand = next((index[k] for k in sorted(_keys(vendor)) if k in index), None)
        if brand is None:
            unmatched.append(vendor)
        else:
            matched[vendor] = brand
    return matched, unmatched


async def brands_in_use(db: AsyncSession) -> set[str]:
    rows = await db.execute(
        select(LocalPreset.filament_vendor)
        .where(LocalPreset.preset_type == "filament", LocalPreset.filament_vendor.is_not(None))
        .distinct()
    )
    return {v.strip() for (v,) in rows if v and v.strip()}


async def brand_colours(client: OpenFilamentDatabaseClient, slug: str) -> list[tuple[str, str, str]]:
    """``(filament, colour name, #HEX)`` for every colour of every filament of the brand."""
    out: list[tuple[str, str, str]] = []
    brand = await client.get_brand(slug)
    for material in brand.get("materials") or []:
        material_name = material.get("material") or material.get("slug")
        if not material_name:
            continue
        listing = await client.get_material(slug, material_name)
        for filament in listing.get("filaments") or []:
            if not filament.get("slug"):
                continue
            detail = await client.get_filament(slug, material_name, filament["slug"])
            label = detail.get("name") or filament.get("name") or material_name
            for variant in detail.get("variants") or []:
                colour, hex_value = (variant.get("name") or "").strip(), variant.get("color_hex") or ""
                m = _HEX.match(hex_value.strip())
                if colour and m:
                    out.append((label, colour, f"#{m.group(1).upper()}"))
    return out


async def _state(db: AsyncSession) -> dict:
    try:
        return json.loads(await get_setting(db, STATE_KEY) or "{}")
    except ValueError:
        return {}


def _fresh(state: dict, slugs: list[str]) -> bool:
    try:
        at = datetime.fromisoformat(state["at"])
    except (KeyError, TypeError, ValueError):
        return False
    return state.get("brands") == slugs and _now() - at < timedelta(days=REFRESH_DAYS)


async def sync(db: AsyncSession, client: OpenFilamentDatabaseClient | None = None, *, force: bool = False) -> dict:
    """Add the colours of the brands in use; skipped when nothing changed since the last run this week."""
    client = client or OpenFilamentDatabaseClient()
    vendors = await brands_in_use(db)
    try:
        catalogue = (await client.get_brands()).get("brands") or []
    except OpenFilamentDatabaseError as exc:
        logger.warning("Brand colours: Open Filament Database not reachable: %s", exc)
        return {"status": "unavailable", "error": str(exc)}
    matched, unmatched = match_brands(vendors, catalogue)
    brands = {b["slug"]: b for b in matched.values() if b.get("slug")}
    slugs = sorted(brands)
    previous = await _state(db)
    if not force and _fresh(previous, slugs):
        return {**previous, "status": "unchanged"}

    rows = await db.execute(
        select(
            func.lower(ColorCatalogEntry.manufacturer),
            func.lower(ColorCatalogEntry.color_name),
            func.lower(func.coalesce(ColorCatalogEntry.material, "")),
        )
    )
    existing = set(rows.all())
    added: dict[str, int] = {}
    failed: list[str] = []
    for slug in slugs:
        name = brands[slug].get("name") or slug
        try:
            colours = await brand_colours(client, slug)
        except OpenFilamentDatabaseError as exc:
            logger.warning("Brand colours: %s not read: %s", name, exc)
            failed.append(name)
            continue
        added[name] = 0
        for material, colour, hex_value in colours:
            key = (name.lower(), colour.lower(), material.lower())
            if key in existing:
                continue
            existing.add(key)
            db.add(
                ColorCatalogEntry(
                    manufacturer=name, color_name=colour, hex_color=hex_value, material=material, is_default=False
                )
            )
            added[name] += 1
    result = {
        # a brand that failed is not recorded as done, so the next check tries it again
        "brands": [s for s in slugs if (brands[s].get("name") or s) not in failed],
        "at": _now().isoformat(),
        "added": sum(added.values()),
        "added_by_brand": added,
        "unmatched": unmatched,
        "failed": failed,
    }
    await set_setting(db, STATE_KEY, json.dumps(result))
    await db.commit()
    logger.info("Brand colours: %s colours added (%s), not in the database: %s", result["added"], added, unmatched)
    return {**result, "status": "done"}


class BrandColourScheduler:
    """Checks the brands in use every hour; ``sync`` does the work only when they changed or weekly."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        await asyncio.sleep(FIRST_CHECK_SECONDS)
        while True:
            try:
                async with database.async_session() as db:
                    await sync(db)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - defensive, keep the loop alive
                logger.exception("Brand colours check failed")
            await asyncio.sleep(CHECK_EVERY_SECONDS)


brand_colour_scheduler = BrandColourScheduler()

router = APIRouter(prefix="/farm/brand-colors", tags=["farm"])


@router.get("", dependencies=[Depends(require_permission_if_auth_enabled(Permission.INVENTORY_READ))])
async def get_brand_colours(db: AsyncSession = Depends(get_db)) -> dict:
    """The last run: brands done, colours added, brands the database does not know."""
    return await _state(db)


@router.post("/sync", dependencies=[Depends(require_permission_if_auth_enabled(Permission.INVENTORY_UPDATE))])
async def sync_brand_colours(db: AsyncSession = Depends(get_db)) -> dict:
    """Run now, even if nothing changed (e.g. after adding a brand by hand)."""
    return await sync(db, force=True)
