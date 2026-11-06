"""Farm API: Accoda (phase 4) jobs, and the prints done outside the queue (``outside``)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.jobs import outside, service
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.plates.models import FarmPlate
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer

router = APIRouter(tags=["farm"])  # mounted under /farm by the products router

_read = Depends(require_permission_if_auth_enabled(Permission.QUEUE_READ))
_create = Depends(require_permission_if_auth_enabled(Permission.QUEUE_CREATE))
_delete = Depends(require_permission_if_auth_enabled(Permission.QUEUE_DELETE_ALL))
RECENT = timedelta(days=2)


class JobIn(BaseModel):
    component_code: str = Field(min_length=1, max_length=12)
    plate_ids: list[int] = Field(min_length=1, max_length=50)
    printer_id: int | None = None  # None: the first free printer that fits
    # Upstream queue options, as in its print dialog. Slots: global tray ids by the plate's filament order.
    ams_mapping: list[int] | None = Field(default=None, max_length=32)
    scheduled_time: datetime | None = None
    manual_start: bool = False
    require_previous_success: bool = False
    auto_off_after: bool = False
    gcode_injection: bool = False
    force_slice: bool = False  # slice its plates again from the 3MF on the NAS before it is queued
    # a chosen printer gets it even without the colour loaded, and it starts by itself when the printer is free
    ignore_spool: bool = False


class JobsIn(BaseModel):
    jobs: list[JobIn] = Field(min_length=1, max_length=100)


class AssignPartIn(BaseModel):
    component_code: str = Field(min_length=1, max_length=12)
    pieces: int = Field(ge=1, le=10000)


class AssignIn(BaseModel):
    parts: list[AssignPartIn] = Field(default_factory=list, max_length=50)
    not_production: bool = False


def _state(job: FarmPrintJob, item_status: str | None) -> str:
    if job.status == "queued":  # its queue item's state; once deleted, the outcome the job kept (``outcome``)
        return item_status or job.outcome or "removed"
    return "completed" if job.status == "outside" else job.status


def _exclusions(job: FarmPrintJob, state: str) -> str | None:
    """Whether the objects the printer excluded come off the batch: ``read`` (subtracted), ``unread`` (those read
    subtracted, the end not read: there may be more), ``unmatched`` (one isn't on the plate: those matched
    before subtracted); None for a print outside the queue or not started."""
    if job.source is not None or state not in ("printing", "completed"):
        return None
    read = job.excluded
    if read is None or (state == "completed" and not read.get("end")):
        return "unread"
    return "read" if read.get("matched", True) else "unmatched"


def _job_dict(
    job: FarmPrintJob, item_status: str | None, printer: str | None, finished_at: datetime | None = None
) -> dict:
    state = _state(job, item_status)
    planned = job.pieces if job.pieces is not None else job.expected_pieces
    exclusions = _exclusions(job, state)
    excluded = (job.excluded or {}).get("objects") if exclusions else None
    return {
        "id": job.id,
        "component_code": job.component_code,
        # The batch's colour: the filament the printer fed when known, else the component's
        "color": job.used_color or job.color,
        "component_color": job.color,
        "color_from_printer": job.used_color is not None,
        # waiting -> pending (in the queue, waiting for the plate-clear OK) -> printing -> completed/failed;
        # outside the queue: to_assign -> completed, or not_production
        "state": state,
        "note": job.note,
        "plate_ids": job.plate_ids,
        "plate_id": job.plate_id,
        "printer_id": job.printer_id,
        "printer": printer,
        "queue_item_id": job.queue_item_id,
        "archive_id": job.archive_id,
        # The batch's pieces: the plate's less the excluded objects matched so far (``exclusions`` says if there
        # may be more), else the plate's: a ceiling of the good pieces either way. It can change while printing.
        "pieces": job.printed_pieces if exclusions and job.printed_pieces is not None else planned,
        "pieces_planned": planned,
        "excluded_objects": None if excluded is None else [n or f"#{oid}" for oid, n in excluded.items()],
        "exclusions": exclusions,
        "scheduled_time": (job.queue_options or {}).get("scheduled_time"),
        "created_at": job.created_at,
        "finished_at": finished_at or job.finished_at,  # the print's end, UTC
        # A batch since: the end of a queue print; when recognised or assigned for one outside the queue
        "listed_at": (job.listed_at if job.source else finished_at) if state == "completed" else None,
        "outside_queue": job.source is not None,
        "print_name": job.outside_name,  # outside the queue: the file it printed
        "proposal": job.proposal,  # to_assign: the components its name suggests
    }


@router.get("/jobs/history-links", dependencies=[_read])
async def history_links(
    printer_id: int | None = None, status: str | None = None, db: AsyncSession = Depends(get_db)
) -> dict:
    """How many farm jobs the queue's "clear history" touches (same filters as ``/queue/history``): they keep
    their outcome on themselves; the warning says how many, and how many are batches Seller Hub may still check."""
    ended = ("completed", "failed", "skipped", "cancelled")
    query = (
        select(PrintQueueItem.status, func.count(FarmPrintJob.id))
        .join(FarmPrintJob, FarmPrintJob.queue_item_id == PrintQueueItem.id)
        .where(PrintQueueItem.status.in_(ended if not status else [status] if status in ended else []))
        .group_by(PrintQueueItem.status)
    )
    if printer_id is not None:
        query = query.where(
            PrintQueueItem.printer_id.is_(None) if printer_id == -1 else PrintQueueItem.printer_id == printer_id
        )
    counts = dict((await db.execute(query)).all())
    return {"jobs": sum(counts.values()), "completed": counts.get("completed", 0)}


@router.post("/jobs", dependencies=[_create])
async def create_jobs(data: JobsIn, db: AsyncSession = Depends(get_db)) -> list[dict]:
    jobs = await service.create_jobs(db, [j.model_dump() for j in data.jobs])
    if any(j.printer_id for j in jobs):
        await service.dispatch_once(db)  # a chosen printer's queue gets it now, not at the next tick
    return [_job_dict(j, "pending" if j.status == "queued" else None, None) for j in jobs]


@router.get("/plates/{plate_id}/filaments", dependencies=[_read])
async def plate_filaments(plate_id: int, color: str | None = None, db: AsyncSession = Depends(get_db)) -> dict:
    """Filament slots of the plate sliced in the component's ``color``, for the print dialog's slot mapping;
    ``sliced`` false: none yet."""
    plate = await db.get(FarmPlate, plate_id)
    if plate is None:
        raise HTTPException(404, "Plate not found")
    filaments = await service.plate_filaments(db, plate, color)
    return {"plate_id": plate_id, "sliced": filaments is not None, "filaments": filaments or []}


@router.get("/jobs", dependencies=[_read])
async def list_jobs(
    state: Literal["completed"] | None = None, since: datetime | None = None, db: AsyncSession = Depends(get_db)
) -> list[dict]:
    """Jobs still waiting or in the queue, prints outside the queue still to assign, plus those of the last two days.

    ``state=completed``: only the jobs printed to the end, however old, oldest first: Seller Hub's batches to
    check before they go to stock. Prints outside the queue come too (``outside_queue``) once their components
    are known, those to assign only after. ``since`` and the order go by ``listed_at``: a print assigned today
    is a batch of today, whenever it ended.
    """
    query = (
        select(FarmPrintJob, PrintQueueItem.status, Printer.name, PrintQueueItem.completed_at)
        .outerjoin(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .outerjoin(Printer, Printer.id == FarmPrintJob.printer_id)
    )
    if state == "completed":
        queued = query.where(
            FarmPrintJob.status == "queued",
            or_(
                PrintQueueItem.status == "completed",
                # its queue item deleted: the outcome it kept
                PrintQueueItem.id.is_(None) & (FarmPrintJob.outcome == "completed"),
            ),
        )
        printed = query.where(FarmPrintJob.status == "outside")
        if since is not None:
            queued = queued.where(
                func.coalesce(PrintQueueItem.completed_at, FarmPrintJob.finished_at) >= service._utc(since)
            )
            printed = printed.where(FarmPrintJob.listed_at >= service._utc(since))
        jobs = [_job_dict(*row) for q in (queued, printed) for row in (await db.execute(q)).all()]
        return sorted(jobs, key=lambda j: (j["listed_at"] or datetime.min, j["id"]))
    recent = datetime.now(timezone.utc).replace(tzinfo=None) - RECENT
    query = query.where(
        or_(
            FarmPrintJob.status.in_(("waiting", "to_assign")),
            PrintQueueItem.status.in_(service.ACTIVE_QUEUE),
            FarmPrintJob.updated_at >= recent,
        )
    ).order_by(FarmPrintJob.id.desc())
    rows = await db.execute(query)
    return [_job_dict(job, status, printer, finished) for job, status, printer, finished in rows.all()]


@router.post("/jobs/{job_id}/assign", dependencies=[_create])
async def assign_job(job_id: int, data: AssignIn, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """A print outside the queue: the components and pieces it printed, or ``not_production``."""
    job = await db.get(FarmPrintJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    jobs = await outside.assign(db, job, [p.model_dump() for p in data.parts], data.not_production)
    printer = await db.get(Printer, job.printer_id) if job.printer_id else None
    return [_job_dict(j, None, printer.name if printer else None) for j in jobs]


@router.delete("/jobs/{job_id}", dependencies=[_delete])
async def cancel_job(job_id: int, request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    from backend.app.services.queue_failure import requester

    job = await db.get(FarmPrintJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    await service.cancel_job(db, job, requester(None, request))
    return {"id": job_id, "state": "cancelled"}
