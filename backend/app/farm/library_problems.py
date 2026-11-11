"""Library problems: a 3MF that will not work is told when it is saved, not when its job waits (fork).

Two sources, both by file and plate:

* checks without slicing, on every new version (sha) of a production 3MF, run by a sweep in its own
  task: a plate without objects, a printer model no farm printer has;
* the automatic re-slice of a changed project (``reslice``): each plate's outcome, a failure with
  its reason explained (``slicing.explain``).

Only what stops a print: profile names and brand spellings are not checked (the print works with them),
nor the colours of the filament slots (a job takes the component's colour; a colour variant with no
filament of that colour already stops with its reason). Only production 3MFs (:func:`production_files`).

A problem goes away by itself when the file is fixed and checked again (or re-sliced). A file with a
new problem is notified once (``farm_library_problem``), not once per plate.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from sqlalchemy import DateTime, Integer, String, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core import database
from backend.app.core.database import Base
from backend.app.farm import notify
from backend.app.farm.models import FarmLibraryFile
from backend.app.models.library import LibraryFile

logger = logging.getLogger(__name__)

SLICE = "slice_failed"
EMPTY = "empty_plate"
MODEL = "printer_model"
CODES = (SLICE, EMPTY, MODEL)  # any other code stored by an older version is dropped
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
    # What the checks depended on (the printer models): checked again when it changes, so mapping the
    # model clears the problem by itself.
    env: Mapped[str | None] = mapped_column(String(64), nullable=True)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- checks without slicing


def read_project(path) -> dict[int, int]:
    """Blocking: the number of objects on each plate."""
    from backend.app.farm import variants
    from backend.app.utils.xml_prefixes import declared

    with zipfile.ZipFile(path) as zf:
        try:
            root = ET.fromstring(declared(zf.read(variants.MODEL_SETTINGS_ENTRY)))
        except KeyError:
            return {}
    plates = [
        int(m.get("value"))
        for p in root.findall("plate")
        for m in p.findall("metadata")
        if m.get("key") == "plater_id" and (m.get("value") or "").isdigit()
    ]
    return {n: len(variants._plate_objects(root, n)) for n in plates}


def static_problems(objects: dict[int, int], *, model: str | None, model_ok: bool) -> list[tuple[int | None, str, str]]:
    """``(plate, code, message)`` of a project, from what it holds (no slicing)."""
    found: list[tuple[int | None, str, str]] = []
    if model and not model_ok:
        found.append((None, MODEL, f"printer model {model} has no printer in the farm"))
    found += [(plate, EMPTY, f"plate {plate} has no objects") for plate, count in sorted(objects.items()) if not count]
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


async def production_files(db: AsyncSession) -> set[int]:
    """3MFs Printbuddy would print an active product from: a plate yielding a component of a product
    not disabled, in a 3MF that product prints from (``plates_for``, the rule of the plan and Accoda).

    Only these are checked or read; a confirmed plate alone, or a file in a product's folder that
    yields none of its components, is not enough.
    """
    from backend.app.farm.plates.service import plates_for
    from backend.app.farm.products.models import FarmComponent, FarmProduct, FarmProductPart

    components = (
        (
            await db.execute(
                select(FarmComponent)
                .join(FarmProductPart, FarmProductPart.component_id == FarmComponent.id)
                .join(FarmProduct, FarmProduct.project_id == FarmProductPart.project_id)
                .where(FarmProduct.disabled_at.is_(None))
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if not components:
        return set()
    return {p["library_file_id"] for plates in (await plates_for(db, list(components))).values() for p in plates}


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

        if file_id not in await production_files(db):
            return
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

    production = await production_files(db)
    # A file that is not (or no longer) the farm's: its problems go, unannounced, and it is checked
    # again from scratch if it becomes one. So do the problems this version no longer checks.
    for table in (FarmLibraryProblem, FarmLibraryCheck):
        await db.execute(delete(table).where(table.library_file_id.notin_(production)))
    await db.execute(delete(FarmLibraryProblem).where(FarmLibraryProblem.code.notin_(CODES)))
    await db.commit()
    checked = {c.library_file_id: c for c in (await db.execute(select(FarmLibraryCheck))).scalars()}
    rows = await db.execute(
        select(LibraryFile, FarmLibraryFile)
        .join(FarmLibraryFile, FarmLibraryFile.library_file_id == LibraryFile.id)
        .where(LibraryFile.file_type == "3mf", FarmLibraryFile.status == "ok", LibraryFile.deleted_at.is_(None))
    )
    resolver = await PrinterModelResolver.load(db)
    models = sorted(resolver.manual.items()) + sorted(resolver.by_canonical.items())  # incl. manual mappings
    env = hashlib.sha256(json.dumps(models).encode()).hexdigest()
    # Plain values: a rollback below expires every loaded object, which async code cannot reload lazily.
    todo = [
        (f.id, s.sha256, s.printer_model)
        for f, s in rows.all()
        if f.id in production and (f.id not in checked or checked[f.id].sha256 != s.sha256 or checked[f.id].env != env)
    ]
    if not todo:
        return 0
    told: dict[int, list[tuple[int | None, str, str]]] = {}
    for file_id, sha, model in todo:
        name = file_id
        try:  # any failure on one file: that file is skipped this round, never the whole sweep
            lib_file = await db.get(LibraryFile, file_id)
            name = lib_file.filename
            await ensure_local(db, lib_file)
            objects = await asyncio.to_thread(read_project, library_path(lib_file.file_path))
            found = static_problems(objects, model=model, model_ok=resolver.resolve(model).target_model is not None)
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
    production = await production_files(db)  # also before the next round has cleaned up
    rows = await db.execute(
        select(FarmLibraryProblem, LibraryFile)
        .join(LibraryFile, LibraryFile.id == FarmLibraryProblem.library_file_id)
        .where(LibraryFile.deleted_at.is_(None), LibraryFile.id.in_(production), FarmLibraryProblem.code.in_(CODES))
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
