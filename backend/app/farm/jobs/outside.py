"""Prints started outside Accoda (printer display, slicer, Mainsail, HelixScreen): their batches.

Upstream archives every print, wherever it was started. A print archived as completed that no
farm job printed becomes a farm job with its ``source`` (see ``FarmPrintJob``), recognised in
this order:
1. its name carries a queue fingerprint (``Sopra__1a2b3c4d.3mf``, also when reprinted from the
   printer's display): the earlier farm job that sent that file says component, colour, plate
   and pieces; or a plate's sliced output with that fingerprint says the plate;
2. the archived file's SHA-256 is a farm job's file or a plate's sliced output;
3. otherwise it is ``to_assign``, with a proposal when its name looks like a plate's 3MF; the
   user says the components and pieces (``assign``), or that it is not production.
A plate whose components only differ by colour is a proposal too: the colour is the user's call.

On Klipper, Moonraker's history adds the prints done while Printbuddy was off (no archive).
Failed and cancelled prints make no batch. Only prints that end after the first scan count
(``farm_outside_since``), so the farm's earlier history is not asked about.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.plates.models import FarmPlate
from backend.app.farm.products.folders import component_files, may_print
from backend.app.farm.products.models import FarmComponent, component_shapes
from backend.app.farm.products.shapes import plate_yield, product_shapes
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer

logger = logging.getLogger(__name__)

SINCE_KEY = "farm_outside_since"
FINGERPRINT_RE = re.compile(r"__([0-9a-f]{8})(?![0-9a-f])")
PLATE_RE = re.compile(r"plate[\s_-]*(\d+)", re.IGNORECASE)
SAME_PRINT = timedelta(minutes=10)  # a Moonraker job and an archive started this close are one print
NAME_MATCH = 0.75  # how alike a print's name and a 3MF's name must be for a proposal


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _since(db: AsyncSession) -> datetime:
    """Prints ending before the first scan are not asked about."""
    raw = await get_setting(db, SINCE_KEY)
    if raw:
        return datetime.fromisoformat(raw)
    now = _now()
    await set_setting(db, SINCE_KEY, now.isoformat())
    await db.commit()
    return now


def _core(name: str) -> str:
    """``K2-Citroen AMI - plate 2__1a2b3c4d.gcode.3mf`` -> ``k2citroenami``: what two names of one 3MF share."""
    name = FINGERPRINT_RE.sub("", name.rsplit("/", 1)[-1])
    for ext in (".gcode.3mf", ".3mf", ".gcode"):
        name = name.removesuffix(ext)
    return re.sub(r"[^a-z0-9]", "", PLATE_RE.sub("", name.lower()))


def _plate_number(names: list[str | None]) -> int | None:
    for name in names:
        if name and (m := PLATE_RE.search(name)):
            return int(m.group(1))
    return None


async def _plate_parts(db: AsyncSession, plate: FarmPlate, color: str | None) -> list[dict]:
    """What the plate prints, one entry per component; a colour variant's slice keeps its colour only."""
    files = await component_files(db)
    comps = list((await db.execute(select(FarmComponent).where(FarmComponent.retired_at.is_(None)))).scalars())
    own = await product_shapes(db, {c.project_id for c in comps if c.project_id is not None})
    out = []
    for c in comps:
        if not may_print(files, c.id, plate.library_file_id):
            continue
        if color and (c.color or "").strip().lower() != color:
            continue
        pieces = plate_yield(c, plate, own)
        if pieces:
            out.append(
                {
                    "component_code": c.code,
                    "name": c.name,
                    "pieces": pieces,
                    "color": c.color,
                    "plate_id": plate.id,
                    "_shapes": frozenset(component_shapes(c)),
                }
            )
    return out


def _clean(parts: list[dict]) -> list[dict]:
    return [{k: v for k, v in p.items() if k != "_shapes"} for p in parts]


def _ambiguous(parts: list[dict]) -> bool:
    """Two components printed as the same pieces (colours of one part): which one is not known."""
    return len({p["_shapes"] for p in parts}) != len(parts)


async def _output_plate(db: AsyncSession, out: LibraryFile) -> tuple[FarmPlate | None, str | None]:
    """The plate a sliced output (or the Klipper G-code taken out of one) was sliced from, and its colour."""
    meta = out.file_metadata or {}
    if "extracted_from_library_file_id" in meta:
        source = await db.get(LibraryFile, meta["extracted_from_library_file_id"])
        meta = (source.file_metadata or {}) if source else {}
    if not meta.get("sliced_from_library_file_id"):
        return None, None
    plate = await db.scalar(
        select(FarmPlate).where(
            FarmPlate.library_file_id == meta["sliced_from_library_file_id"],
            FarmPlate.plate_index == (meta.get("sliced_plate") or 1),
        )
    )
    return plate, (meta.get("sliced_color") or "").strip().lower() or None


