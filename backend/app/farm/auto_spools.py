"""Spools for the filaments the production 3MFs really use, created once each (fork).

"Assign spool" lists Inventory's spools only; the filament profiles (``slicer_filaments``) are not spools.
For each confirmed plate this reads which filaments of its project it prints with: the slots of its
objects, and the slots its components' colours move it to (``variants``). Each slot gives material,
colour, brand and profile from the project's settings. A (brand, material, colour) with no spool in
Inventory gets one: 1 kg, nothing used, linked to the project's filament profile when it was imported,
with a note saying where it came from.

Never a duplicate (any spool, archived too, of that material and colour and the same or no brand),
never a spool changed or removed, never one from a catalogue profile no 3MF uses, never one for a colour
the farm's colour table does not name. What was created is remembered: one removed by hand is not made
again. ``farm_auto_spools`` = "0" turns it off. It runs in its own task, when the plates, their files,
the imported profiles or the colour table change.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import zipfile

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.core import database
from backend.app.farm import variants
from backend.app.farm.detect import read_project_settings
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.models.library import LibraryFile
from backend.app.models.local_preset import LocalPreset
from backend.app.models.spool import Spool

logger = logging.getLogger(__name__)

ENABLED_KEY = "farm_auto_spools"
DONE_KEY = "farm_auto_spools_done"  # JSON list of the keys already created (also if removed since)
HASH_KEY = "farm_auto_spools_hash"
NOTE = "creata da Printbuddy dai filamenti usati nei 3MF"
BATCH = 50

_task: asyncio.Task | None = None


def brand_key(brand: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (brand or "").lower())


def same_brand(a: str | None, b: str | None) -> bool:
    """ "Bambu", "Bambu Lab", "BambuLab" are one brand; an unset brand matches any."""
    a, b = brand_key(a), brand_key(b)
    return not a or not b or a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def key_of(brand: str | None, material: str, colour: str) -> str:
    return f"{brand_key(brand)}|{material.upper()}|{colour.lower()}"


def _real(brand: str | None) -> str | None:
    """None for an unset or "Generic" brand: a better one may come from the profile's name."""
    return brand if brand_key(brand) not in ("", "generic") else None


# Materials read from a profile's name, longest first ("PCTG" before "PC", "PETG" before "PET").
_MATERIALS = ("PCTG", "PETG", "PBT", "PLA", "ABS", "ASA", "TPU", "PA12", "PA6", "PA", "PC", "PET", "PP", "PVA", "HIPS")


def material_of(filament_type: str | None, profile: str | None) -> str | None:
    """The material, from the profile's name when it says more than ``filament_type`` (PBT, -CF, -GF).

    Projects often keep a generic type: "CC3D PBT-Pro" with type PETG, "IEMAI PETG-CF" with type PETG.
    """
    name = (profile or "").upper().split("@")[0]
    ftype = (filament_type or "").upper()
    if ftype and re.search(rf"\b{re.escape(ftype)}\b", name):
        more = re.search(rf"\b{re.escape(ftype)}[-\s]?(CF|GF)\b", name)
        return f"{ftype}-{more.group(1)}" if more and not ftype.endswith(("-CF", "-GF")) else ftype
    for mat in _MATERIALS:
        found = re.search(rf"\b{mat}(?:[-\s]?(CF|GF))?\b", name)
        if found:
            return f"{mat}-{found.group(1)}" if found.group(1) else mat
    return ftype or None


def brand_from_name(profile: str | None) -> str | None:
    """The brand the owner wrote before the material in the profile's name ("CC3D PBT-PRO @X2D" -> "CC3D")."""
    name = (profile or "").split("@")[0]
    found = re.search(rf"\b(?:{'|'.join(_MATERIALS)})\b", name.upper())
    brand = name[: found.start()].strip(" -_()") if found else ""
    return brand if len(brand) > 1 else None


def _slot(cfg: dict, name: str, slot: int) -> str | None:
    values = cfg.get(name)
    if isinstance(values, list) and 0 < slot <= len(values) and values[slot - 1] not in (None, ""):
        return str(values[slot - 1]).strip()
    return None


