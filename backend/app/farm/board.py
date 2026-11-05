"""The queue page's "Jobs" board: one row per print, from the request to the end (fork).

A row is a farm job (Accoda, Seller Hub) with its queue item and archive, a queue item of its own (a print
from the library, a reprint), or a print outside the queue still to assign. Rows come in four groups:
``problems`` (a slice or a send that failed, a blocked job, a print that failed in the last day, a print
outside the queue to assign), ``printing``, ``waiting`` and ``history``. A row opened shows its timeline
(``farm_job_events``) and the technical details, the debug lines of the log included, to copy for support.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm import job_events
from backend.app.farm.job_events import FarmJobEvent
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.models import FarmPlate
from backend.app.farm.products.models import FarmComponent
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer

router = APIRouter(prefix="/farm/board", tags=["farm"])
_read = Depends(require_permission_if_auth_enabled(Permission.QUEUE_READ))
_act = Depends(require_permission_if_auth_enabled(Permission.QUEUE_CREATE))
# a forced slice cancels the job's queue item: the same permission as cancelling the job
_delete = Depends(require_permission_if_auth_enabled(Permission.QUEUE_DELETE_ALL))

PROBLEM_HOURS = 24  # a print that failed stays in "problems" this long, then it is history
RECENT_DAYS = 2  # cancelled jobs without a queue item shown in the history
TERMINAL = ("completed", "failed", "cancelled", "skipped")
# A waiting job's note that says something went wrong (from the dispatcher and the slicing notes)
_TROUBLE = re.compile(r"failed|not reachable|timed out|can't|No reference|changed|deleted|renamed", re.I)
_SECRETS = (
    # key = value, key: "value with spaces", "key": "value" (JSON)
    (
        re.compile(
            r"(access[_ ]?code|password|passwd|pwd|token|secret|api[_-]?key|auth[_-]?key)"
            r"(['\"]?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
            re.I,
        ),
        r"\1\2***",
    ),
    (re.compile(r"(authorization['\"]?\s*[:=]\s*['\"]?)(?:(bearer|basic|token)\s+)?[^\s'\",;]+", re.I), r"\1\2 ***"),
    (re.compile(r"(\bbearer\s+)[A-Za-z0-9._~+/=-]+", re.I), r"\1***"),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I), r"\1***:***@"),  # user:pass@host
    (re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+"), r"\1***"),  # a Telegram bot token in a URL
    (re.compile(r"(\bPASS\s+)\S+"), r"\1***"),  # the FTP login in an upload log
)


_SECRET_KEY = re.compile(
    r"^(access[_ ]?code|password|passwd|pwd|token|secret|api[_-]?key|auth[_-]?key|authorization)$", re.I
)


def _mask(value):
    """Secrets masked in a text, or in every text of a detail (dicts and lists walked)."""
    if isinstance(value, str):
        for pattern, repl in _SECRETS:
            value = pattern.sub(repl, value)
        return value.replace("  ***", " ***")
    if isinstance(value, dict):
        return {k: "***" if v and _SECRET_KEY.search(str(k)) else _mask(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_mask(v) for v in value]
    return value


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _naive(value: datetime | None) -> datetime | None:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value and value.tzinfo else value


def _slicer_of(state: FarmLibraryFile | None) -> str | None:
    return state.slicer if state is not None else None


def _group(job: FarmPrintJob | None, item: PrintQueueItem | None) -> str:
    if job is not None and job.status == "to_assign":
        return "problems"
    if job is not None and job.status == "waiting":
        return "problems" if job.note and _TROUBLE.search(job.note) else "waiting"
    if job is not None and job.status == "cancelled" and item is None:
        return "history"
    if item is None:
        return "history"
    if item.status == "printing":
        return "printing"
    if item.status == "pending":
        return "problems" if job is not None and job.note and _TROUBLE.search(job.note) else "waiting"
    if item.status == "failed" and (_naive(item.completed_at) or _now()) > _now() - timedelta(hours=PROBLEM_HOURS):
        return "problems"
    return "history"


def _phase(job: FarmPrintJob | None, item: PrintQueueItem | None) -> str:
    if job is not None and job.status == "to_assign":
        return "to_assign"
    if job is not None and job.status == "waiting":
        note = job.note or ""
        if "sliced" in note or "slicing" in note or "slice" in note.lower():
            return "slice_failed" if _TROUBLE.search(note) else "slicing"
        return "waiting"
    if job is not None and job.status == "cancelled" and item is None:
        return "cancelled"
    if item is None:
        return "finished" if job is not None and job.status == "outside" else "waiting"
    if item.status == "pending":
        return "sending" if item.dispatching_at else "queued"
    return {"printing": "printing", "completed": "finished", "failed": "failed"}.get(item.status, "cancelled")


def _actions(job: FarmPrintJob | None, item: PrintQueueItem | None) -> list[str]:
    acts: list[str] = []
    if job is not None and job.source is None:
        if job.status == "waiting":
            acts += ["retry_slice", "reslice", "cancel"]
        elif item is not None and item.status in ("failed", "cancelled"):
            acts += ["reslice", "requeue"]
        elif item is not None and item.status == "pending":
            acts += ["reslice", "cancel"]
    if job is not None and job.status == "to_assign":
        acts.append("assign")
    if item is not None and item.status == "pending":
        if item.manual_start:
            acts.append("start")
        if job is None:
            acts.append("cancel_item")
    if (item is not None and item.library_file_id) or job is not None:
        acts.append("open_file")
    return acts


def _row(job, item, printer, comp, archive, sliced, state) -> dict:
    status_text = None
    if job is not None and job.status in ("waiting", "to_assign"):
        status_text = job.note
    elif item is not None:
        status_text = item.error_message if item.status in ("failed", "cancelled") else item.waiting_reason
        if job is not None and item.status == "pending" and job.note:
            status_text = job.note
    if job is not None:
        title = (
            f"{job.component_code} {comp.name}"
            if comp is not None
            else job.outside_name or job.component_code or f"#{job.id}"
        )
    else:
        title = sliced.filename if sliced is not None else archive.filename if archive is not None else f"#{item.id}"
    group = _group(job, item)
    return {
        "key": f"job:{job.id}" if job is not None else f"item:{item.id}",
        "job_id": job.id if job is not None else None,
        "queue_item_id": item.id if item is not None else None,
        "archive_id": item.archive_id if item is not None else None,
        "title": title,
        "component_code": job.component_code if job is not None else None,
        "colour": (job.used_color or job.color) if job is not None else None,
        "pieces": (job.pieces or job.expected_pieces) if job is not None else None,
        "printer_id": (item.printer_id if item is not None else None) or (job.printer_id if job is not None else None),
        "printer": printer.name if printer is not None else None,
        "group": group,
        "phase": _phase(job, item),
        "level": "error" if group == "problems" else "info",
        "status_text": status_text,
        # where a queue item stopped (upload / start / printing / dispatch): the timeline of a print without events
        "failure_phase": (item.failure_detail or {}).get("phase") if item is not None else None,
        "slicer": _slicer_of(state),
        "file": sliced.filename if sliced is not None else None,
        "library_file_id": sliced.id if sliced is not None else None,
        "manual_start": bool(item.manual_start) if item is not None else False,
        "source": "outside" if job is not None and job.source else ("farm" if job is not None else "queue"),
        "created_at": (job.created_at if job is not None else item.created_at),
        "completed_at": item.completed_at if item is not None else (job.finished_at if job is not None else None),
        "actions": _actions(job, item),
    }


def _printer_id(job: FarmPrintJob | None, item: PrintQueueItem | None) -> int | None:
    return (item.printer_id if item is not None else None) or (job.printer_id if job is not None else None)


def _plate_id(job: FarmPrintJob | None) -> int | None:
    return (job.plate_id or (job.plate_ids or [None])[0]) if job is not None else None


async def _contexts(db: AsyncSession, pairs: list) -> list[tuple]:
    """Printer, component, archive, sliced file, project state and plate of each (job, item): six IN queries."""

    async def by(model, column, keys) -> dict:
        keys = {k for k in keys if k}
        if not keys:
            return {}
        rows = (await db.execute(select(model).where(column.in_(keys)))).scalars()
        return {getattr(r, column.key): r for r in rows}

    items = [item for _, item in pairs if item is not None]
    jobs = [job for job, _ in pairs if job is not None]
    printers = await by(Printer, Printer.id, (_printer_id(j, i) for j, i in pairs))
    comps = await by(FarmComponent, FarmComponent.code, (j.component_code for j in jobs))
    archives = await by(PrintArchive, PrintArchive.id, (i.archive_id for i in items))
    files = await by(LibraryFile, LibraryFile.id, (i.library_file_id for i in items))
    plates = await by(FarmPlate, FarmPlate.id, (_plate_id(j) for j in jobs))
    states = await by(FarmLibraryFile, FarmLibraryFile.library_file_id, (p.library_file_id for p in plates.values()))
    out = []
    for job, item in pairs:
        plate = plates.get(_plate_id(job))
        out.append(
            (
                printers.get(_printer_id(job, item)),
                comps.get(job.component_code) if job is not None else None,
                archives.get(item.archive_id) if item is not None else None,
                files.get(item.library_file_id) if item is not None else None,
                states.get(plate.library_file_id) if plate is not None else None,
                plate,
            )
        )
    return out


@router.get("", dependencies=[_read])
async def board(
    printer_id: int | None = None,
    history_offset: int = Query(0, ge=0),
    history_limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> dict:
    recent = _now() - timedelta(days=RECENT_DAYS)
    pairs: list[tuple[FarmPrintJob | None, PrintQueueItem | None]] = []
    jobs = await db.execute(
        select(FarmPrintJob, PrintQueueItem)
        .outerjoin(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .where(
            or_(
                FarmPrintJob.status.in_(("waiting", "to_assign")),
                PrintQueueItem.status.in_(("pending", "printing")),
                PrintQueueItem.completed_at >= _now() - timedelta(hours=PROBLEM_HOURS),
                (FarmPrintJob.status == "cancelled") & (FarmPrintJob.updated_at >= recent),
            )
        )
        .order_by(FarmPrintJob.id.desc())
    )
    pairs += list(jobs.all())
    seen = {item.id for _, item in pairs if item is not None}
    items = list(
        (
            await db.execute(
                select(PrintQueueItem)
                .where(PrintQueueItem.status.in_(("pending", "printing", *TERMINAL)))
                .order_by(PrintQueueItem.completed_at.desc().nulls_first(), PrintQueueItem.id.desc())
                .limit(300)
            )
        ).scalars()
    )
    items = [i for i in items if i.id not in seen]
    owners = {}
    if items:
        rows = await db.execute(select(FarmPrintJob).where(FarmPrintJob.queue_item_id.in_([i.id for i in items])))
        owners = {j.queue_item_id: j for j in rows.scalars()}
    pairs += [(owners.get(i.id), i) for i in items]

    groups: dict[str, list] = {"problems": [], "printing": [], "waiting": [], "history": []}
    for job, item in pairs:
        pid = (item.printer_id if item is not None else None) or (job.printer_id if job is not None else None)
        if printer_id and pid != printer_id:
            continue
        groups[_group(job, item)].append((job, item))

    def ended(pair) -> datetime:
        job, item = pair
        when = (item.completed_at if item is not None else None) or (job.updated_at if job is not None else None)
        return _naive(when) or datetime.min

    history = sorted(groups["history"], key=ended, reverse=True)
    groups["history"] = history[history_offset : history_offset + history_limit]
    out: dict = {"history_total": len(history)}
    shown = [pair for members in groups.values() for pair in members]
    contexts = iter(await _contexts(db, shown))
    progress = await _slice_progress(db, [job for job, _ in shown if job is not None and job.status == "waiting"])
    for name, members in groups.items():
        out[name] = [
            {**_row(job, item, *next(contexts)[:5]), "slice_progress": progress.get(job.id) if job else None}
            for job, item in members
        ]
    return out


async def _slice_progress(db: AsyncSession, jobs: list[FarmPrintJob]) -> dict[int, dict]:
    """The slicer's progress for waiting jobs whose plate is being sliced now (one query for their plates)."""
    from backend.app.farm.slicing import slice_progress

    ids = {pid for job in jobs for pid in job.plate_ids or []}
    if not ids:
        return {}
    plates = {p.id: p for p in (await db.execute(select(FarmPlate).where(FarmPlate.id.in_(ids)))).scalars()}
    out = {}
    for job in jobs:
        running = []
        for pid in job.plate_ids or []:
            plate = plates.get(pid)
            now = slice_progress(plate.library_file_id, plate.plate_index, job.color) if plate else None
            if now:
                running.append(now)
        if running:  # the furthest one: the plate the job will most likely get
            out[job.id] = max(running, key=lambda p: p["percent"] or 0)
    return out


