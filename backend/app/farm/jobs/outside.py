"""Prints started outside Accoda (printer display, slicer, Mainsail, HelixScreen): their batches.

Upstream archives every print, wherever it was started. A print archived as completed that no
farm job printed becomes a farm job with its ``source`` (see ``FarmPrintJob``), recognised in
this order:
1. its name carries a queue fingerprint (``Sopra__1a2b3c4d.3mf``, also when reprinted from the
   printer's display): the earlier farm job that sent that file says component, colour, plate
   and pieces (farm jobs that disagree make it a proposal); or a plate's sliced output with that
   fingerprint says the plate;
2. the archived file's SHA-256 is a farm job's file or a plate's sliced output;
3. otherwise it is ``to_assign``, with a proposal when its name looks like a plate's 3MF; the
   user says the components and pieces (``assign``), or that it is not production.
A plate whose components only differ by colour takes the one in the colour the printer fed,
only when that colour is sure (``service.used_color(..., sure=True)``); otherwise it is a
proposal of alternatives: components are never mixed up on a guess.

A print left to assign is notified (``farm_batch_to_assign``).

On Klipper, Moonraker's history adds the prints done while Printbuddy was off (no archive). It is
read page by page from a cursor per printer, so no job is left out however many there are; a job
Printbuddy archived itself (same file, archived during it or ended with it) is not counted twice.
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
from backend.app.farm import notify
from backend.app.farm.jobs import service
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
SAME_PRINT = timedelta(minutes=10)  # margin around a Moonraker job for the archive of the same print
HISTORY_KEY = "farm_klipper_history_{}"  # per printer: start (epoch) of the history read up to
HISTORY_PAGE = 100  # Moonraker's largest page
HISTORY_PAGES = 50  # pages a scan at most; the cursor goes on from there at the next scan
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


async def _from_queue(db: AsyncSession, sha8: str | None, sha256: str | None) -> list[FarmPrintJob]:
    """The farm jobs whose queue print was this file, latest first."""
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
    return list((await db.execute(query.order_by(FarmPrintJob.id.desc()))).scalars())


async def _alternatives(db: AsyncSession, parts: list[dict]) -> list[dict]:
    """Proposal entries of which only one was printed: the user picks."""
    names = dict(
        (
            await db.execute(
                select(FarmComponent.code, FarmComponent.name).where(
                    FarmComponent.code.in_([p["component_code"] for p in parts])
                )
            )
        ).all()
    )
    return [{**p, "name": p.get("name") or names.get(p["component_code"], ""), "alternative": True} for p in parts]


def _offer(parts: list[dict]) -> list[dict]:
    """A plate's parts as a proposal: printed together, except colours of one piece (alternatives)."""
    alternative = _ambiguous(parts)
    return [{**p, "alternative": alternative} for p in _clean(parts)]


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
    return _offer(await _plate_parts(db, best, None)) or None


async def recognise(
    db: AsyncSession, names: list[str | None], sha256: str | None, used: str | None = None
) -> tuple[list[dict], list | None]:
    """``(parts, None)`` when the print is known, else ``([], proposal)``.

    ``used``: the colour the printer fed, only when sure: it picks between colours of one piece.
    """
    sha8 = next((m.group(1) for n in names if n and (m := FINGERPRINT_RE.search(n))), None)
    keys = ([{"sha8": sha8, "sha256": None}] if sha8 else []) + ([{"sha8": None, "sha256": sha256}] if sha256 else [])
    for key in keys:
        jobs = await _from_queue(db, **key)
        if jobs:
            parts = list(
                {
                    (j.component_code, j.pieces or j.expected_pieces, j.color, j.plate_id): {
                        "component_code": j.component_code,
                        "pieces": j.pieces or j.expected_pieces,
                        "color": j.color,
                        "plate_id": j.plate_id,
                    }
                    for j in jobs
                }.values()
            )
            if len(parts) == 1:
                return parts, None
            return [], await _alternatives(db, parts)  # the same file sent for different batches
        parts = await _from_outputs(db, **key)
        if parts and _ambiguous(parts) and used:
            parts = [p for p in parts if (p["color"] or "").lower() == used.lower()] or parts
        if parts:
            return ([], _offer(parts)) if _ambiguous(parts) else (_clean(parts), None)
    return [], await _proposal(db, names)