def _colour_name(hex_: str, colours: dict[str, str], tolerance: float) -> str | None:
    """The farm's name for this colour (its colour table, as for the colour variants), or None."""
    from backend.app.farm.plan.service import color_distance

    near = sorted((color_distance(h, hex_), name) for name, h in colours.items() if len(h) == 6)
    return near[0][1] if near and near[0][0] <= tolerance else None


def _has_spool(spools: list[Spool], brand: str | None, material: str, colour: str, ref: str, tolerance: float) -> bool:
    """Inventory has it: same material and brand (or none), and the same colour by name or within tolerance."""
    from backend.app.farm.plan.service import color_distance

    for s in spools:
        if s.material.upper() != material.upper() or not same_brand(s.brand, brand):
            continue
        hex_ = (s.rgba or "")[:6].lower()
        if (s.color_name or "").strip().lower() == colour or (
            re.fullmatch(r"[0-9a-f]{6}", hex_) and color_distance(hex_, ref) <= tolerance
        ):
            return True
    return False


def _read_settings(path) -> dict:
    with open(path, "rb") as fh:
        return read_project_settings(fh)


async def _used_slots(db: AsyncSession, plate: FarmPlate, lib_file: LibraryFile, state: FarmLibraryFile) -> set[int]:
    from backend.app.farm import slicing
    from backend.app.farm.nas.access import library_path
    from backend.app.farm.products.models import FarmComponent

    path = library_path(lib_file.file_path)
    used = {s for s in await asyncio.to_thread(variants.plate_filaments, path, plate.plate_index) if s > 0}
    codes = await db.execute(select(FarmPlateYield.component_code).where(FarmPlateYield.plate_id == plate.id))
    codes = {c for (c,) in codes if c}
    colours = {c for (c,) in await db.execute(select(FarmComponent.color).where(FarmComponent.code.in_(codes))) if c}
    for colour in colours:
        try:
            slot = await slicing._variant_filament(db, lib_file, state, plate.plate_index, colour)
        except HTTPException:
            continue  # the 3MF has no filament of that colour: nothing printed in it from this plate
        if slot:
            used.add(slot)
    return used


