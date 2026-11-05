"""Library problems: a 3MF that will not work is told when it is saved, not when its job waits (fork).

Two sources, both by file and plate:

* checks without slicing, on every new version (sha) of a farm 3MF, run by a sweep in its own task:
  a plate without objects, a printer model no farm printer has, a filament profile not among the
  imported ones (``slicer_filaments``), a brand spelt unlike most profiles spell it or close to a more
  common brand (with the likely name), a filament slot without a colour;
* the automatic re-slice of a changed project (``reslice``): each plate's outcome, a failure with
  its reason explained (``slicing.explain``).

A problem goes away by itself when the file is fixed and checked again (or re-sliced). A file with a
new problem is notified once (``farm_library_problem``), not once per plate.
"""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core import database
from backend.app.core.database import Base
from backend.app.farm import notify
from backend.app.farm.detect import PROJECT_SETTINGS_ENTRY
from backend.app.farm.models import FarmLibraryFile
from backend.app.models.library import LibraryFile

logger = logging.getLogger(__name__)

SLICE = "slice_failed"
SIMILAR = 0.8  # difflib ratio of two brand keys that are probably the same brand misspelt
SUMMARY_AFTER = 5  # more files than this with new problems in one round: one summary message
_task: asyncio.Task | None = None


class FarmLibraryProblem(Base):
    __tablename__ = "farm_library_problems"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_file_id: Mapped[int] = mapped_column(Integer, index=True)
    plate: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None: the whole file
    code: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(String(500))
    since: Mapped[datetime] = mapped_column(DateTime)


class FarmLibraryCheck(Base):
    """The version of each 3MF the checks without slicing last ran on."""

    __tablename__ = "farm_library_checks"

    library_file_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # What the checks depended on (imported profiles, printer models): checked again when it changes,
    # so importing the profile or mapping the model clears the problem by itself.
    env: Mapped[str | None] = mapped_column(String(64), nullable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- checks without slicing


def read_project(path) -> tuple[dict, dict[int, int], dict[int, set[int]]]:
    """Blocking: project settings, objects per plate, filament slots per plate (1-based)."""
    from backend.app.farm import variants

    with zipfile.ZipFile(path) as zf:
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY))
        except (KeyError, ValueError):
            cfg = {}
        try:
            root = ET.fromstring(zf.read(variants.MODEL_SETTINGS_ENTRY))
        except KeyError:
            return cfg, {}, {}
    plates = [
        int(m.get("value"))
        for p in root.findall("plate")
        for m in p.findall("metadata")
        if m.get("key") == "plater_id" and (m.get("value") or "").isdigit()
    ]
    objects = {n: len(variants._plate_objects(root, n)) for n in plates}
    slots = {n: {s for s in variants.plate_filaments(path, n) if s > 0} for n in plates if objects[n]}
    return cfg, objects, slots


def _slot(cfg: dict, name: str, slot: int) -> str:
    values = cfg.get(name)
    if isinstance(values, list) and 0 < slot <= len(values):
        return str(values[slot - 1] or "").strip()
    return ""


def known_brands(names: list[str], vendors: list[str]) -> dict[str, tuple[str, int]]:
    """brand key -> (the spelling most use, how many profiles use it), from profile names and vendor fields."""
    from backend.app.farm.auto_spools import brand_from_name, brand_key

    seen: dict[str, Counter] = {}
    for brand in [brand_from_name(n) for n in names] + vendors:
        if brand and brand_key(brand) not in ("", "generic"):
            seen.setdefault(brand_key(brand), Counter())[brand.strip()] += 1
    out = {}
    for key, counts in seen.items():
        top = counts.most_common(2)
        tie = len(top) == 2 and top[0][1] == top[1][1]
        out[key] = (None if tie else top[0][0], sum(counts.values()))  # a tie: no usual spelling to suggest
    return out


def brand_problem(profile: str, brands: dict[str, tuple[str, int]]) -> str | None:
    """A brand written unlike most (Sunlu/SUNLU), or close to a more used one (Quidi/Qidi)."""
    from backend.app.farm.auto_spools import brand_from_name, brand_key

    brand = brand_from_name(profile)
    if not brand:
        return None
    key = brand_key(brand)
    usual, count = brands.get(key, (brand, 0))
    usual = usual or brand
    near = [
        (other_count, other)
        for other_key, (other, other_count) in brands.items()
        if other_key != key and other_count > count and difflib.SequenceMatcher(None, key, other_key).ratio() >= SIMILAR
    ]
    if near:
        return f"brand '{brand}' in '{profile}': did you mean '{max(near)[1]}'?"
    if usual.lower() != brand.lower():  # capitals alone are not a problem
        return f"brand '{brand}' in '{profile}' is usually written '{usual}'"
    return None