async def _from_queue(db: AsyncSession, sha8: str | None, sha256: str | None) -> FarmPrintJob | None:
    """The latest farm job whose queue print was this file."""
    query = (
        select(FarmPrintJob)
        .join(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .join(PrintArchive, PrintArchive.id == PrintQueueItem.archive_id)
        .where(FarmPrintJob.status == "queued")
    )
    if sha256:
        query = query.where(PrintArchive.content_hash == sha256)
    else:
        query = query.where(PrintArchive.content_hash.like(f"{sha8}%"))
    return await db.scalar(query.order_by(FarmPrintJob.id.desc()).limit(1))


async def _from_outputs(db: AsyncSession, sha8: str | None, sha256: str | None) -> list[dict]:
    """The parts of the plate whose sliced output is this file ([] when none is)."""
    query = LibraryFile.active().where(LibraryFile.source_type == "sliced")
    if sha256:
        query = query.where(LibraryFile.file_hash == sha256)
    else:
        query = query.where(LibraryFile.file_hash.like(f"{sha8}%"))
    for out in (await db.execute(query.order_by(LibraryFile.id.desc()))).scalars():
        plate, color = await _output_plate(db, out)
        if plate is not None:
            return await _plate_parts(db, plate, color)
    return []


async def _proposal(db: AsyncSession, names: list[str | None]) -> list[dict] | None:
    """The parts of the plate whose 3MF (or sliced output) name is most like the print's."""
    core = next((c for n in names if n and (c := _core(n))), "")
    if not core:
        return None
    number = _plate_number(names)
    candidates: list[tuple[str, FarmPlate]] = []
    plates = [p for p in (await db.execute(select(FarmPlate))).scalars() if number is None or p.plate_index == number]
    files = {p.library_file_id for p in plates}
    names_of = {
        f.id: f.filename for f in (await db.execute(LibraryFile.active().where(LibraryFile.id.in_(files)))).scalars()
    }
    candidates += [(names_of[p.library_file_id], p) for p in plates if p.library_file_id in names_of]
    for out in (await db.execute(LibraryFile.active().where(LibraryFile.source_type == "sliced"))).scalars():
        plate, _ = await _output_plate(db, out)
        if plate is not None and (number is None or plate.plate_index == number):
            candidates.append((out.filename, plate))
    best, best_ratio = None, NAME_MATCH
    for name, plate in candidates:
        ratio = difflib.SequenceMatcher(None, core, _core(name)).ratio()
        if ratio >= best_ratio:
            best, best_ratio = plate, ratio
    if best is None:
        return None
    return _clean(await _plate_parts(db, best, None)) or None


async def recognise(db: AsyncSession, names: list[str | None], sha256: str | None) -> tuple[list[dict], list | None]:
    """``(parts, None)`` when the print is known, else ``([], proposal)``."""
    sha8 = next((m.group(1) for n in names if n and (m := FINGERPRINT_RE.search(n))), None)
    keys = ([{"sha8": sha8, "sha256": None}] if sha8 else []) + ([{"sha8": None, "sha256": sha256}] if sha256 else [])
    for key in keys:
        job = await _from_queue(db, **key)
        if job is not None:
            pieces = job.pieces or job.expected_pieces
            return [
                {"component_code": job.component_code, "pieces": pieces, "color": job.color, "plate_id": job.plate_id}
            ], None
        parts = await _from_outputs(db, **key)
        if parts:
            return ([], _clean(parts)) if _ambiguous(parts) else (_clean(parts), None)
    return [], await _proposal(db, names)


async def _record(
    db: AsyncSession,
    source: str,
    printer_id: int | None,
    name: str,
    finished_at: datetime | None,
    names: list[str | None],
    sha256: str | None,
) -> None:
    parts, proposal = await recognise(db, names, sha256)
    common = {"source": source, "printer_id": printer_id, "outside_name": name[:255], "finished_at": finished_at}
    if not parts:
        db.add(
            FarmPrintJob(
                component_code="", plate_ids=[], expected_pieces=0, status="to_assign", proposal=proposal, **common
            )
        )
        return
    now = _now()
    for p in parts:
        db.add(
            FarmPrintJob(
                component_code=p["component_code"],
                color=p["color"],
                plate_ids=[p["plate_id"]] if p["plate_id"] else [],
                plate_id=p["plate_id"],
                expected_pieces=p["pieces"],
                pieces=p["pieces"],
                status="outside",
                listed_at=now,
                **common,
            )
        )


async def _seen(db: AsyncSession, source: str) -> bool:
    return bool(await db.scalar(select(FarmPrintJob.id).where(FarmPrintJob.source == source).limit(1)))


async def scan_archives(db: AsyncSession) -> int:
    """Completed archives no farm job printed, since the first scan: their jobs. Returns how many prints."""
    since = await _since(db)
    queue_archives = (
        select(PrintQueueItem.archive_id)
        .join(FarmPrintJob, FarmPrintJob.queue_item_id == PrintQueueItem.id)
        .where(PrintQueueItem.archive_id.isnot(None))
    )
    rows = await db.execute(
        select(PrintArchive)
        .where(
            PrintArchive.status == "completed",
            PrintArchive.completed_at >= since,
            PrintArchive.deleted_at.is_(None),
            PrintArchive.id.not_in(queue_archives),
        )
        .order_by(PrintArchive.completed_at)
    )
    done = 0
    for archive in rows.scalars().all():
        source = f"archive:{archive.id}"
        if await _seen(db, source):
            continue
        extra = archive.extra_data or {}
        names = [
            archive.filename,
            archive.print_name,
            extra.get("original_subtask"),
            (extra.get("_print_data") or {}).get("gcode_file"),
        ]
        name = archive.print_name or archive.filename
        await _record(db, source, archive.printer_id, name, archive.completed_at, names, archive.content_hash)
        done += 1
    if done:
        await db.commit()
    return done


def _moonraker_history(client, since: datetime) -> list[dict]:
    """Moonraker's jobs since ``since``, through upstream's client (read-only)."""
    query = f"server/history/list?limit=100&order=asc&since={since.replace(tzinfo=timezone.utc).timestamp():.0f}"
    return client._get(query).get("jobs") or []


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


async def scan_klipper_history(db: AsyncSession) -> int:
    """Klipper prints that ended while Printbuddy was off (no archive): their jobs. Returns how many prints."""
    from backend.app.services.printer_manager import printer_manager
    from backend.app.services.printer_providers.factory import MOONRAKER_PROVIDERS

    since = await _since(db)
    done = 0
    for printer in (await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars().all():
        if (printer.provider or "").strip().lower() not in MOONRAKER_PROVIDERS:
            continue
        client = printer_manager.get_client(printer.id)
        if client is None or not hasattr(client, "_get"):
            continue
        try:
            history = await asyncio.to_thread(_moonraker_history, client, since)
        except Exception as exc:  # noqa: BLE001 - printer offline: asked again at the next scan
            logger.debug("Printer %s: Moonraker history not available: %s", printer.id, exc)
            continue
        for job in history:
            if job.get("status") != "completed" or not job.get("job_id") or not job.get("end_time"):
                continue
            source = f"klipper:{printer.id}:{job['job_id']}"
            if await _seen(db, source):
                continue
            started = _utc(job.get("start_time") or job["end_time"])
            window = (started - SAME_PRINT, started + SAME_PRINT)
            archived = await db.scalar(
                select(PrintArchive.id)
                .where(
                    PrintArchive.printer_id == printer.id,
                    or_(PrintArchive.started_at.between(*window), PrintArchive.created_at.between(*window)),
                )
                .limit(1)
            )
            if archived:
                continue  # Printbuddy saw it: its archive counts
            name = str(job.get("filename") or "")
            await _record(db, source, printer.id, name, _utc(job["end_time"]), [name], None)
            done += 1
    if done:
        await db.commit()
    return done


async def assign(db: AsyncSession, job: FarmPrintJob, parts: list[dict], not_production: bool) -> list[FarmPrintJob]:
    """The user's answer for a print outside the queue: its components and pieces, or not production."""
    if job.status not in ("to_assign", "not_production"):
        raise HTTPException(409, "Only a print outside the queue still to assign can be assigned")
    if not_production:
        job.status = "not_production"
        await db.commit()
        return [job]
    if not parts:
        raise HTTPException(422, "Say which components it printed, or that it is not production")
    codes = [p["component_code"] for p in parts]
    if len(set(codes)) != len(codes):
        raise HTTPException(422, "A component listed twice")
    active = select(FarmComponent).where(FarmComponent.code.in_(codes), FarmComponent.retired_at.is_(None))
    comps = {c.code: c for c in (await db.execute(active)).scalars()}
    for code in codes:
        if code not in comps:
            raise HTTPException(422, f"{code} is not an active component")
    now = _now()
    jobs = []
    for i, p in enumerate(parts):
        target = job
        if i:
            target = FarmPrintJob(
                source=job.source, printer_id=job.printer_id, outside_name=job.outside_name, finished_at=job.finished_at
            )
            db.add(target)
        target.component_code = p["component_code"]
        target.color = comps[p["component_code"]].color
        target.plate_ids, target.plate_id = [], None
        target.expected_pieces = target.pieces = p["pieces"]
        target.status, target.listed_at, target.proposal = "outside", now, None
        jobs.append(target)
    await db.commit()
    return jobs
