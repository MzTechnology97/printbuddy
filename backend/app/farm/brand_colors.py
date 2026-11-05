"""The colours of the filament lines in use, from the Open Filament Database, in the colour catalog (fork).

The brands and lines come from the user's filament presets (``local_presets``: the PC slicers' filaments
imported every night, and any imported by hand; not the slicer's own catalogue). For each brand the Open Filament Database knows, only the lines the
presets use are taken: a line comes in when its words (besides the material) are all in a preset's name,
so "Marble PLA" comes with "Sunlu-Marble PLA" and "PA6-CF" with "Sunlu PA6-CF"; the plain line of a material
in use ("PLA") comes too. Each colour is added with manufacturer, line as material, colour name and hex, at
most ``MAX_PER_BRAND`` per brand. All of a brand's lines would be far too many (SUNLU alone: over 500).

Never duplicated, never deleted: a colour already there (same brand, colour and line, compared without case,
punctuation or the brand's spelling: "eSUN" = "eSUN 3D") is left alone, and one this sync added and the user
deleted is not added again. It runs when the brands change, once a week for new colours, and retries a brand
that could not be read after a growing wait; a single line that can't be read is skipped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.core import database
from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.slicer_filaments import SOURCES as SLICER_SOURCES
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.local_preset import LocalPreset
from backend.app.services.open_filament_database import (
    OpenFilamentDatabaseClient,
    OpenFilamentDatabaseError,
    OpenFilamentDatabaseUnavailable,
)

logger = logging.getLogger(__name__)

STATE_KEY = "farm_brand_colors"  # the last run: brands done, "at" (last full run), retries, counts
ADDED_KEY = "farm_brand_colors_added"  # every colour this sync ever added: deleted by hand, never again
REFRESH_DAYS = 7
MAX_PER_BRAND = 150
RETRY_FIRST_SECONDS = 3600  # a brand that could not be read: 1 h, then doubling up to a day
RETRY_MAX_SECONDS = 24 * 3600
CONCURRENCY = 2  # parallel requests to the database during a brand (more trips its rate limit)
CHECK_EVERY_SECONDS = 3600
FIRST_CHECK_SECONDS = 120  # after start-up, once the rest is up
NOT_BRANDS = {"", "generic", "default", "custom", "unknown", "orcaslicer", "bambustudio", "orca"}
_SUFFIXES = ("filaments", "filament", "tech", "3d", "lab")  # "eSUN 3D" = "Esun", "QIDI Tech" = "Qidi"
_HEX = re.compile(r"^#?([0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
_READ_ERRORS = (OpenFilamentDatabaseError, ValueError)  # ValueError: a name the client refuses as a path

# The slicer's own catalogue (thousands of profiles of every vendor it ships) says nothing about what is in
# use: only the user's filaments count (from the PC's slicers, or imported by hand).
SYSTEM_SOURCE = SLICER_SOURCES["bambu/system"]

_lock = asyncio.Lock()  # one run at a time: the hourly check and a run by hand never insert twice
_background: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _keys(name: str) -> set[str]:
    """The spellings a brand name is matched by: as is, and without a trailing "3D", "Tech", "Lab"..."""
    n = _norm(name)
    keys = {n} if n else set()
    for suffix in _SUFFIXES:
        if n.endswith(suffix) and len(n) > len(suffix) + 1:
            keys.add(n[: -len(suffix)])
    return keys


def _brand_index(brands: list[dict]) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for brand in brands:
        for label in (brand.get("name") or "", brand.get("slug") or ""):
            for key in _keys(label):
                index.setdefault(key, brand)
    return index


def _brand_of(name: str, index: dict[str, dict]) -> dict | None:
    return next((index[k] for k in sorted(_keys(name)) if k in index), None)


def match_brands(vendors: set[str], brands: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """``vendor -> Open Filament Database brand`` for the vendors it knows, and the vendors it does not."""
    index = _brand_index(brands)
    matched: dict[str, dict] = {}
    unmatched: list[str] = []
    for vendor in sorted(vendors):
        if _norm(vendor) in NOT_BRANDS:
            continue
        brand = _brand_of(vendor, index)
        if brand is None:
            unmatched.append(vendor)
        else:
            matched[vendor] = brand
    return matched, unmatched


def _words(text: str | None) -> set[str]:
    """Lower-case words, "+" kept ("PLA+" is not "PLA"): "Sunlu-Marble PLA" -> {sunlu, marble, pla}."""
    return set(re.findall(r"[a-z0-9]+\+?", (text or "").lower()))


def _family(word: str) -> str:
    return re.sub(r"\d+$", "", _norm(word))  # "PA6" and "PA" are one family, "PCTG" and "PC" are not


def line_in_use(material: str, line: str, presets: list[set[str]]) -> bool:
    """A line of this material whose words (besides the material) are all in one preset of the brand."""
    family = _family(material)
    extra = _words(line) - {material.lower(), _norm(material)}
    return any(extra <= words and any(_family(w) == family for w in words) for words in presets)


async def presets_by_vendor(db: AsyncSession) -> dict[str, list[set[str]]]:
    rows = await db.execute(
        select(LocalPreset.filament_vendor, LocalPreset.name, LocalPreset.filament_type).where(
            LocalPreset.preset_type == "filament",
            LocalPreset.filament_vendor.is_not(None),
            LocalPreset.source != SYSTEM_SOURCE,
        )
    )
    out: dict[str, list[set[str]]] = {}
    for vendor, name, ftype in rows:
        if vendor and vendor.strip():
            out.setdefault(vendor.strip(), []).append(_words(name.split("@")[0]) | _words(ftype))
    return out


async def brand_colours(
    client: OpenFilamentDatabaseClient, slug: str, presets: list[set[str]]
) -> tuple[list[tuple[str, str, str]], int, int]:
    """``(line, colour name, #HEX)`` of the brand's lines in use, then how many materials or lines were
    skipped for good (not found, a refused name) and how many only for now (timeout, server error).

    The brand itself not being readable raises.
    """
    brand = await client.get_brand(slug)
    gate = asyncio.Semaphore(CONCURRENCY)
    skipped = transient = 0

    async def read(call, *args):
        nonlocal skipped, transient
        async with gate:
            try:
                return await call(*args)
            except OpenFilamentDatabaseUnavailable as exc:
                transient += 1
                logger.debug("Brand colours: %s %s not read now: %s", slug, args, exc)
            except _READ_ERRORS as exc:
                skipped += 1
                logger.debug("Brand colours: %s %s skipped: %s", slug, args, exc)
            return None

    families = {_family(w) for words in presets for w in words}
    materials = [
        m.get("material") or m.get("slug")
        for m in brand.get("materials") or []
        if _family(m.get("material") or m.get("slug") or "") in families
    ]
    listings = await asyncio.gather(*(read(client.get_material, slug, m) for m in materials))
    wanted = [
        (material, f["slug"])
        for material, listing in zip(materials, listings, strict=True)
        if listing
        for f in listing.get("filaments") or []
        if f.get("slug") and line_in_use(material, f.get("name") or f["slug"], presets)
    ]
    details = await asyncio.gather(*(read(client.get_filament, slug, m, f) for m, f in wanted))
    out: list[tuple[str, str, str]] = []
    for (material, _), detail in zip(wanted, details, strict=True):
        if not detail:
            continue
        line = detail.get("name") or material
        for variant in detail.get("variants") or []:
            colour = (variant.get("name") or "").strip()
            m = _HEX.match((variant.get("color_hex") or "").strip())
            if colour and m:
                out.append((line, colour, f"#{m.group(1).upper()}"))
    return out, skipped, transient


async def _read_json(db: AsyncSession, key: str, default):
    try:
        value = json.loads(await get_setting(db, key) or "null")
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _when(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _todo(previous: dict, slugs: list[str], force: bool) -> tuple[list[str], bool]:
    """The brands to read now, and whether it is a full run (brands changed, a week passed, by hand)."""
    retry = previous.get("retry") or {}
    known = sorted(set(previous.get("brands") or []) | set(retry))
    last = _when(previous.get("at"))
    if force or known != slugs or last is None or _now() - last >= timedelta(days=REFRESH_DAYS):
        return slugs, True
    due = [s for s, r in retry.items() if s in slugs and (_when(r.get("next_at")) or _now()) <= _now()]
    return sorted(due), False


async def sync(db: AsyncSession, client: OpenFilamentDatabaseClient | None = None, *, force: bool = False) -> dict:
    async with _lock:
        return await _sync(db, client or OpenFilamentDatabaseClient(), force)


async def _sync(db: AsyncSession, client: OpenFilamentDatabaseClient, force: bool) -> dict:
    by_vendor = await presets_by_vendor(db)
    try:
        catalogue = (await client.get_brands()).get("brands") or []
    except _READ_ERRORS as exc:
        logger.warning("Brand colours: Open Filament Database not reachable: %s", exc)
        return {"status": "unavailable", "error": str(exc)}
    index = _brand_index(catalogue)
    matched, unmatched = match_brands(set(by_vendor), catalogue)
    brands: dict[str, dict] = {}
    presets: dict[str, list[set[str]]] = {}
    for vendor, brand in matched.items():
        if brand.get("slug"):
            brands[brand["slug"]] = brand
            presets.setdefault(brand["slug"], []).extend(by_vendor[vendor])
    slugs = sorted(brands)
    previous = await _read_json(db, STATE_KEY, {})
    todo, full = _todo(previous, slugs, force)
    if not todo:
        return {**previous, "status": "unchanged"}

    read: dict[str, list[tuple[str, str, str]]] = {}
    retry = {s: r for s, r in (previous.get("retry") or {}).items() if s in slugs}
    skipped: dict[str, int] = {}

    def later(slug: str, why) -> None:
        fails = int((retry.get(slug) or {}).get("fails", 0)) + 1
        wait = min(RETRY_MAX_SECONDS, RETRY_FIRST_SECONDS * 2 ** (fails - 1))
        retry[slug] = {"fails": fails, "next_at": (_now() + timedelta(seconds=wait)).isoformat()}
        logger.warning("Brand colours: %s not fully read (try %s, again in %s s): %s", slug, fails, wait, why)

    for slug in todo:
        try:
            read[slug], skipped[slug], transient = await brand_colours(client, slug, presets[slug])
        except _READ_ERRORS as exc:
            later(slug, exc)
            continue
        if transient:  # what was read is added now; the rest is asked again after the wait
            later(slug, f"{transient} materials or lines not answered")
        else:
            retry.pop(slug, None)

    # Read just before inserting (the requests above take a while), keys without case, punctuation or the
    # brand's spelling: what is there, and what this sync once added (deleted by hand: not again).
    def key(manufacturer: str, colour: str, line: str | None) -> str:
        brand = _brand_of(manufacturer, index)
        return "|".join((brand["slug"] if brand else _norm(manufacturer), _norm(colour), _norm(line)))

    rows = await db.execute(
        select(ColorCatalogEntry.manufacturer, ColorCatalogEntry.color_name, ColorCatalogEntry.material)
    )
    there = {key(*row) for row in rows}
    ever = set(await _read_json(db, ADDED_KEY, []))
    added: dict[str, int] = {}
    capped: list[str] = []
    for slug, colours in read.items():
        name = brands[slug].get("name") or slug
        added[name] = 0
        for line, colour, hex_value in colours:
            k = key(name, colour, line)
            if k in there or k in ever:
                continue
            if added[name] >= MAX_PER_BRAND:
                capped.append(name)
                break
            there.add(k)
            ever.add(k)
            db.add(ColorCatalogEntry(manufacturer=name, color_name=colour, hex_color=hex_value, material=line))
            added[name] += 1
    done = set(previous.get("brands") or []) if not full else set()
    result = {
        "brands": sorted(((done & set(slugs)) | set(read)) - set(retry)),
        "at": _now().isoformat() if full else previous.get("at"),
        "added": sum(added.values()),
        "added_by_brand": added,
        "skipped_lines": {brands[s].get("name") or s: n for s, n in skipped.items() if n},
        "capped": capped,
        "unmatched": unmatched,
        "retry": retry,
    }
    await set_setting(db, STATE_KEY, json.dumps(result))
    await set_setting(db, ADDED_KEY, json.dumps(sorted(ever)))
    await db.commit()
    logger.info("Brand colours: %s added %s, not in the database: %s", result["added"], added, unmatched)
    return {**result, "status": "done"}


async def _run_once(force: bool = False) -> None:
    try:
        async with database.async_session() as db:
            await sync(db, force=force)
    except Exception:  # pragma: no cover - defensive, a background run never takes anything down
        logger.exception("Brand colours run failed")


class BrandColourScheduler:
    """Checks every hour; ``sync`` reads the database only when the brands changed, weekly, or to retry."""

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
            await _run_once()
            await asyncio.sleep(CHECK_EVERY_SECONDS)


brand_colour_scheduler = BrandColourScheduler()

router = APIRouter(prefix="/farm/brand-colors", tags=["farm"])


@router.get("", dependencies=[Depends(require_permission_if_auth_enabled(Permission.INVENTORY_READ))])
async def get_brand_colours(db: AsyncSession = Depends(get_db)) -> dict:
    """The last run (brands done, colours added, lines skipped, retries, unknown brands) and whether one runs."""
    running = _lock.locked() or (_background is not None and not _background.done())
    return {**await _read_json(db, STATE_KEY, {}), "running": running}


@router.post(
    "/sync",
    status_code=202,
    dependencies=[Depends(require_permission_if_auth_enabled(Permission.INVENTORY_UPDATE))],
)
async def sync_brand_colours():
    """Start a full run in the background (it reads the database for a while); follow it with GET."""
    global _background
    if _lock.locked() or (_background is not None and not _background.done()):
        return JSONResponse({"status": "running"}, status_code=202)
    _background = asyncio.create_task(_run_once(force=True))
    return JSONResponse({"status": "started"}, status_code=202)
