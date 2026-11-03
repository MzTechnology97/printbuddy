"""Farm API: Accoda (phase 4) jobs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.jobs import service
from backend.app.farm.jobs.models import FarmPrintJob
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


class JobsIn(BaseModel):
    jobs: list[JobIn] = Field(min_length=1, max_length=100)


def _job_dict(job: FarmPrintJob, item_status: str | None, printer: str | None) -> dict:
    return {
        "id": job.id,
        "component_code": job.component_code,
        "color": job.color,
        # waiting -> pending (in the queue, waiting for the plate-clear OK) -> printing -> completed/failed
        "state": job.status if job.status != "queued" else (item_status or "removed"),
        "note": job.note,
        "plate_ids": job.plate_ids,
        "plate_id": job.plate_id,
        "printer_id": job.printer_id,
        "printer": printer,
        "queue_item_id": job.queue_item_id,
        "pieces": job.pieces if job.pieces is not None else job.expected_pieces,
        "created_at": job.created_at,
    }


@router.post("/jobs", dependencies=[_create])
async def create_jobs(data: JobsIn, db: AsyncSession = Depends(get_db)) -> list[dict]:
    jobs = await service.create_jobs(db, [j.model_dump() for j in data.jobs])
    if any(j.printer_id for j in jobs):
        await service.dispatch_once(db)  # a chosen printer's queue gets it now, not at the next tick
    return [_job_dict(j, "pending" if j.status == "queued" else None, None) for j in jobs]


@router.get("/jobs", dependencies=[_read])
async def list_jobs(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Jobs still waiting or in the queue, plus those of the last two days."""
    since = datetime.now(timezone.utc).replace(tzinfo=None) - RECENT
    rows = await db.execute(
        select(FarmPrintJob, PrintQueueItem.status, Printer.name)
        .outerjoin(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .outerjoin(Printer, Printer.id == FarmPrintJob.printer_id)
        .where(
            or_(
                FarmPrintJob.status == "waiting",
                PrintQueueItem.status.in_(service.ACTIVE_QUEUE),
                FarmPrintJob.updated_at >= since,
            )
        )
        .order_by(FarmPrintJob.id.desc())
    )
    return [_job_dict(job, status, printer) for job, status, printer in rows.all()]


@router.delete("/jobs/{job_id}", dependencies=[_delete])
async def cancel_job(job_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    job = await db.get(FarmPrintJob, job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    await service.cancel_job(db, job)
    return {"id": job_id, "state": "cancelled"}
