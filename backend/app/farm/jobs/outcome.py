"""A farm job never loses how its print ended (fork).

A queued job's state comes from its queue item. When that item is deleted (one, several, "clear history") the
job keeps the item's end on itself first: ``outcome`` (completed / failed / cancelled), ``finished_at``, the
printer and the archive (its printed pieces are already on it). ``/farm/jobs`` and the queue board read those
once the item is gone, so Seller Hub's batches to check never vanish.

Every ORM delete goes through ``before_flush``; a bulk SQL delete must call ``keep_outcomes`` before it.
"""

from __future__ import annotations

import logging

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.models.print_queue import PrintQueueItem

logger = logging.getLogger(__name__)

# The queue's end states and the outcome a job keeps ("skipped": it never printed)
OUTCOMES = {"completed": "completed", "failed": "failed", "cancelled": "cancelled", "skipped": "cancelled"}


def _keep(job: FarmPrintJob, item: PrintQueueItem) -> None:
    outcome = OUTCOMES.get(item.status)
    if outcome is None:  # still pending: nothing printed, the job just loses its place ("removed")
        return
    job.outcome = outcome
    end = item.completed_at
    job.finished_at = job.finished_at or (end.replace(tzinfo=None) if end is not None else None)  # stored UTC
    job.printer_id = job.printer_id or item.printer_id
    job.archive_id = job.archive_id or item.archive_id
    logger.info("Farm job %d keeps its outcome %s: queue item %d deleted", job.id, outcome, item.id)


@event.listens_for(Session, "before_flush")
def _before_flush(session: Session, flush_context, instances) -> None:
    items = {o.id: o for o in session.deleted if isinstance(o, PrintQueueItem) and o.id is not None}
    if not items:
        return
    with session.no_autoflush:
        jobs = session.execute(select(FarmPrintJob).where(FarmPrintJob.queue_item_id.in_(items))).scalars()
        for job in jobs:
            _keep(job, items[job.queue_item_id])


async def queue_print_archive(db: AsyncSession, printer_id: int) -> int | None:
    """The archive of the queue print this printer is printing, for the end of the print to close it.

    The printer reports the name the file was sent under, which carries its fingerprint (``__<sha8>``,
    ``farm.printer_files``): the archive, made at the start, knows the library name, so names don't match.
    """
    from backend.app.models.archive import PrintArchive

    return await db.scalar(
        select(PrintQueueItem.archive_id)
        .join(PrintArchive, PrintArchive.id == PrintQueueItem.archive_id)
        .where(PrintQueueItem.printer_id == printer_id)
        .where(PrintQueueItem.status == "printing")
        .where(PrintArchive.status == "printing")
        .order_by(PrintQueueItem.id.desc())
        .limit(1)
    )


async def close_stale_archives(db: AsyncSession) -> int:
    """Archives still "printing" whose queue item has ended: closed with the item's end (status, time, reason).

    Before this fix the end of a print sent under a fingerprinted name (``__<sha8>``) didn't find its archive, which
    stayed "printing" (out of the history's ends and the statistics). Run at start and once a day; the print
    is over when its queue item is, so nothing still printing is touched.
    """
    from backend.app.models.archive import PrintArchive
    from backend.app.services.archive import ArchiveService

    rows = (
        await db.execute(
            select(PrintArchive.id, PrintQueueItem.status, PrintQueueItem.completed_at, PrintQueueItem.error_message)
            .join(PrintQueueItem, PrintQueueItem.archive_id == PrintArchive.id)
            .where(PrintArchive.status == "printing", PrintQueueItem.status.in_(OUTCOMES))
        )
    ).all()
    service = ArchiveService(db)
    for archive_id, status, ended, error in rows:
        archive_status = OUTCOMES[status]
        await service.update_archive_status(
            archive_id,
            status=archive_status,
            completed_at=ended,
            failure_reason=(error or "")[:100] or None if archive_status == "failed" else None,
        )
        logger.info("Archive %d was left printing: closed as %s (its queue item ended)", archive_id, archive_status)
    if rows:
        await db.commit()
    return len(rows)


async def keep_outcomes(db: AsyncSession, where) -> None:
    """Before a bulk ``delete(PrintQueueItem).where(where)``: the jobs of those items keep their outcome."""
    rows = await db.execute(
        select(FarmPrintJob, PrintQueueItem)
        .join(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .where(where)
    )
    for job, item in rows.all():
        _keep(job, item)
    await db.flush()