def static_problems(
    cfg: dict,
    objects: dict[int, int],
    slots: dict[int, set[int]],
    *,
    model: str | None,
    model_ok: bool,
    presets: set[str],
    brands: dict[str, tuple[str, int]],
) -> list[tuple[int | None, str, str]]:
    """``(plate, code, message)`` of a project, from what it holds (no slicing)."""
    found: list[tuple[int | None, str, str]] = []
    if model and not model_ok:
        found.append((None, "printer_model", f"printer model {model} has no printer in the farm"))
    for plate, count in sorted(objects.items()):
        if not count:
            found.append((plate, "empty_plate", f"plate {plate} has no objects"))
    told: set[str] = set()
    for plate, used in sorted(slots.items()):
        for slot in sorted(used):
            profile = _slot(cfg, "filament_settings_id", slot)
            colour = _slot(cfg, "filament_colour", slot).lstrip("#")
            if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", colour):
                found.append((plate, "no_colour", f"filament {slot} ({profile or 'no profile'}) has no colour"))
            if not profile or profile in told:
                continue
            told.add(profile)
            # Orca's own system profiles cannot be imported (packed catalogue): not a problem.
            if presets and profile not in presets and "@system" not in profile.lower():
                found.append((plate, "unknown_profile", f"filament profile '{profile}' is not among the imported ones"))
            spelling = brand_problem(profile, brands)
            if spelling:
                found.append((plate, "brand_spelling", spelling))
    return found


# ---------------------------------------------------------------- storing them


async def _replace(
    db: AsyncSession,
    file_id: int,
    codes: set[str] | None,
    found: list[tuple[int | None, str, str]],
    plates: set[int | None] | None = None,
) -> list[FarmLibraryProblem]:
    """Make the file's problems of ``codes`` (all but slices when None) exactly ``found``; the new ones.

    ``plates`` limits it to those plates (a re-slice of some plates leaves the others as they are).
    """
    rows = (await db.execute(select(FarmLibraryProblem).where(FarmLibraryProblem.library_file_id == file_id))).scalars()
    mine = [
        r for r in rows if (r.code in codes if codes else r.code != SLICE) and (plates is None or r.plate in plates)
    ]
    old = {(r.plate, r.code, r.message): r for r in mine}
    new: list[FarmLibraryProblem] = []
    for key in dict.fromkeys(found):
        if key not in old:
            row = FarmLibraryProblem(
                library_file_id=file_id, plate=key[0], code=key[1], message=key[2][:500], since=_now()
            )
            db.add(row)
            new.append(row)
    for key, row in old.items():
        if key not in found:
            await db.delete(row)
    return new


async def _tell(db: AsyncSession, file_id: int, new: list[tuple[int | None, str, str]]) -> None:
    """One notification per file, only for problems it did not have ((plate, code, message) each)."""
    if not new:
        return
    lib_file = await db.get(LibraryFile, file_id)
    lines = [f"plate {plate}: {message}" if plate else message for plate, _, message in new]
    key = (file_id, tuple(sorted((plate or 0, code, message) for plate, code, message in new)))
    notify.notify(
        notify.LIBRARY_PROBLEM,
        key,
        filename=lib_file.filename if lib_file else file_id,
        problems="; ".join(lines)[:900],
    )


async def record_slice(db: AsyncSession, file_id: int, plate: int | None, colour: str | None, error=None) -> None:
    """The automatic re-slice of a plate ended: a failure is a problem of that plate, a success clears it.

    Bookkeeping only: it never raises (the re-slice keeps its own outcome and error), and it notifies
    nothing (``farm_slicing_failed`` already does).
    """
    try:
        from backend.app.farm.slicing import explain

        plate = plate or 1
        found = []
        if error is not None:
            reason = explain(str(getattr(error, "detail", None) or error))
            found = [(plate, SLICE, f"slicing{f' in {colour}' if colour else ''} failed: {reason}")]
        await _replace(db, file_id, {SLICE}, found, plates={plate})
        await db.commit()
    except Exception:
        logger.exception("Library problems: re-slice outcome of file %s plate %s not recorded", file_id, plate)
        await db.rollback()


