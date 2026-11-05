"""What happened to each print, step by step: the queue page's timeline and debug details (fork).

``record()`` is called from the flow (Accoda, slicing, the dispatcher, the scheduler, the end of a print)
and never blocks it: the event goes to a list in memory and a background task writes the list every
second in its own session. With SQLite a write from the caller's own transaction would wait for the very
lock that transaction holds; here a failed write is logged and tried again, the flow never sees it.

An event belongs to a farm job (``job_id``) and/or a queue item (``queue_item_id``; the job is looked up
when writing); slice events carry the file, plate and colour instead, since a slice serves every job of
that plate. Old events go with the archive's own clean-up days (90 when that is off).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import JSON, DateTime, Integer, String, delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core import database
from backend.app.core.database import Base

logger = logging.getLogger(__name__)

KEEP_DAYS = 90  # when the archive's own clean-up is off
FLUSH_SECONDS = 1.0
MAX_PENDING = 2000  # events kept while the database can't be written; the oldest go first
MESSAGE_MAX = 300
TEXT_MAX = 4000  # any single text in the details (a slicer's error, upload log lines...)

ERROR_PHASES = {"slice_failed", "upload_failed", "start_failed", "dispatch_failed", "failed"}


class FarmJobEvent(Base):
    __tablename__ = "farm_job_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    queue_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    archive_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    printer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # slice events: the plate they are of
    file_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    plate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    colour: Mapped[str | None] = mapped_column(String(50), nullable=True)
    phase: Mapped[str] = mapped_column(String(24))
    level: Mapped[str] = mapped_column(String(8), default="info")  # info | warning | error
    message: Mapped[str] = mapped_column(String(MESSAGE_MAX))
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


_pending: list[dict] = []
_task: asyncio.Task | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _short(value):
    """Texts cut to ``TEXT_MAX``, lists and dicts walked: a detail never grows without bound."""
    if isinstance(value, str):
        return value if len(value) <= TEXT_MAX else value[:TEXT_MAX] + "…"
    if isinstance(value, dict):
        return {str(k): _short(v) for k, v in value.items() if v not in (None, "", [], {})}
    if isinstance(value, (list, tuple)):
        return [_short(v) for v in list(value)[:200]]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return str(value)


def record(
    phase: str,
    message: str,
    *,
    job_id: int | None = None,
    queue_item_id: int | None = None,
    archive_id: int | None = None,
    printer_id: int | None = None,
    file_id: int | None = None,
    plate: int | None = None,
    colour: str | None = None,
    level: str | None = None,
    detail: dict | None = None,
) -> None:
    """Note an event; returns at once and never raises (written within a second by the background task)."""
    try:
        _pending.append(
            {
                "at": _now(),
                "phase": phase[:24],
                "level": level or ("error" if phase in ERROR_PHASES else "info"),
                "message": (message or phase)[:MESSAGE_MAX],
                "job_id": job_id,
                "queue_item_id": queue_item_id,
                "archive_id": archive_id,
                "printer_id": printer_id,
                "file_id": file_id,
                "plate": plate,
                "colour": colour,
                "detail": _short(detail) if detail else None,
            }
        )
        if len(_pending) > MAX_PENDING:
            del _pending[: len(_pending) - MAX_PENDING]
        _ensure_writer()
    except Exception:  # pragma: no cover - the flow must never notice
        logger.debug("Job event not recorded", exc_info=True)


def _ensure_writer() -> None:
    global _task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (a sync caller in a thread): written by the next record() made on the loop
    if _task is None or _task.done():
        _task = loop.create_task(_writer())


async def _writer() -> None:
    while True:
        await asyncio.sleep(FLUSH_SECONDS)
        await flush()


async def flush() -> int:
    """Write what is pending; on a failure it stays pending for the next try. Returns how many were written."""
    if not _pending:
        return 0
    batch = list(_pending)
    try:
        from backend.app.farm.jobs.models import FarmPrintJob

        async with database.async_session() as db:
            items = {e["queue_item_id"] for e in batch if e["queue_item_id"] and not e["job_id"]}
            owner: dict[int, int] = {}
            if items:
                rows = await db.execute(
                    select(FarmPrintJob.queue_item_id, FarmPrintJob.id).where(FarmPrintJob.queue_item_id.in_(items))
                )
                owner = dict(rows.all())
            for e in batch:
                db.add(FarmJobEvent(**{**e, "job_id": e["job_id"] or owner.get(e["queue_item_id"])}))
            await db.commit()
    except Exception as exc:
        logger.debug("Job events not written yet (%d pending): %s", len(_pending), exc)
        return 0
    del _pending[: len(batch)]
    return len(batch)


async def stop() -> None:
    """At shutdown: the writer stops and what is pending is written once more."""
    global _task
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await _task
        _task = None
    with contextlib.suppress(Exception):
        await asyncio.wait_for(flush(), 5)


async def keep_days(db: AsyncSession) -> int:
    """The archive's clean-up days when that is on, else ``KEEP_DAYS``."""
    try:
        from backend.app.services.archive_purge import archive_purge_service

        settings = await archive_purge_service.get_settings(db)
        if settings.get("enabled") and int(settings.get("days") or 0) > 0:
            return int(settings["days"])
    except Exception:
        logger.debug("Archive clean-up settings not read", exc_info=True)
    return KEEP_DAYS


async def cleanup(db: AsyncSession) -> int:
    started = time.monotonic()
    cutoff = _now() - timedelta(days=await keep_days(db))
    result = await db.execute(delete(FarmJobEvent).where(FarmJobEvent.at < cutoff))
    await db.commit()
    if result.rowcount:
        logger.info("Job events: %d older than %s removed (%.1fs)", result.rowcount, cutoff, time.monotonic() - started)
    return result.rowcount or 0