async def _find(db: AsyncSession, key: str) -> tuple[FarmPrintJob | None, PrintQueueItem | None]:
    kind, _, raw = key.partition(":")
    if not raw.isdigit() or kind not in ("job", "item"):
        raise HTTPException(404, "Unknown row")
    if kind == "job":
        job = await db.get(FarmPrintJob, int(raw))
        if job is None:
            raise HTTPException(404, "Job not found")
        item = await db.get(PrintQueueItem, job.queue_item_id) if job.queue_item_id else None
        return job, item
    item = await db.get(PrintQueueItem, int(raw))
    if item is None:
        raise HTTPException(404, "Queue item not found")
    return await db.scalar(select(FarmPrintJob).where(FarmPrintJob.queue_item_id == item.id)), item


def _event_dict(e: FarmJobEvent) -> dict:
    return {
        "at": e.at,
        "phase": e.phase,
        "level": e.level,
        "message": _mask(e.message),
        "printer_id": e.printer_id,
        "detail": _mask(e.detail),  # upload logs, failure details: what the printer or the NAS answered
    }


async def timeline(db: AsyncSession, job: FarmPrintJob | None, item: PrintQueueItem | None) -> list[dict]:
    """The row's events, oldest first: its own, and the slices of its plates (asked since the job was made)."""
    await job_events.flush()  # what happened in the last second too
    conds = []
    if job is not None:
        conds.append(FarmJobEvent.job_id == job.id)
    if item is not None:
        conds.append(FarmJobEvent.queue_item_id == item.id)
    if job is not None and job.plate_ids:
        from backend.app.farm.slicing import colour_key

        plates = (await db.execute(select(FarmPlate).where(FarmPlate.id.in_(job.plate_ids)))).scalars().all()
        since = (_naive(job.created_at) or _now()) - timedelta(minutes=5)
        for plate in plates:
            conds.append(
                (FarmJobEvent.file_id == plate.library_file_id)
                & (FarmJobEvent.plate == plate.plate_index)
                & (FarmJobEvent.at >= since)
                & or_(FarmJobEvent.colour.is_(None), FarmJobEvent.colour == colour_key(job.color))
            )
    if not conds:
        return []
    rows = await db.execute(select(FarmJobEvent).where(or_(*conds)).order_by(FarmJobEvent.at, FarmJobEvent.id))
    return [_event_dict(e) for e in rows.scalars()]