async def check_new_versions(db: AsyncSession) -> int:
    """Run the checks without slicing on every farm 3MF whose version was not checked; how many."""
    from backend.app.farm.nas.access import library_path
    from backend.app.farm.nas.cache import ensure_local
    from backend.app.farm.plates.printer_map import PrinterModelResolver
    from backend.app.models.local_preset import LocalPreset

    checked = {c.library_file_id: c for c in (await db.execute(select(FarmLibraryCheck))).scalars()}
    rows = await db.execute(
        select(LibraryFile, FarmLibraryFile)
        .join(FarmLibraryFile, FarmLibraryFile.library_file_id == LibraryFile.id)
        .where(LibraryFile.file_type == "3mf", FarmLibraryFile.status == "ok", LibraryFile.deleted_at.is_(None))
    )
    filaments = [p for p in (await db.execute(select(LocalPreset))).scalars() if p.preset_type == "filament"]
    presets = {p.name for p in filaments}
    resolver = await PrinterModelResolver.load(db)
    models = sorted(resolver.manual.items()) + sorted(resolver.by_canonical.items())  # incl. manual mappings
    env = hashlib.sha256(json.dumps([sorted(presets), models]).encode()).hexdigest()
    # Plain values: a rollback below expires every loaded object, which async code cannot reload lazily.
    todo = [
        (f.id, s.sha256, s.printer_model)
        for f, s in rows.all()
        if f.id not in checked or checked[f.id].sha256 != s.sha256 or checked[f.id].env != env
    ]
    if not todo:
        return 0
    brands = known_brands([p.name for p in filaments], [p.filament_vendor for p in filaments if p.filament_vendor])
    told: dict[int, list[tuple[int | None, str, str]]] = {}
    for file_id, sha, model in todo:
        name = file_id
        try:  # any failure on one file: that file is skipped this round, never the whole sweep
            lib_file = await db.get(LibraryFile, file_id)
            name = lib_file.filename
            await ensure_local(db, lib_file)
            cfg, objects, slots = await asyncio.to_thread(read_project, library_path(lib_file.file_path))
            found = static_problems(
                cfg,
                objects,
                slots,
                model=model,
                model_ok=resolver.resolve(model).target_model is not None,
                presets=presets,
                brands=brands,
            )
            new = await _replace(db, file_id, None, found)
            row = await db.get(FarmLibraryCheck, file_id) or FarmLibraryCheck(library_file_id=file_id)
            row.sha256, row.env = sha, env
            db.add(row)
            await db.commit()
        except Exception as exc:
            logger.warning("Library check: %s skipped: %s", name, exc)
            await db.rollback()
            continue
        if new:
            told[file_id] = [(p.plate, p.code, p.message) for p in new]
    if len(told) > SUMMARY_AFTER:  # e.g. the first round: one message, not dozens
        notify.notify(
            notify.LIBRARY_PROBLEM,
            ("summary", tuple(sorted(told))),
            filename=f"{len(told)} file",
            problems=f"{len(told)} file da sistemare nella libreria: vedi Cosa stampare",
        )
    else:
        for file_id, new in told.items():
            await _tell(db, file_id, new)
    return len(todo)


async def list_problems(db: AsyncSession) -> list[dict]:
    rows = await db.execute(
        select(FarmLibraryProblem, LibraryFile)
        .join(LibraryFile, LibraryFile.id == FarmLibraryProblem.library_file_id)
        .where(LibraryFile.deleted_at.is_(None))
        .order_by(FarmLibraryProblem.since.desc())
    )
    return [
        {
            "id": p.id,
            "library_file_id": f.id,
            "filename": f.filename,
            "plate": p.plate,
            "code": p.code,
            "message": p.message,
            "since": p.since.isoformat() + "Z",
        }
        for p, f in rows.all()
    ]


async def _run() -> None:
    try:
        async with database.async_session() as db:
            await check_new_versions(db)
    except Exception:
        logger.exception("Library checks failed")


def start_check() -> None:
    """Check the new 3MF versions in a task of its own, one at a time."""
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_run(), name="farm-library-checks")


def stop_check() -> None:
    if _task is not None and not _task.done():
        _task.cancel()