async def _record(
    db: AsyncSession,
    source: str,
    printer_id: int | None,
    name: str,
    finished_at: datetime | None,
    names: list[str | None],
    sha256: str | None,
    used: str | None = None,
    sure: str | None = None,
) -> list[FarmPrintJob]:
    """The print's jobs, added to the session; ``_commit`` lists and notifies them."""
    parts, proposal = await recognise(db, names, sha256, sure)
    common = {
        "source": source,
        "printer_id": printer_id,
        "outside_name": name[:255],
        "finished_at": finished_at,
        "used_color": used,
    }
    if not parts:
        jobs = [
            FarmPrintJob(
                component_code="", plate_ids=[], expected_pieces=0, status="to_assign", proposal=proposal, **common
            )
        ]
    else:
        jobs = [
            FarmPrintJob(
                component_code=p["component_code"],
                color=p["color"],
                plate_ids=[p["plate_id"]] if p["plate_id"] else [],
                plate_id=p["plate_id"],
                expected_pieces=p["pieces"],
                pieces=p["pieces"],
                status="outside",
                **common,
            )
            for p in parts
        ]
    db.add_all(jobs)
    return jobs


async def _commit(db: AsyncSession, jobs: list[FarmPrintJob]) -> None:
    """Batches get ``listed_at`` right before the commit, so a reader of ``since`` never skips one; prints
    left to assign are notified once committed."""
    now = _now()
    for job in jobs:
        if job.status == "outside":
            job.listed_at = now
    await db.commit()
    names = dict((await db.execute(select(Printer.id, Printer.name))).all())
    for job in jobs:
        if job.status != "to_assign":
            continue
        printer = names.get(job.printer_id)
        if job.proposal:
            joiner = " oppure " if any(p.get("alternative") for p in job.proposal) else ", "
            hint = "Sembra: " + joiner.join(f"{p['component_code']} {p['name']} ×{p['pieces']}" for p in job.proposal)
        else:
            hint = "Nessuna proposta: scegli prodotto e pezzi in Cosa stampare."
        detail = f"{printer or 'Stampante'}: {job.outside_name} stampato fuori coda. {hint}"
        notify.notify(
            notify.BATCH_TO_ASSIGN,
            job.source,
            printer_id=job.printer_id,
            printer_name=printer,
            batch=job.outside_name,
            detail=detail,
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
    archives = [
        (a.id, a.printer_id, a.filename, a.print_name, a.completed_at, a.content_hash, a.extra_data or {})
        for a in rows.scalars().all()
    ]
    done = 0
    for archive_id, printer_id, filename, print_name, completed_at, content_hash, extra in archives:
        source = f"archive:{archive_id}"
        try:  # one archive at a time: one that always fails never holds back the others
            if await _seen(db, source):
                continue
            gcode = (extra.get("_print_data") or {}).get("gcode_file")
            names = [filename, print_name, extra.get("original_subtask"), gcode]
            used, sure = service.used_color(extra), service.used_color(extra, sure=True)
            jobs = await _record(
                db, source, printer_id, print_name or filename, completed_at, names, content_hash, used, sure
            )
            await _commit(db, jobs)
        except Exception:
            await db.rollback()
            logger.exception("Archive %s outside the queue not recorded: tried again at the next scan", archive_id)
            continue
        done += 1
    return done


def _moonraker_history(client, since: float) -> tuple[list[dict], bool]:
    """Moonraker's jobs started since ``since`` (epoch), oldest first, page by page, through upstream's
    client (read-only); False when more pages are left for the next scan."""
    jobs: list[dict] = []
    for page in range(HISTORY_PAGES):
        query = f"server/history/list?limit={HISTORY_PAGE}&start={page * HISTORY_PAGE}&order=asc&since={since:.0f}"
        found = client._get(query).get("jobs") or []
        jobs += found
        if len(found) < HISTORY_PAGE:
            return jobs, True
    return jobs, False


def _next_cursor(jobs: list[dict], cursor: float) -> float:
    """Up to the first job still running (read again until it ends), else the latest one read."""
    running = [j["start_time"] for j in jobs if j.get("status") == "in_progress" and j.get("start_time")]
    if running:
        return max(cursor, min(running))
    return max([cursor] + [j["start_time"] for j in jobs if j.get("start_time")])


async def _archived(db: AsyncSession, printer_id: int, name: str, start: datetime, end: datetime) -> bool:
    """Printbuddy archived this Moonraker job itself: an archive of the same file on the printer, made or
    started during the job (also after a restart mid-print), or ended with it.

    Made or started up to the job's end, not after: the next print of the same file, archived right
    after, is another print.
    """
    during = (start - SAME_PRINT, end)
    rows = await db.execute(
        select(PrintArchive.filename, PrintArchive.print_name).where(
            PrintArchive.printer_id == printer_id,
            or_(
                PrintArchive.started_at.between(*during),
                PrintArchive.created_at.between(*during),
                PrintArchive.completed_at.between(end - SAME_PRINT, end + SAME_PRINT),
            ),
        )
    )
    core = _core(name)
    return any(core in (_core(filename or ""), _core(print_name or "")) for filename, print_name in rows.all())


def _utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None)