def _log_lines(needles: list[str], limit: int = 150) -> list[str]:
    """The log lines that name this print (its queue item, its job, its files), secrets masked."""
    needles = [n for n in needles if n]
    if not needles:
        return []
    path = Path(settings.log_dir) / "printbuddy.log"
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            lines = [ln.rstrip() for ln in fh if any(n in ln for n in needles)]
    except OSError:
        return []
    return [_mask(ln)[:1000] for ln in lines[-limit:]]


@router.get("/{key}", dependencies=[_read])
async def detail(key: str, db: AsyncSession = Depends(get_db)) -> dict:
    job, item = await _find(db, key)
    printer, comp, archive, sliced, state, plate = (await _contexts(db, [(job, item)]))[0]
    row = _row(job, item, printer, comp, archive, sliced, state)
    technical = {
        "job": None
        if job is None
        else {
            "id": job.id,
            "component": job.component_code,
            "name": comp.name if comp is not None else None,
            "colour": job.color,
            "colour_printed": job.used_color,
            "pieces_expected": job.expected_pieces,
            "pieces": job.pieces,
            "printed_pieces": job.printed_pieces,
            "plate_ids": job.plate_ids,
            "plate_id": job.plate_id,
            "status": job.status,
            "note": job.note,
            "queued_as": job.queued_as,
            "options": job.queue_options,
            "source": job.source,
            "excluded": job.excluded,
            "created_at": job.created_at,
        },
        "plate": None
        if plate is None
        else {
            "id": plate.id,
            "project_file_id": plate.library_file_id,
            "plate": plate.plate_index,
            "model": plate.printer_model,
            "materials": plate.materials,
            "project": state.path if state is not None else None,
            "project_sha256": state.sha256 if state is not None else None,
            "slicer": _slicer_of(state),
        },
        "queue_item": None
        if item is None
        else {
            "id": item.id,
            "status": item.status,
            "position": item.position,
            "waiting_reason": item.waiting_reason,
            "error_message": item.error_message,
            "failure_detail": item.failure_detail,
            "dispatch_attempts": item.dispatch_attempts,
            "manual_start": item.manual_start,
            "plate": item.plate_id,
            "ams_mapping": item.ams_mapping,
            "filament_overrides": item.filament_overrides,
            "required_filament_types": item.required_filament_types,
            "scheduled_time": item.scheduled_time,
            "dispatching_at": item.dispatching_at,
            "started_at": item.started_at,
            "completed_at": item.completed_at,
        },
        "sliced_file": None
        if sliced is None
        else {
            "id": sliced.id,
            "name": sliced.filename,
            "size": sliced.file_size,
            "sha256": getattr(sliced, "file_hash", None),
            "metadata": sliced.file_metadata,
        },
        "archive": None if archive is None else {"id": archive.id, "name": archive.filename, "status": archive.status},
        "printer": None
        if printer is None
        else {
            "id": printer.id,
            "name": printer.name,
            "model": printer.model,
            "provider": printer.provider,
        },
    }
    needles = [
        f"Queue item {item.id}:" if item is not None else "",
        f"queue item {item.id} " if item is not None else "",
        f"Farm job {job.id}:" if job is not None else "",
        sliced.filename if sliced is not None else "",
    ]
    return {
        "row": row,
        "events": await timeline(db, job, item),
        "technical": _mask(technical),
        "log": _log_lines(needles),
    }


