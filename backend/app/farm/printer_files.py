"""Queue files on the printers: a content fingerprint in the name, reuse, clean-up (fork).

Upstream deletes and re-uploads the file at every queued print. Here the printer-side
name carries the first 8 hex of the file's SHA-256 (``Sopra__1a2b3c4d.3mf``): when the
printer already lists that name, the upload is skipped. The fingerprint is taken on the
file sent, so a project changed on the NAS gets a new name and is always uploaded.
Files Printbuddy put on a printer (queue, reprints, prints outside the queue) and not used
for ``keep_days`` (setting, 14 by default) are deleted from idle printers; files it did not
put there (by hand or by the slicer) are never touched.

Upstream deletes the file from the SD as soon as a print ends (upstream #374: some printers
restart the last file on power-up), which breaks reprints from the printer's display. A
printer set to keep its files skips that delete; the clean-up above removes them later.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core import database
from backend.app.core.database import Base

logger = logging.getLogger(__name__)

KEEP_DAYS = 14  # default of the farm_sd_keep_days setting
KEEP_DAYS_KEY = "farm_sd_keep_days"
KEEP_PRINTERS_KEY = "farm_sd_keep_printers"  # JSON list of printer ids that keep their files after a print
# Reported to start the last print again on power-up while its file is on the SD (upstream #374:
# P1S, and an A1 with "SD card data damaged"); not reproduced on X1C or H2D.
GHOST_PRINT_MODELS = frozenset({"P1P", "P1S", "A1", "A1MINI"})
FINGERPRINT_RE = re.compile(r"__[0-9a-f]{8}(\.gcode\.3mf|\.3mf|\.gcode)$")
_EXTENSIONS = (".gcode.3mf", ".3mf", ".gcode")
_reusable: set[tuple[int, str]] = set()  # (printer_id, remote_path) found on the printer before upload


class FarmPrinterFile(Base):
    """A fingerprinted file Printbuddy put on a printer, and when a queued print last used it."""

    __tablename__ = "farm_printer_files"
    __table_args__ = (UniqueConstraint("printer_id", "remote_path"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    printer_id: Mapped[int] = mapped_column(Integer, index=True)
    remote_path: Mapped[str] = mapped_column(String(500))
    last_used_at: Mapped[datetime] = mapped_column(DateTime)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def with_fingerprint(remote_filename: str, data: bytes) -> str:
    sha8 = hashlib.sha256(data).hexdigest()[:8]
    if FINGERPRINT_RE.search(remote_filename):
        remote_filename = without_fingerprint(remote_filename)
    for ext in _EXTENSIONS:
        if remote_filename.endswith(ext):
            return f"{remote_filename[: -len(ext)]}__{sha8}{ext}"
    return f"{remote_filename}__{sha8}"


def without_fingerprint(name: str) -> str:
    """The name a file had before ``fingerprint`` added ``__<sha8>`` (before its extension, or at the end):
    the name its archive and its library file know it by."""
    return re.sub(r"__[0-9a-f]{8}(?=\.|$)", "", name, count=1)


async def fingerprint(remote_filename: str, file_path: Path) -> str:
    """The printer-side name with the fingerprint of the file about to be sent."""
    data = await asyncio.to_thread(file_path.read_bytes)
    return with_fingerprint(remote_filename, data)


async def _list_names(printer_id: int, provider: str, ip: str, access_code: str, model: str | None) -> set[str]:
    if provider == "bambu":
        from backend.app.services.bambu_ftp import list_files_async

        entries = await list_files_async(ip, access_code, "/", printer_model=model)
    else:
        from backend.app.services.printer_manager import printer_manager

        client = printer_manager.get_client(printer_id)
        if client is None or not hasattr(client, "list_files"):
            return set()
        entries = await asyncio.to_thread(client.list_files, "/")
    names = set()
    for e in entries or []:
        name = str(e.get("name") or e.get("path") or e.get("filename") or "")
        names.add(name.rsplit("/", 1)[-1])
    return names


async def check_reusable(
    printer_id: int, provider: str, ip: str, access_code: str, remote_path: str, model: str | None
) -> bool:
    """True when the printer already has this fingerprinted file (the delete before upload is then skipped)."""
    if not FINGERPRINT_RE.search(remote_path):
        return False
    try:
        names = await _list_names(printer_id, provider, ip, access_code, model)
    except Exception as exc:  # listing is an optimisation: on any error upload as upstream does
        logger.debug("Printer %s: file list failed, uploading %s: %s", printer_id, remote_path, exc)
        return False
    if remote_path.rsplit("/", 1)[-1] in names:
        _reusable.add((printer_id, remote_path))
        await _record(printer_id, remote_path)  # used now: the clean-up must not take it away meanwhile
        logger.info("Printer %s already has %s: reusing it", printer_id, remote_path)
        return True
    return False


def ghost_print_risk(model: str | None) -> bool:
    return (model or "").upper().replace(" ", "").replace("-", "") in GHOST_PRINT_MODELS


async def keep_days(db: AsyncSession) -> int:
    from backend.app.api.routes.settings import get_setting

    try:
        return max(1, int(await get_setting(db, KEEP_DAYS_KEY) or KEEP_DAYS))
    except ValueError:
        return KEEP_DAYS


async def kept_printers(db: AsyncSession) -> set[int]:
    from backend.app.api.routes.settings import get_setting

    try:
        return {int(i) for i in json.loads(await get_setting(db, KEEP_PRINTERS_KEY) or "[]")}
    except (ValueError, TypeError):
        return set()


async def keeps_files(printer_id: int) -> bool:
    """Whether this printer keeps its files on the SD after a print (reprint from the display)."""
    async with database.async_session() as db:
        return printer_id in await kept_printers(db)


async def save_settings(db: AsyncSession, days: int, printer_ids: set[int]) -> None:
    from backend.app.api.routes.settings import set_setting

    await set_setting(db, KEEP_DAYS_KEY, str(days))
    await set_setting(db, KEEP_PRINTERS_KEY, json.dumps(sorted(printer_ids)))
    await db.commit()


async def _record(printer_id: int, remote_path: str, *, plain_if_kept: bool = False) -> None:
    """Mark the file used now. Bookkeeping only: a failure is logged and never stops an upload.

    Fingerprinted names are always recorded (queue files, as before). With ``plain_if_kept`` a plain name
    (archive reprint, print outside the queue) is recorded too, but only on a printer that keeps its files:
    elsewhere upstream already deletes it after the print, and Klipper is left as it was.
    """
    try:
        async with database.async_session() as db:
            if not FINGERPRINT_RE.search(remote_path) and not (plain_if_kept and printer_id in await kept_printers(db)):
                return
            row = await db.scalar(
                select(FarmPrinterFile).where(
                    FarmPrinterFile.printer_id == printer_id, FarmPrinterFile.remote_path == remote_path
                )
            )
            if row is None:
                row = FarmPrinterFile(printer_id=printer_id, remote_path=remote_path)
                db.add(row)
            row.last_used_at = _now()
            await db.commit()
    except Exception as exc:
        logger.warning("Printer %s: could not record %s for the clean-up: %s", printer_id, remote_path, exc)


async def before_upload(printer_id: int, remote_path: str) -> bool:
    """Record a file Printbuddy puts on a printer (for the clean-up); True when the upload can be skipped.

    Only fingerprinted names are ever reused.
    """
    await _record(printer_id, remote_path, plain_if_kept=True)
    reuse = (printer_id, remote_path) in _reusable
    _reusable.discard((printer_id, remote_path))
    return reuse


async def _busy_printers(db: AsyncSession) -> set[int]:
    """Printers with a queue item printing or being sent: their files are not touched."""
    from backend.app.models.print_queue import PrintQueueItem

    rows = await db.execute(
        select(PrintQueueItem.printer_id).where(
            (PrintQueueItem.status == "printing")
            | ((PrintQueueItem.status == "pending") & PrintQueueItem.dispatching_at.is_not(None))
        )
    )
    return {pid for (pid,) in rows if pid is not None}


async def _listing(printer, provider: str) -> set[str] | None:
    """The file names at the printer's root; None when it can't be listed now (offline, no support, error)."""
    if provider != "bambu":
        from backend.app.services.printer_manager import printer_manager

        client = printer_manager.get_client(printer.id)
        if client is None or not hasattr(client, "list_files"):
            return None
    try:
        return await _list_names(printer.id, provider, printer.ip_address, printer.access_code, printer.model)
    except Exception as exc:
        logger.debug("Printer %s: file list failed, clean-up skipped: %s", printer.id, exc)
        return None


async def _delete(printer, provider: str, remote_path: str) -> bool:
    if provider == "bambu":
        from backend.app.services.bambu_ftp import delete_file_async

        return bool(
            await delete_file_async(printer.ip_address, printer.access_code, remote_path, printer_model=printer.model)
        )
    from backend.app.services.printer_manager import printer_manager

    client = printer_manager.get_client(printer.id)
    if client is None:
        return False
    return await asyncio.to_thread(client.delete_file, remote_path) is not False


async def cleanup(db: AsyncSession) -> int:
    """Delete files Printbuddy put on idle printers and unused for ``keep_days``; returns how many.

    Before each delete the printer's queue and the file's last use are read again: a queued print may
    have picked the file up (reuse) while the earlier deletes were running. A file no longer on the
    printer is forgotten; one that could not be deleted is kept and tried again next time.
    """
    from backend.app.models.printer import Printer
    from backend.app.services.print_scheduler import scheduler
    from backend.app.services.printer_providers.factory import normalize_provider

    cutoff = _now() - timedelta(days=await keep_days(db))
    old = (await db.execute(select(FarmPrinterFile).where(FarmPrinterFile.last_used_at < cutoff))).scalars().all()
    listed: dict[int, set[str] | None] = {}
    deleted = 0
    for row in old:
        printer = await db.get(Printer, row.printer_id)
        if printer is None:
            await db.delete(row)
            continue
        if not scheduler._is_printer_idle(printer.id, require_plate_clear=False):
            continue  # never touch a printer that is working
        provider = normalize_provider(printer.provider)
        if printer.id not in listed:
            listed[printer.id] = await _listing(printer, provider)
        names = listed[printer.id]
        if names is None:
            continue
        await db.refresh(row)
        if printer.id in await _busy_printers(db) or row.last_used_at >= cutoff:
            continue  # printing, being sent, or picked up again since the list was read
        if row.remote_path.rsplit("/", 1)[-1] in names:
            try:
                ok = await _delete(printer, provider, row.remote_path)
            except Exception as exc:
                ok = False
                logger.warning("Printer %s: could not delete old file %s: %s", printer.id, row.remote_path, exc)
            if not ok:
                continue  # still on the printer: keep the record, try again next time
            deleted += 1
        await db.delete(row)
    await db.commit()
    return deleted
