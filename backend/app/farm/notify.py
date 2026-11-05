"""Farm events sent as notifications: each one a provider toggle (``on_<event>``) and a template.

``notify`` returns at once: the send runs in the background with its own session, so a slice job
or the dispatcher tick never waits for a slow provider. The same event about the same thing
(``key``) goes out once, then again only after ``REPEAT_SECONDS`` (a slice retried every 30 minutes,
a printer still waiting for its plate); ``forget`` lets it go out earlier (the plate was cleared).

``BATCH_TO_ASSIGN`` is ready for Products' "lotto da assegnare":
``notify(BATCH_TO_ASSIGN, key, batch=..., detail=...)``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Hashable

from backend.app.core import database

logger = logging.getLogger(__name__)

SLICING_FAILED = "farm_slicing_failed"
FILE_CORRUPT = "farm_file_corrupt"
LIBRARY_PROBLEM = "farm_library_problem"
PLATE_TO_CLEAR = "farm_plate_to_clear"
COMPONENT_URGENT = "farm_component_urgent"
BATCH_TO_ASSIGN = "farm_batch_to_assign"

QUEUE_SLICE = "Slicing per la coda"
AUTO_RESLICE = "Re-slice automatico"

REPEAT_SECONDS = 12 * 3600
# shortcut: in memory, so a restart may send a pending event once more; persist it if that is noisy.
_sent: dict[tuple[str, Hashable], float] = {}  # (event, key) -> when a provider got it
_sending: set[tuple[str, Hashable]] = set()  # (event, key) being sent now
_tasks: set[asyncio.Task] = set()


def notify(
    event: str, key: Hashable, *, printer_id: int | None = None, printer_name: str | None = None, **variables
) -> asyncio.Task | None:
    """Send ``event`` to the providers that want it, unless it reached one for ``key`` lately; never raises.

    Only a delivery counts: with the event off everywhere, or a failed send, the next call tries again.
    """
    if (event, key) in _sending or time.monotonic() - _sent.get((event, key), -REPEAT_SECONDS) < REPEAT_SECONDS:
        return None
    _sending.add((event, key))
    values = {name: "" if value is None else str(value) for name, value in variables.items()}
    task = asyncio.create_task(_deliver(event, key, values, printer_id, printer_name), name=f"farm-notify-{event}")
    _tasks.add(task)  # keep a reference until it ends
    task.add_done_callback(_tasks.discard)
    return task


def forget(event: str, key: Hashable) -> None:
    """The condition is over: the next time it happens is notified at once."""
    _sent.pop((event, key), None)


def forget_others(event: str, keep: set) -> None:
    """``forget`` every key of ``event`` not in ``keep`` (conditions no longer found)."""
    for sent_event, key in [k for k in _sent if k[0] == event and k[1] not in keep]:
        _sent.pop((sent_event, key), None)


async def _deliver(event: str, key: Hashable, values: dict[str, str], printer_id, printer_name) -> None:
    try:
        if await _send(event, values, printer_id, printer_name):
            _sent[(event, key)] = time.monotonic()
    finally:
        _sending.discard((event, key))


def cancel_pending() -> None:
    """At shutdown: sends still running are dropped."""
    for task in list(_tasks):
        task.cancel()


def slicing_done(file_id: int, filename: str, plate: int | None, colour: str | None, source: str, error=None) -> None:
    """A background slice ended: a failure is notified (once per plate and colour), a success clears it."""
    key = (file_id, plate or 1, (colour or "").strip().lower() or None)
    if error is None:
        forget(SLICING_FAILED, key)
        return
    reason = getattr(error, "detail", None) or f"{type(error).__name__}: {error}"
    notify(SLICING_FAILED, key, filename=filename, plate=plate or 1, color=colour, source=source, reason=reason)


async def _send(event: str, variables: dict[str, str], printer_id: int | None, printer_name: str | None) -> bool:
    """True when at least one provider got it (or is retrying it)."""
    from backend.app.services.notification_service import notification_service

    try:
        async with database.async_session() as db:
            return await notification_service.on_farm_event(
                event, variables, db, printer_id=printer_id, printer_name=printer_name
            )
    except Exception:
        logger.exception("Farm notification %s could not be sent", event)
        return False
