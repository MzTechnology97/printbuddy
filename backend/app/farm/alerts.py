"""Farm conditions checked on the dispatcher tick and notified (``farm.notify``).

* Plate to clear: a printer finished, waits for the plate-clear OK, and farm work waits for it
  (a farm print in its queue, or a waiting job with a plate for its model).
* Urgent component: Seller Hub marks it ``esaurito``/``urgente`` and no free printer has the
  material and colour of one of its plates loaded.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import database
from backend.app.farm import notify
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.plan import service as plan
from backend.app.farm.plates.models import FarmPlate
from backend.app.farm.plates.printer_map import PrinterModelResolver
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.print_scheduler import scheduler
from backend.app.services.printer_manager import printer_manager

logger = logging.getLogger(__name__)

URGENT_EVERY_SECONDS = 10 * 60
URGENT = ("esaurito", "urgente")
_last_urgent_check = -float(URGENT_EVERY_SECONDS)
_running: asyncio.Task | None = None


def _label(job: FarmPrintJob) -> str:
    return f"{job.component_code} {job.color or ''}".strip()


async def plates_to_clear(db: AsyncSession) -> None:
    printers = (await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars().all()
    held = [
        p
        for p in printers
        if printer_manager.is_awaiting_plate_clear(p.id) and scheduler._is_printer_idle(p.id, require_plate_clear=False)
    ]
    for p in printers:
        if p not in held:
            notify.forget(notify.PLATE_TO_CLEAR, p.id)  # cleared (or busy): the next finish notifies again
    if not held:
        return
    waiting: dict[int, list[str]] = {p.id: [] for p in held}
    queued = await db.execute(
        select(FarmPrintJob, PrintQueueItem.printer_id)
        .join(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .where(FarmPrintJob.status == "queued", PrintQueueItem.status == "pending")
    )
    for job, printer_id in queued.all():
        if printer_id in waiting:
            waiting[printer_id].append(_label(job))
    jobs = (await db.execute(select(FarmPrintJob).where(FarmPrintJob.status == "waiting"))).scalars().all()
    if jobs:
        resolver = await PrinterModelResolver.load(db)
        for job in jobs:
            plates = [await db.get(FarmPlate, plate_id) for plate_id in job.plate_ids]
            models = {
                (resolver.resolve(pl.printer_model).target_model or pl.printer_model or "").lower()
                for pl in plates
                if pl is not None
            }
            for p in held:
                if (job.printer_id in (None, p.id)) and (p.model or "").lower() in models:
                    waiting[p.id].append(_label(job))
    for p in held:
        if waiting[p.id]:
            names = list(dict.fromkeys(waiting[p.id]))
            shown = ", ".join(names[:5]) + (f" (+{len(names) - 5})" if len(names) > 5 else "")
            notify.notify(
                notify.PLATE_TO_CLEAR, p.id, printer_id=p.id, printer_name=p.name, printer=p.name, waiting=shown
            )


async def urgent_components(db: AsyncSession) -> None:
    from backend.app.farm.jobs.service import queued_pieces

    found = await plan.build_plan(db, await queued_pieces(db))
    if found["error"] or found["fetched_at"] is None:
        return  # Seller Hub didn't answer now: an old copy is not judged
    stuck_codes = set()
    for row in found["components"]:
        stuck = (
            row["status"] in URGENT
            and row["plates"]
            and not any(f["idle"] and f["material_ok"] and f["color_ok"] for o in row["plates"] for f in o["printers"])
        )
        if not stuck:
            continue
        stuck_codes.add(row["code"])
        notify.notify(
            notify.COMPONENT_URGENT,
            row["code"],
            component=row["code"],
            name=row["name"],
            status=row["status"],
            missing=row["to_print"],
            material=" / ".join(dict.fromkeys(o["material"] for o in row["plates"] if o["material"])) or "il materiale",
            color=row["color"],
        )
    # Not stuck any more, or gone from the plan (nothing left to print): if it comes back, notify at once.
    notify.forget_others(notify.COMPONENT_URGENT, stuck_codes)


def start_check() -> None:
    """``check`` in its own task and session, one at a time: a slow Seller Hub never holds the dispatcher."""
    global _running
    if _running is None or _running.done():
        _running = asyncio.create_task(_check_alone(), name="farm-alerts")


def stop_check() -> None:
    if _running is not None:
        _running.cancel()


async def _check_alone() -> None:
    async with database.async_session() as db:
        await check(db)


async def check(db: AsyncSession) -> None:
    """Run on every dispatcher tick; the plan (Seller Hub) is read every ``URGENT_EVERY_SECONDS``."""
    global _last_urgent_check
    try:
        await plates_to_clear(db)
    except Exception:
        logger.exception("Farm alerts: plate-to-clear check failed")
    if time.monotonic() - _last_urgent_check >= URGENT_EVERY_SECONDS:
        _last_urgent_check = time.monotonic()
        try:
            await urgent_components(db)
        except Exception:
            logger.exception("Farm alerts: urgent-component check failed")
