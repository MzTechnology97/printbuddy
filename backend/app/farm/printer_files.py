"""Queue files on the printers: a content fingerprint in the name, reuse, clean-up (fork).

Upstream deletes and re-uploads the file at every queued print. Here the printer-side
name carries the first 8 hex of the file's SHA-256 (``Sopra__1a2b3c4d.3mf``): when the
printer already lists that name, the upload is skipped. The fingerprint is taken on the
file sent, so a project changed on the NAS gets a new name and is always uploaded.
Fingerprinted files not used for ``KEEP_DAYS`` are deleted from the printer; files
that do not carry a fingerprint (put there by hand or by the slicer) are never touched.
"""

from __future__ import annotations

import asyncio
import hashlib
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

KEEP_DAYS = 30
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
        remote_filename = re.sub(r"__[0-9a-f]{8}(?=\.)", "", remote_filename, count=1)
    for ext in _EXTENSIONS:
        if remote_filename.endswith(ext):
            return f"{remote_filename[: -len(ext)]}__{sha8}{ext}"
    return f"{remote_filename}__{sha8}"


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
        logger.info("Printer %s already has %s: reusing it", printer_id, remote_path)
        return True
    return False


async def before_upload(printer_id: int, remote_path: str) -> bool:
    """Record the use of a fingerprinted file; True when the upload can be skipped."""
    if not FINGERPRINT_RE.search(remote_path):
        return False
    async with database.async_session() as db:
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
    reuse = (printer_id, remote_path) in _reusable
    _reusable.discard((printer_id, remote_path))
    return reuse


async def cleanup(db: AsyncSession) -> int:
    """Delete fingerprinted files unused for KEEP_DAYS from idle printers; returns how many."""
    from backend.app.models.printer import Printer
    from backend.app.services.print_scheduler import scheduler
    from backend.app.services.printer_providers.factory import normalize_provider

    old = (
        await db.execute(
            select(FarmPrinterFile).where(FarmPrinterFile.last_used_at < _now() - timedelta(days=KEEP_DAYS))
        )
    ).scalars()
    deleted = 0
    for row in old:
        printer = await db.get(Printer, row.printer_id)
        if printer is None:
            await db.delete(row)
            continue
        if not scheduler._is_printer_idle(printer.id, require_plate_clear=False):
            continue  # never touch a printer that is working
        try:
            provider = normalize_provider(printer.provider)
            if provider == "bambu":
                from backend.app.services.bambu_ftp import delete_file_async

                await delete_file_async(
                    printer.ip_address, printer.access_code, row.remote_path, printer_model=printer.model
                )
            else:
                from backend.app.services.printer_manager import printer_manager

                client = printer_manager.get_client(printer.id)
                if client is None:
                    continue
                await asyncio.to_thread(client.delete_file, row.remote_path)
        except Exception as exc:
            logger.warning("Printer %s: could not delete old queue file %s: %s", printer.id, row.remote_path, exc)
            continue
        await db.delete(row)
        deleted += 1
    await db.commit()
    return deleted