# ---------------------------------------------------------------- actions


async def _farm_job(db: AsyncSession, key: str) -> tuple[FarmPrintJob, PrintQueueItem | None]:
    job, item = await _find(db, key)
    if job is None or job.source is not None:
        raise HTTPException(409, "Only Accoda jobs can be sliced again or put back in the queue")
    return job, item


@router.post("/{key}/retry-slice", dependencies=[_act])
async def retry_slice(key: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """A waiting job: forget the failed slices of its plates and ask them again now (not after the wait)."""
    from backend.app.farm import slicing

    job, _ = await _farm_job(db, key)
    if job.status != "waiting":
        raise HTTPException(409, "The job is not waiting for a slice")
    asked = await _slice_again(db, job, slicing, force=False)
    job_events.record("action", f"Slice asked again by hand ({asked} plate(s))", job_id=job.id, detail=_who(request))
    return {"asked": asked}


@router.post("/{key}/reslice", dependencies=[_delete])
async def reslice(key: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """Slice the job's plates again from the 3MF, even when a slice exists (a wrong or broken one).

    A job already queued, or whose print failed, goes back to waiting: it is sent again with the new slice.
    A slice used by another queued print is never overwritten: the new one is saved beside it (#104).
    The current slice is marked superseded at once, so the job waits for the new one and is never placed
    again with the old one while the new one is being made.
    """
    from backend.app.farm import slicing

    job, item = await _farm_job(db, key)
    if item is not None and item.status == "printing":
        raise HTTPException(409, "Printing now: stop it first")
    if item is not None and item.status == "pending" and item.dispatching_at is not None:
        raise HTTPException(409, "Being sent to the printer right now: try again in a minute")
    if item is not None and item.status == "pending":
        item.status = "cancelled"
        item.completed_at = datetime.now(timezone.utc)
        item.error_message = "Sliced again by hand: queued again with the new slice"
    _back_to_waiting(job, "Sliced again by hand")
    await db.commit()
    asked = await _slice_again(db, job, slicing, force=True)
    job_events.record("action", f"New slice forced by hand ({asked} plate(s))", job_id=job.id, detail=_who(request))
    return {"asked": asked}


@router.post("/{key}/requeue", dependencies=[_act])
async def requeue(key: str, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    """A job whose queue item failed or was cancelled: back to waiting, placed again by the dispatcher."""
    job, item = await _farm_job(db, key)
    if item is None or item.status not in ("failed", "cancelled"):
        raise HTTPException(409, "Only a job whose print failed or was cancelled goes back to the queue")
    _back_to_waiting(job, "Put back in the queue by hand")
    await db.commit()
    job_events.record("action", "Put back in the queue by hand", job_id=job.id, detail=_who(request))
    return {"id": job.id, "status": job.status}


def _back_to_waiting(job: FarmPrintJob, note: str) -> None:
    job.status = "waiting"
    job.queue_item_id = None
    job.plate_id = None
    job.pieces = None
    job.queued_as = None
    job.printer_id = (job.queue_options or {}).get("printer_id")  # a printer chosen by hand stays, auto stays auto
    job.note = note


async def _slice_again(db: AsyncSession, job: FarmPrintJob, slicing, *, force: bool) -> int:
    asked = 0
    for plate_id in job.plate_ids or []:
        plate = await db.get(FarmPlate, plate_id)
        if plate is None:
            continue
        if force:  # the 3MF read again from the NAS; the old slice is never used meanwhile
            asked += await slicing.force_slice(db, plate, job.color)
            continue
        key = slicing._queue_key(plate.library_file_id, plate.plate_index, job.color)
        slicing._failed.pop(key, None)
        slicing._forget_asked(key)
        if await slicing.variant_slice(db, plate, job.color) is not None:
            continue  # already sliced: nothing to retry for this plate
        if await slicing.ask_variant_slicing(db, plate, job.color):
            asked += 1
    return asked


def _who(request: Request) -> dict:
    from backend.app.services.queue_failure import requester

    return requester(getattr(request.state, "user", None), request)