async def plan(db: AsyncSession) -> list[dict]:
    """The spools to create now (nothing is written): one per (brand, material, colour) used and missing."""
    from backend.app.farm.nas.access import library_path
    from backend.app.farm.nas.cache import ensure_local
    from backend.app.farm.plan import service as plan_service

    config = await plan_service.load_config(db)
    done = set(json.loads(await get_setting(db, DONE_KEY) or "[]"))
    spools = (await db.execute(select(Spool))).scalars().all()
    presets = {p.name: p for p in (await db.execute(select(LocalPreset))).scalars() if p.preset_type == "filament"}
    rows = await db.execute(
        select(FarmPlate, LibraryFile, FarmLibraryFile)
        .join(LibraryFile, LibraryFile.id == FarmPlate.library_file_id)
        .join(FarmLibraryFile, FarmLibraryFile.library_file_id == FarmPlate.library_file_id)
        .where(FarmPlate.status == "confirmed", FarmLibraryFile.status == "ok")
        .order_by(FarmPlate.id)
    )
    settings_of: dict[int, dict] = {}
    wanted: dict[str, dict] = {}
    for plate, lib_file, state in rows.all():
        try:
            await ensure_local(db, lib_file)
            if lib_file.id not in settings_of:
                settings_of[lib_file.id] = await asyncio.to_thread(_read_settings, library_path(lib_file.file_path))
            slots = await _used_slots(db, plate, lib_file, state)
        except (OSError, ValueError, KeyError, SyntaxError, zipfile.BadZipFile) as exc:  # SyntaxError: bad XML
            logger.warning("Auto spools: %s skipped, unreadable: %s", lib_file.filename, exc)
            continue
        cfg = settings_of[lib_file.id]
        for slot in sorted(slots):
            profile = _slot(cfg, "filament_settings_id", slot)
            material = material_of(_slot(cfg, "filament_type", slot), profile)
            hex_ = (_slot(cfg, "filament_colour", slot) or "").lstrip("#")[:6].lower()
            if not material or not re.fullmatch(r"[0-9a-f]{6}", hex_):
                continue
            colour = _colour_name(hex_, config["colors"], config["tolerance"])
            if colour is None:
                continue  # a colour the farm does not name: no spool with a made-up colour
            preset = presets.get(profile or "")
            vendor = _slot(cfg, "filament_vendor", slot)
            named = brand_from_name(profile)
            known = _real(preset.filament_vendor if preset else None) or _real(vendor)
            # The name the owner gave the profile first; its vendor field when that is the same brand spelt
            # properly ("Bambu" -> "Bambu Lab"), or when the name has none.
            brand = (known if named and known and same_brand(named, known) else named) or known or vendor
            brand = brand.strip() if brand else None
            key = key_of(brand, material, colour)  # by the colour's name: shades of black are one spool
            if (
                key in done
                or key in wanted
                or any(
                    w["material"].upper() == material.upper()
                    and w["color_name"].lower() == colour
                    and same_brand(w["brand"], brand)
                    and brand_key(w["brand"])
                    and brand_key(brand)
                    for w in wanted.values()
                )
            ):
                continue
            ref = config["colors"][colour]
            if _has_spool(spools, brand, material, colour, ref, config["tolerance"]):
                continue  # Inventory has it already
            wanted[key] = {
                "key": key,
                "brand": brand,
                "material": material,
                "color_name": colour.capitalize(),
                "rgba": f"{ref.upper()}FF",
                "slicer_filament": str(preset.id) if preset else None,
                "slicer_filament_name": profile,
                "from": f"{lib_file.filename} plate {plate.plate_index}",
            }
    return list(wanted.values())


async def _fingerprint(db: AsyncSession) -> str:
    """What the spools to create depend on: confirmed plates, their files' versions, profiles, colour table."""
    rows = await db.execute(
        select(FarmPlate.id, FarmLibraryFile.sha256)
        .join(FarmLibraryFile, FarmLibraryFile.library_file_id == FarmPlate.library_file_id)
        .where(FarmPlate.status == "confirmed")
        .order_by(FarmPlate.id)
    )
    parts = [f"{pid}:{sha}" for pid, sha in rows.all()]
    for key in ("farm_slicer_filaments_hash", "farm_plan_colors", "farm_plan_tolerance", DONE_KEY):
        parts.append(f"{key}={await get_setting(db, key)}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


async def sync() -> list[dict] | None:
    """Create the missing spools when what they depend on changed; the spools created, or None."""
    async with database.async_session() as db:
        if (await get_setting(db, ENABLED_KEY) or "1") == "0":
            return None
        if await _fingerprint(db) == await get_setting(db, HASH_KEY):
            return None
        todo = await plan(db)
        done = set(json.loads(await get_setting(db, DONE_KEY) or "[]"))
        for n, item in enumerate(todo, 1):
            fields = {k: v for k, v in item.items() if k not in ("key", "from")}
            db.add(Spool(**fields, label_weight=1000, weight_used=0, added_full=True, note=NOTE))
            done.add(item["key"])
            if n % BATCH == 0:
                await set_setting(db, DONE_KEY, json.dumps(sorted(done)))
                await db.commit()
        await set_setting(db, DONE_KEY, json.dumps(sorted(done)))
        await set_setting(db, HASH_KEY, await _fingerprint(db))  # with the record just written in it
        await db.commit()
    if todo:
        logger.info("Auto spools created: %s", [f"{t['brand']} {t['material']} {t['color_name']}" for t in todo])
    return todo


async def _run() -> None:
    try:
        await sync()
    except Exception:
        logger.exception("Auto spools failed")


def start_check() -> None:
    """Create the missing spools in a task of its own, one at a time."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(), name="farm-auto-spools")


def stop_check() -> None:
    if _task is not None and not _task.done():
        _task.cancel()