async def scan_klipper_history(db: AsyncSession) -> int:
    """Klipper prints that ended while Printbuddy was off (no archive): their jobs. Returns how many prints."""
    from backend.app.services.printer_manager import printer_manager
    from backend.app.services.printer_providers.factory import MOONRAKER_PROVIDERS

    since = (await _since(db)).replace(tzinfo=timezone.utc).timestamp()
    printers = (await db.execute(select(Printer.id, Printer.provider).where(Printer.is_active.is_(True)))).all()
    done = 0
    for printer_id, provider in printers:
        if (provider or "").strip().lower() not in MOONRAKER_PROVIDERS:
            continue
        client = printer_manager.get_client(printer_id)
        if client is None or not hasattr(client, "_get"):
            continue
        key = HISTORY_KEY.format(printer_id)
        cursor = float(await get_setting(db, key) or since)
        await db.commit()  # no transaction open while a slow or offline printer is asked
        try:
            history, complete = await asyncio.to_thread(_moonraker_history, client, cursor)
        except Exception as exc:  # noqa: BLE001 - printer offline: asked again at the next scan
            logger.info("Printer %s: Moonraker history not available: %s", printer_id, exc)
            continue
        if not complete:
            logger.warning("Printer %s: %d Moonraker jobs read, more at the next scan", printer_id, len(history))
        failed: list[float] = []
        for job in history:
            if job.get("status") != "completed" or not job.get("job_id") or not job.get("end_time"):
                continue
            try:  # one job at a time: one that always fails never holds back the others
                if await _klipper_job(db, printer_id, job):
                    done += 1
            except Exception:
                await db.rollback()
                logger.exception("Printer %s: Moonraker job %s not recorded", printer_id, job.get("job_id"))
                failed.append(job.get("start_time") or cursor)
        # The cursor stops at a job that failed, so it is read again at the next scan.
        await set_setting(db, key, str(min([_next_cursor(history, cursor)] + [max(cursor, f) for f in failed])))
        await db.commit()
        if history:
            logger.info("Printer %s: %d Moonraker jobs read", printer_id, len(history))
    return done


async def _klipper_job(db: AsyncSession, printer_id: int, job: dict) -> bool:
    """Record one finished Moonraker job, unless Printbuddy already has it; True when recorded."""
    source = f"klipper:{printer_id}:{job['job_id']}"
    if await _seen(db, source):
        return False
    name = str(job.get("filename") or "")
    started, ended = _utc(job.get("start_time") or job["end_time"]), _utc(job["end_time"])
    if await _archived(db, printer_id, name, started, ended):
        return False  # Printbuddy saw it: its archive counts
    await _commit(db, await _record(db, source, printer_id, name, ended, [name], None))
    return True


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
                source=job.source,
                printer_id=job.printer_id,
                outside_name=job.outside_name,
                finished_at=job.finished_at,
                used_color=job.used_color,
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
