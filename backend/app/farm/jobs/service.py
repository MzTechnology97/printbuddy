"""Accoda: farm jobs with alternative plates, turned into upstream queue items on a free printer.

The upstream queue item has one file and one printer. A farm job keeps the list of plates
(one per printer model, usually); every tick the dispatcher looks for an idle printer of a
model that has one of them sliced, with the plate's material and the component's colour
loaded, and only then creates the queue item, pinned to that printer, with the colour of
the loaded spool forced. Starting it stays with the upstream scheduler, so its plate-clear
confirmation still applies: nothing prints without the user saying the plate is clear.

A job created for a printer the user chose (``printer_id`` set while waiting) goes into that
printer's queue as soon as its plate is sliced, busy or not, and with a manual start when the
loaded spool can't be matched: the user picked the printer, the upstream queue waits for it.

Slots are mapped here, not left to the upstream scheduler: each filament the sliced plate really
uses (its slice_info) goes on a loaded AMS/CFS tray of the COMPONENT's colour and a compatible
material, the others to -1. The colour written in the 3MF doesn't count. No such tray: the job
waits (a chosen printer gets it for a manual start).

The print dialog's choices ride on the job (``queue_options``) to the queue item: AMS/CFS slots
chosen by hand (then no spool check and no forced colour), start time, manual start and the
other upstream queue flags. A job for the first free printer waits for its start time here,
so it doesn't hold a printer; one for a chosen printer waits in that printer's queue.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import logging
import os
import re
import time
import uuid
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import database
from backend.app.core.config import settings
from backend.app.farm import notify
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plan import service as plan
from backend.app.farm.plan.models import FarmPrinterSpool
from backend.app.farm.plates.models import FarmPlate
from backend.app.farm.plates.printer_map import PrinterModelResolver
from backend.app.farm.plates.service import _latest_slices, match_object
from backend.app.farm.products.folders import component_files, excluded_files, may_print
from backend.app.farm.products.models import FarmComponent
from backend.app.farm.products.shapes import ProductShapes, plate_yield, product_shapes
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services import queue_failure
from backend.app.services.filament_requirements import extract_filament_requirements
from backend.app.services.print_scheduler import _canonical_filament_type, scheduler
from backend.app.services.printer_manager import printer_manager
from backend.app.services.printer_providers.factory import normalize_provider

logger = logging.getLogger(__name__)

from backend.app.farm import job_events  # noqa: E402  fork: the queue page's timeline
from backend.app.farm.jobs import outcome  # noqa: E402  a job keeps its outcome when its queue item goes

TICK_SECONDS = 30
HISTORY_EVERY_SECONDS = 10 * 60  # Klipper prints done while Printbuddy was off
CLEANUP_EVERY_SECONDS = 24 * 3600  # old queue files on the printers
RESLICE_AFTER_SECONDS = 30 * 60  # a failed slice is tried again after this
ACTIVE_QUEUE = ("pending", "printing")
EXTRACTED_VERSION = 2  # 2: with the plate's preview and layer info; older extractions are redone
QUEUE_FLAGS = ("manual_start", "require_previous_success", "auto_off_after", "gcode_injection", "ignore_spool")
USED_COLORS_KEY = "farm_used_colors"  # in a print's archive extra_data: {colour: samples taken while printing}
SURE_SAMPLES = 10  # a colour picks between components only after 5 minutes of samples...
SURE_SHARE = 0.9  # ...nearly all of them that colour
PRINT_MAX_AGE = 7 * 24 * 3600  # an archive still "printing" older than this is a leftover, not the print
HEX_RE = re.compile(r"[0-9a-f]{6}|[0-9a-f]{3}")
_slicing: dict[tuple[int, int], float] = {}  # (library_file_id, plate) -> when slicing was asked


def _pieces(plate: FarmPlate, comp: FarmComponent, own: dict[int, ProductShapes]) -> int:
    return plate_yield(comp, plate, own)


async def sliced_file(db: AsyncSession, plate: FarmPlate) -> LibraryFile | None:
    """The sliced output of this plate for the current version of its project, if any."""
    lib_file = await db.get(LibraryFile, plate.library_file_id)
    state = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == plate.library_file_id))
    if lib_file is None or state is None:
        return None
    return (await _latest_slices(db, lib_file, state.sha256)).get(plate.plate_index)


async def ask_slicing(db: AsyncSession, plate: FarmPlate, from_nas: bool = False) -> bool:
    """Slice the plate on its slicer's sidecar in the background (once; again after a while if it failed).

    True when a slice was queued now.
    """
    key = (plate.library_file_id, plate.plate_index)
    if time.monotonic() - _slicing.get(key, -RESLICE_AFTER_SECONDS) < RESLICE_AFTER_SECONDS:
        return False
    _slicing[key] = time.monotonic()
    from backend.app.farm.slicing import _queue_key, _slice_jobs, queue_slice_ended, slice_embedded
    from backend.app.services.slice_dispatch import http_exception_to_job_error, slice_dispatch

    lib_file = await db.get(LibraryFile, plate.library_file_id)
    if lib_file is None:
        return False
    name = lib_file.filename

    async def run(job_id: int):
        async with database.async_session() as task_db:
            task_file = await task_db.get(LibraryFile, key[0])
            state = await task_db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == key[0]))
            try:
                response = await slice_embedded(
                    task_db, lib_file=task_file, state=state, plate=key[1], job_id=job_id, from_nas=from_nas
                )
            except Exception as exc:
                queue_slice_ended(key[0], name, key[1], None, state.slicer if state else None, exc)
                if isinstance(exc, HTTPException):
                    raise http_exception_to_job_error(exc) from exc
                raise
        queue_slice_ended(key[0], name, key[1], None, state.slicer if state else None)
        return response.model_dump()

    task = await slice_dispatch.enqueue(kind="library_file", source_id=key[0], source_name=name, run=run)
    if getattr(task, "id", None):  # its % on the queue board
        _slice_jobs[_queue_key(key[0], key[1], None)] = task.id
    job_events.record(
        "slice_requested",
        f"Slice asked: {name}, plate {key[1]}",
        file_id=key[0],
        plate=key[1],
        detail={"slice_job": getattr(task, "id", None), "file": name},
    )
    return True


async def variant_file(db: AsyncSession, plate: FarmPlate, color: str | None) -> LibraryFile | None:
    """The plate's sliced output in the component's colour (``farm.slicing``); None until sliced."""
    from backend.app.farm.slicing import variant_slice

    return await variant_slice(db, plate, color)


async def ask_variant(db: AsyncSession, plate: FarmPlate, color: str | None) -> str:
    """Ask for the plate's slice in the component's colour; the job's note: being sliced, or why not yet."""
    from backend.app.farm.slicing import queue_slice_note

    return await queue_slice_note(db, plate, color)


def _plate_model(resolver: PrinterModelResolver, plate: FarmPlate) -> str:
    return (resolver.resolve(plate.printer_model).target_model or plate.printer_model or "").lower()


def _utc(value: datetime | str | None) -> datetime | None:
    """Naive UTC, as upstream stores ``scheduled_time``."""
    if not value:
        return None
    when = datetime.fromisoformat(value) if isinstance(value, str) else value
    return when.astimezone(timezone.utc).replace(tzinfo=None) if when.tzinfo else when


def _queue_options(req: dict, printer_id: int | None, plates: list[FarmPlate]) -> dict | None:
    options = {k: True for k in QUEUE_FLAGS if req.get(k)}
    if when := _utc(req.get("scheduled_time")):
        options["scheduled_time"] = when.isoformat()
    if req.get("ams_mapping"):
        # Slots follow one plate's filament order on one printer's AMS/CFS.
        if printer_id is None or len(plates) != 1:
            raise HTTPException(422, "Chosen slots need a chosen printer and one plate")
        options["ams_mapping"] = list(req["ams_mapping"])
    return options or None


async def create_jobs(db: AsyncSession, requests: list[dict]) -> list[FarmPrintJob]:
    """One job per request ``{component_code, plate_ids, printer_id?, ...queue options}``; plates not
    sliced yet are sliced now.

    With ``printer_id`` only the plates of that printer's model are kept.
    """
    resolver = await PrinterModelResolver.load(db)
    excluded = await excluded_files(db)
    files = await component_files(db)
    own = await product_shapes(db)
    jobs = []
    forced: set[tuple[int, str | None]] = set()  # (plate, colour) sliced again once, whatever jobs share it
    for req in requests:
        comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == req["component_code"]))
        if comp is None or comp.retired_at is not None:
            raise HTTPException(422, f"Unknown or retired component {req['component_code']}")
        plates = []
        for plate_id in dict.fromkeys(req["plate_ids"]):
            plate = await db.get(FarmPlate, plate_id)
            if plate is None or not _pieces(plate, comp, own):
                raise HTTPException(422, f"Plate {plate_id} is not a confirmed plate of {comp.code}")
            if plate.library_file_id in excluded:
                raise HTTPException(422, f"Plate {plate_id} is in a 3MF excluded from its products")
            if not may_print(files, comp.id, plate.library_file_id):
                raise HTTPException(422, f"Plate {plate_id} is in a 3MF of another product, not of {comp.code}")
            plates.append(plate)
        printer_id = req.get("printer_id")
        if printer_id is not None:
            printer = await db.get(Printer, printer_id)
            if printer is None or not printer.is_active:
                raise HTTPException(422, f"Unknown or inactive printer {printer_id}")
            plates = [p for p in plates if _plate_model(resolver, p) == (printer.model or "").lower()]
            if not plates:
                raise HTTPException(422, f"No confirmed plate of {comp.code} for {printer.name} ({printer.model})")
        if not plates:
            raise HTTPException(422, f"No plate for {comp.code}")
        job = FarmPrintJob(
            component_code=comp.code,
            color=comp.color,
            plate_ids=[p.id for p in plates],
            expected_pieces=_pieces(plates[0], comp, own),
            printer_id=printer_id,
            queue_options=_queue_options(req, printer_id, plates),
        )
        db.add(job)
        jobs.append(job)
        for plate in plates:
            if req.get("force_slice"):  # asked in Accoda: a new slice from the 3MF on the NAS, never the old one
                from backend.app.farm.slicing import colour_key, force_slice

                if (plate.id, colour_key(comp.color)) not in forced:
                    forced.add((plate.id, colour_key(comp.color)))
                    if not await force_slice(db, plate, comp.color):
                        job.note = f"plate {plate.plate_index}: the 3MF is no longer in the library, no new slice"
            elif await variant_file(db, plate, comp.color) is None:
                await ask_variant(db, plate, comp.color)
    await db.commit()
    for job in jobs:
        job_events.record(
            "created",
            f"{job.component_code}: {job.expected_pieces} pieces, {len(job.plate_ids)} plate(s) to choose from",
            job_id=job.id,
            printer_id=job.printer_id,
            detail={"plate_ids": job.plate_ids, "colour": job.color, "options": job.queue_options},
        )
    return jobs


async def plate_filaments(db: AsyncSession, plate: FarmPlate, color: str | None) -> list[dict] | None:
    """The filament slots of the plate's sliced output in that colour (what AMS/CFS slots are chosen for);
    None until sliced."""
    sliced = await variant_file(db, plate, color)
    if sliced is None:
        return None
    path = await _local_path(db, sliced)
    return await asyncio.to_thread(extract_filament_requirements, path, _plate_in_file(path, plate.plate_index))


async def queued_pieces(db: AsyncSession) -> dict[str, int]:
    """Pieces per component waiting for a printer, in the queue or printing (not to be proposed again)."""
    out: dict[str, int] = {}
    rows = await db.execute(
        select(FarmPrintJob, PrintQueueItem.status)
        .outerjoin(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .where(FarmPrintJob.status != "cancelled")
    )
    for job, item_status in rows.all():
        if job.status == "waiting":
            out[job.component_code] = out.get(job.component_code, 0) + job.expected_pieces
        elif item_status in ACTIVE_QUEUE:  # less the objects the printer excluded
            pieces = job.printed_pieces if job.printed_pieces is not None else job.pieces
            out[job.component_code] = out.get(job.component_code, 0) + (pieces or 0)
    return out


def klipper_gcode_name(sliced_name: str, plate: int) -> str:
    """``K2-Citroen AMI - plate 2.gcode.3mf`` -> ``K2-Citroen-AMI-plate-2.gcode`` (the printer adds its fingerprint)."""
    base = sliced_name.removesuffix(".gcode.3mf").removesuffix(".3mf")
    if not re.search(rf"plate[\s_-]*{plate}(?!\d)", base, re.IGNORECASE):
        base += f" plate {plate}"
    return f"{re.sub(r'[^A-Za-z0-9]+', '-', base).strip('-') or 'plate'}.gcode"


class SliceNotLocal(Exception):
    """The local copy of a sliced output isn't the current one (the NAS didn't answer the fetch)."""


def _sha256_of(path: Path) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
    except OSError:
        return None
    return h.hexdigest()


async def gcode_for_klipper(db: AsyncSession, sliced: LibraryFile, path: Path, plate: int) -> LibraryFile | None:
    """Moonraker prints plain G-code: the plate's ``Metadata/plate_N.gcode`` from the sliced 3MF, kept once.

    A sliced output replaced by a new slice keeps its row, so the extracted G-code is refreshed in
    its own row when the output's bytes change (here, and before a queued item starts, see
    ``stale_slice_reason``): Klipper queue items follow it like Bambu ones.
    The G-code gets the plate's picture and layer info (``klipper_gcode``); the row gets the plate's
    print time, grams and picture, which the queue shows as for a Bambu print.
    """
    from backend.app.api.routes import library as lib
    from backend.app.api.routes.print_queue import _extract_print_time_from_3mf
    from backend.app.farm.klipper_gcode import for_klipper
    from backend.app.utils.threemf_tools import extract_filament_usage_from_3mf

    name = klipper_gcode_name(sliced.filename, plate)
    gcode = None
    for candidate in (await db.execute(LibraryFile.active().where(LibraryFile.filename == name))).scalars():
        meta = candidate.file_metadata or {}
        if meta.get("extracted_from_library_file_id") == sliced.id and meta.get("plate") == plate:
            if (
                meta.get("extracted_from_hash") == sliced.file_hash
                and meta.get("extracted_version") == EXTRACTED_VERSION
            ):
                return candidate
            gcode = candidate  # sliced again, or extracted before the preview: extract into the same row
    # A cached copy the NAS couldn't refresh holds the previous slice: never stamp it with the new hash.
    if sliced.file_hash and await asyncio.to_thread(_sha256_of, path) != sliced.file_hash:
        raise SliceNotLocal(f"{sliced.filename}: the local copy is not the current slice")
    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read(f"Metadata/plate_{plate}.gcode")
            png = zf.read(f"Metadata/plate_{plate}.png") if f"Metadata/plate_{plate}.png" in zf.namelist() else None
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    data = await asyncio.to_thread(for_klipper, data, png)
    out = lib.to_absolute_path(gcode.file_path) if gcode else lib.get_library_files_dir() / f"{uuid.uuid4().hex}.gcode"
    tmp = out.with_name(f".{out.name}.part")
    await asyncio.to_thread(tmp.write_bytes, data)
    os.replace(tmp, out)  # a print starting meanwhile never reads half a file
    if gcode is None:
        gcode = LibraryFile(
            folder_id=sliced.folder_id,
            filename=name,
            file_path=lib.to_relative_path(out),
            file_type="gcode",
            source_type="sliced",
        )
        db.add(gcode)
    gcode.file_size = len(data)
    if png:
        thumb = lib.to_absolute_path(gcode.thumbnail_path) if gcode.thumbnail_path else None
        thumb = thumb or lib.get_library_thumbnails_dir() / f"{uuid.uuid4().hex}.png"
        await asyncio.to_thread(thumb.write_bytes, png)
        gcode.thumbnail_path = lib.to_relative_path(thumb)
    grams = sum(f["used_g"] for f in await asyncio.to_thread(extract_filament_usage_from_3mf, path, plate))
    gcode.file_metadata = {
        **(sliced.file_metadata or {}),
        "print_time_seconds": _extract_print_time_from_3mf(path, plate)
        or (sliced.file_metadata or {}).get("print_time_seconds"),
        "filament_used_grams": round(grams, 2) if grams else (sliced.file_metadata or {}).get("filament_used_grams"),
        "extracted_from_library_file_id": sliced.id,
        "extracted_from_hash": sliced.file_hash,
        "extracted_version": EXTRACTED_VERSION,
        "plate": plate,
    }
    await db.flush()
    return gcode


def _plate_in_file(path: Path, default: int) -> int:
    """The plate number the sliced file carries (``Metadata/plate_N.gcode``)."""
    try:
        with zipfile.ZipFile(path) as zf:
            for name in zf.namelist():
                if m := re.fullmatch(r"Metadata/plate_(\d+)\.gcode", name):
                    return int(m.group(1))
    except (OSError, zipfile.BadZipFile):
        pass
    return default


async def _local_path(db: AsyncSession, lib_file: LibraryFile) -> Path:
    from backend.app.farm.nas.cache import ensure_local

    await ensure_local(db, lib_file)
    path = Path(lib_file.file_path)
    return path if path.is_absolute() else settings.base_dir / lib_file.file_path


# Why a queued farm output waits instead of starting (``waiting_reason`` of its queue item).
WAIT_NEW_SLICE = "3MF changed after queueing: waiting for the new slice"
WAIT_RESLICE_FAILED = "3MF changed after queueing, and slicing it again failed: slice it again or cancel"
WAIT_NAS_CHANGED = "3MF changed on the NAS: waiting for it to be read again"
WAIT_NO_SOURCE = "The 3MF this was sliced from is missing or unreadable"
WAIT_SLICE_UNREADABLE = "Cannot read the new slice from the NAS: waiting for it"
WAIT_CHECK_FAILED = "Cannot check the slice of this print: waiting"
WAIT_FILE_UNREADABLE = "Cannot read the file to print (NAS or cache): waiting for it"
WAIT_REASONS = (
    WAIT_NEW_SLICE,
    WAIT_RESLICE_FAILED,
    WAIT_NAS_CHANGED,
    WAIT_NO_SOURCE,
    WAIT_SLICE_UNREADABLE,
    WAIT_CHECK_FAILED,
    WAIT_FILE_UNREADABLE,
)
# Why it never will: the queue item fails with this ``error_message``.
FAIL_NO_PROJECT = "The 3MF this was sliced from was removed from the library"
FAIL_NO_OUTPUT = "The sliced file this G-code came from is gone: queue the plate again"
FAIL_NO_GCODE = "The new slice has no G-code for this plate: queue the plate again"
FAIL_PLATE_GONE = "This plate is no longer in the 3MF: queue another plate"
FAIL_PLATE_CHANGED = "The 3MF changed after queueing: this plate prints other pieces now. Queue it again"
FAIL_PLATE_RENAMED = "The 3MF changed after queueing: this plate's objects were renamed. Check it and queue it again"
FAIL_FILE_CHANGED = "The file to print changed after queueing: queue the plate again"
FAIL_REASONS = (
    FAIL_NO_PROJECT,
    FAIL_NO_OUTPUT,
    FAIL_NO_GCODE,
    FAIL_PLATE_GONE,
    FAIL_PLATE_CHANGED,
    FAIL_PLATE_RENAMED,
    FAIL_FILE_CHANGED,
)

_reslice_asked: dict[tuple[int, str], float] = {}  # (project id, version) -> when a start asked to re-slice it
_sha_cache: dict[tuple[str, int, int], str] = {}  # (path, mtime_ns, size) -> sha256: not read again every tick


async def fail_queued(db: AsyncSession, item: PrintQueueItem, reason: str) -> None:
    """A queue item that never can start fails: the queue history shows why (in the "dispatch" phase,
    ``services.queue_failure``), and so does its farm job's note."""
    queue_failure.mark_failed(item, reason, "dispatch")
    item.waiting_reason = None
    job = await db.scalar(select(FarmPrintJob).where(FarmPrintJob.queue_item_id == item.id))
    if job is not None:
        job.note = reason[:255]
    logger.warning("Queue item %s failed: %s", item.id, reason)


async def fail_queued_jobs(db: AsyncSession, plate_ids: set[int], reason: str) -> None:
    """Farm jobs of these plates still waiting in the queue fail now (``plates.refresh_plates``), not at their start.

    ``reason``, unless the job's own check says more (its objects only renamed).
    """
    if not plate_ids:
        return
    rows = await db.execute(
        select(PrintQueueItem, FarmPrintJob)
        .join(FarmPrintJob, FarmPrintJob.queue_item_id == PrintQueueItem.id)
        .where(
            FarmPrintJob.status == "queued", FarmPrintJob.plate_id.in_(plate_ids), PrintQueueItem.status == "pending"
        )
    )
    for item, job in rows.all():
        own = await job_change_reason(db, job) if reason == FAIL_PLATE_CHANGED else None
        await fail_queued(db, item, own or reason)


def _plate_objects(plate: FarmPlate) -> dict[str, int]:
    return {y.object_name: y.quantity for y in plate.yields}


async def job_change_reason(db: AsyncSession, job: FarmPrintJob) -> str | None:
    """Why a queued farm job may not start: its plate left the 3MF, or prints other pieces than it was queued for."""
    plate = await db.get(FarmPlate, job.plate_id) if job.plate_id else None
    if plate is None:
        return FAIL_PLATE_GONE
    if job.queued_as is not None:
        now, then = _plate_objects(plate), job.queued_as.get("objects") or {}
        if now == then:
            return None
        # The same quantities under other names: renamed in the slicer, maybe the same pieces, still checked by hand.
        return FAIL_PLATE_RENAMED if sorted(now.values()) == sorted(then.values()) else FAIL_PLATE_CHANGED
    # Queued before ``queued_as``: only the pieces of its component, and not at all when the component is gone.
    comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == job.component_code))
    changed = comp is not None and _pieces(plate, comp, await product_shapes(db)) != job.pieces
    return FAIL_PLATE_CHANGED if changed else None


def _cached_sha(path: Path) -> str | None:
    """sha256 of a file, read again only when its path, mtime or size change. None: unreadable."""
    try:
        st = path.stat()
    except OSError:
        return None
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key not in _sha_cache:
        if (sha := _sha256_of(path)) is None:
            return None
        if len(_sha_cache) > 512:  # one entry per queued file version: never more than a few
            _sha_cache.clear()
        _sha_cache[key] = sha
    return _sha_cache[key]


async def _file_sha(db: AsyncSession, lib_file: LibraryFile) -> str | None:
    """sha256 of the local copy of a file to print (a NAS file is fetched into the cache first). None: unreadable."""
    return await asyncio.to_thread(_cached_sha, await _local_path(db, lib_file))


async def _sends(db: AsyncSession, job: FarmPrintJob | None, lib_file: LibraryFile) -> bool:
    """The job now sends this file: a new slice of the same pieces (checked before), from the current 3MF.

    False when it can't be read now: the item waits, still on what it was queued with.
    """
    if job is None or job.queued_as is None:
        return True
    if (sha := await _file_sha(db, lib_file)) is None:
        return False
    job.queued_as = {**job.queued_as, "file_hash": sha}
    return True


async def _changed_on_nas(db: AsyncSession, state: FarmLibraryFile) -> bool:
    """Whether a NAS 3MF was saved again since the scan read it: one stat, size and mtime only.

    A NAS that doesn't answer is no reason to hold the print: the known version stands.
    """
    from backend.app.farm.nas import access, service as nas_service
    from backend.app.farm.nas.remote import NasBusyError

    try:
        active = await nas_service.refresh_access(db)
        parts = active.parts(state.path) if active else None
        if parts is None:
            return False  # not on the NAS
        entry = await access.run(active.remote.stat, parts)
    except (ConnectionError, TimeoutError, NasBusyError) as exc:
        logger.warning("Farm start check: no stat of %s on the NAS (%s): using the known version", state.path, exc)
        return False
    except OSError:
        return True  # the NAS answered: gone or unreadable, the scan reads it again
    except Exception:  # a failed check must not stop the queue check of every printer
        logger.warning("Farm start check: stat of %s failed: using the known version", state.path, exc_info=True)
        return False
    return entry.size != state.size or abs(entry.mtime - state.mtime) >= 1


async def stale_slice_reason(db: AsyncSession, item: PrintQueueItem) -> str | None:
    """Why a queued farm output may not start: it was sliced from another version of its 3MF, or
    it is a farm job's and no longer prints what the job was queued for.

    A project saved again is re-sliced (``farm.reslice``), so the item waits for that (asked again
    here when it isn't under way) and then sends the new slice; one saved after the last scan is
    seen by a stat on the NAS and waits for the scan, asked now. A Klipper G-code extracted from
    an output sliced again since is extracted again. A farm job's item fails when its plate left
    the 3MF or prints other pieces now, or when the file it sends isn't the one queued (or the
    new slice of the same pieces). None: it may start, or it isn't a farm output.
    A reason in ``FAIL_REASONS`` never clears: the item should fail.
    """
    job = await db.scalar(select(FarmPrintJob).where(FarmPrintJob.queue_item_id == item.id))
    if job is not None and (reason := await job_change_reason(db, job)):
        return reason
    reason = await _slice_reason(db, item, job)
    if reason is None and job is not None and (want := (job.queued_as or {}).get("file_hash")):
        return await _file_reason(db, item, want)
    return reason


async def _file_reason(db: AsyncSession, item: PrintQueueItem, want: str) -> str | None:
    """The file the item sends must be the one queued: read and different fails, not readable now waits."""
    from backend.app.farm.nas.cache import ensure_local

    sent = await db.get(LibraryFile, item.library_file_id)
    if sent is None:
        return FAIL_NO_OUTPUT
    have = await _file_sha(db, sent)
    if have is None:
        return WAIT_FILE_UNREADABLE  # NAS down and no cache copy, or the cache evicted it: it comes back
    if have == want:
        return None
    if sent.is_external and not await ensure_local(db, sent):
        return WAIT_FILE_UNREADABLE  # an old cache copy the NAS couldn't confirm: no proof it changed
    return FAIL_FILE_CHANGED  # e.g. written over on the NAS: never sent in place of what was queued


async def _slice_reason(db: AsyncSession, item: PrintQueueItem, job: FarmPrintJob | None) -> str | None:
    lib_file = await db.scalar(LibraryFile.active().where(LibraryFile.id == item.library_file_id))
    meta = (lib_file.file_metadata or {}) if lib_file else {}
    project_id = meta.get("sliced_from_library_file_id")
    # Farm outputs carry the source version; the upstream slice route's outputs don't, and are left alone.
    if not meta.get("used_embedded_settings") or project_id is None or "sliced_from_sha256" not in meta:
        return None
    output = lib_file
    if (output_id := meta.get("extracted_from_library_file_id")) is not None:
        output = await db.scalar(LibraryFile.active().where(LibraryFile.id == output_id))
        if output is None:
            return FAIL_NO_OUTPUT
    state = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == project_id))
    if state is None or await db.scalar(LibraryFile.active().where(LibraryFile.id == project_id)) is None:
        return FAIL_NO_PROJECT
    if state.status != "ok":
        return WAIT_NO_SOURCE  # missing or corrupt on the NAS: saved again, it is read again
    if await _changed_on_nas(db, state):
        # Saved after the last scan: scanned again now (the 60 s settle rule holds), then re-sliced.
        from backend.app.farm.models import FarmWatchedFolder

        watch = await db.get(FarmWatchedFolder, state.watch_folder_id)
        if watch is not None and watch.last_scan_at is not None:
            watch.last_scan_at = None
            await db.commit()
        return WAIT_NAS_CHANGED
    out_meta = output.file_metadata or {}
    if out_meta.get("sliced_from_sha256") != state.sha256:
        from backend.app.farm.slicing import previous_output  # slicing imports this module

        newer = await previous_output(db, project_id, out_meta.get("sliced_plate"), out_meta.get("sliced_color"))
        if newer is not None and (newer.file_metadata or {}).get("sliced_from_sha256") == state.sha256:
            # Sliced again into a row of its own: the slice queued here was kept (``slicing.slice_embedded``).
            plate = await db.get(FarmPlate, job.plate_id) if job is not None else None
            if plate is not None and plate.source_sha256 != state.sha256:
                return WAIT_NEW_SLICE  # the job's pieces are checked on the new version's plates, not read yet
            if output is lib_file:
                if not await _sends(db, job, newer):
                    return WAIT_FILE_UNREADABLE
                item.library_file_id = newer.id
                await db.commit()
                logger.info("Queue item %d: moved to the new slice %s", item.id, newer.filename)
                return None
            output = newer  # a Klipper G-code: extracted from the new slice below
        else:
            return await _await_reslice(db, project_id, state, out_meta)
    if output is not lib_file and meta.get("extracted_from_hash") != output.file_hash:
        try:
            gcode = await gcode_for_klipper(db, output, await _local_path(db, output), meta.get("plate") or 1)
        except SliceNotLocal:
            return WAIT_SLICE_UNREADABLE
        if gcode is None:
            return FAIL_NO_GCODE
        if not await _sends(db, job, gcode):
            return WAIT_FILE_UNREADABLE
        item.library_file_id = gcode.id
        await db.commit()
        logger.info("Queue item %d: G-code extracted again from the new slice of %s", item.id, output.filename)
    return None


async def _await_reslice(db: AsyncSession, project_id: int, state: FarmLibraryFile, out_meta: dict) -> str:
    """The output is from an older version of its 3MF and no newer slice exists: ask for it, or say why not."""
    from backend.app.farm import reslice  # imports slicing, which imports this module
    from backend.app.farm.slicing import plate_key

    plates = list((await db.execute(select(FarmPlate).where(FarmPlate.library_file_id == project_id))).scalars())
    plate_no = plate_key(out_meta.get("sliced_plate"))  # 0: all plates, never a stored plate
    if plate_no and plates and all(p.source_sha256 == state.sha256 for p in plates):  # this version's
        if plate_no not in {p.plate_index for p in plates}:
            return FAIL_PLATE_GONE  # the re-slice skips it: it would wait for good
    if project_id in reslice._running:
        return WAIT_NEW_SLICE
    key = (project_id, state.sha256)
    if time.monotonic() - _reslice_asked.get(key, -RESLICE_AFTER_SECONDS) >= RESLICE_AFTER_SECONDS:
        await reslice.schedule(db, [project_id])
        _reslice_asked[key] = time.monotonic()  # only once asked: a failed schedule is tried next check
        if project_id in reslice._running:
            return WAIT_NEW_SLICE
    return WAIT_RESLICE_FAILED  # failed, or skipped (e.g. excluded): asked again after a while


def _matching_spool(printer: dict, materials: list[str], color_hex: str, tolerance: int) -> dict | None:
    wanted = {_canonical_filament_type(m) for m in materials if m}
    spools = [
        f
        for f in printer["loaded"]
        if (not wanted or _canonical_filament_type(f["type"]) in wanted)
        and f["color"]
        and plan.color_distance(f["color"], color_hex) <= tolerance
    ]
    return min(spools, key=lambda f: plan.color_distance(f["color"], color_hex)) if spools else None


def _loaded_trays(printer_id: int) -> list[dict]:
    """The printer's loaded slots with their global tray ids: AMS, K2 CFS, external spool."""
    status = printer_manager.get_status(printer_id)
    return scheduler._build_loaded_filaments(status) if status is not None and status.raw_data else []


def colour_name(color: str, colors: dict[str, str], tolerance: int) -> str | None:
    """The colour table's name nearest a spool's colour, as components name it ("Nero"); else ``#rrggbb``.

    ``fff`` is ``ffffff``; RGBA keeps its RGB. None when it is not a colour.
    """
    color = (color or "").strip().lstrip("#").lower()
    if not HEX_RE.fullmatch(color[:6] if len(color) >= 6 else color):
        return None
    color = "".join(c * 2 for c in color) if len(color) == 3 else color[:6]
    table = {name: hex_ for name, hex_ in colors.items() if HEX_RE.fullmatch(hex_ or "") and len(hex_) == 6}
    near = min(table.items(), key=lambda kv: plan.color_distance(kv[1], color), default=None)
    if near is not None and plan.color_distance(near[1], color) <= tolerance:
        return near[0].capitalize()
    return f"#{color}"


def used_color(extra: dict | None, sure: bool = False) -> str | None:
    """The colour most sampled while the print ran (see ``record_used_colors``).

    ``sure``: only when there are enough samples and nearly all of them say it, as to pick a component.
    """
    samples = (extra or {}).get(USED_COLORS_KEY) or {}
    if not samples:
        return None
    best = max(samples, key=samples.get)
    total = sum(samples.values())
    if sure and (total < SURE_SAMPLES or samples[best] < SURE_SHARE * total):
        return None
    return best


def _same_file(a: str | None, b: str | None) -> bool:
    """One print's file under its names: ``Sopra__1a2b3c4d.3mf``, ``Sopra``, ``/data/Sopra.gcode.3mf``."""
    from backend.app.farm.jobs.outside import _core

    return bool(a and b and _core(a) and _core(a) == _core(b))


async def record_used_colors(db: AsyncSession) -> int:
    """Sample, on each running print's archive and farm job, the colour of the filament being fed.

    The AMS/CFS slot in use (or the external spool); a Klipper printer without CFS reports none,
    so its spool set by hand. The batch's colour is the one sampled most. Returns how many prints.
    """
    config = await plan.load_config(db)
    by_hand = dict((await db.execute(select(FarmPrinterSpool.printer_id, FarmPrinterSpool.color_name))).all())
    sampled = 0
    for printer_id in (await db.execute(select(Printer.id).where(Printer.is_active.is_(True)))).scalars().all():
        try:  # one printer at a time: one that always fails never holds back the others
            if await _sample_colour(db, printer_id, config, by_hand.get(printer_id)):
                await db.commit()
                sampled += 1
        except Exception:
            await db.rollback()
            logger.exception("Printer %s: colour not sampled", printer_id)
    return sampled


async def _sample_colour(db: AsyncSession, printer_id: int, config: dict, by_hand: str | None) -> bool:
    """One sample of the colour this printer feeds, on its running print; False when there is none."""
    status = printer_manager.get_status(printer_id)
    if status is None or status.state != "RUNNING":
        return False
    trays = _loaded_trays(printer_id)
    if trays:
        tray = next((t for t in trays if t["global_tray_id"] == getattr(status, "tray_now", None)), None)
        if tray is None or not tray.get("color"):
            return False  # between two slots, or a tray without colour
        name = colour_name(tray["color"], config["colors"], config["tolerance"])
    else:
        name = by_hand
    if not name:
        return False  # no slot, no spool set by hand, or a slot reporting no real colour
    archive = await _running_archive(db, printer_id, status)
    if archive is None:
        return False
    samples = dict((archive.extra_data or {}).get(USED_COLORS_KEY) or {})
    samples[name] = samples.get(name, 0) + 1
    archive.extra_data = {**(archive.extra_data or {}), USED_COLORS_KEY: samples}
    for job in await _archive_jobs(db, archive):
        job.used_color = used_color(archive.extra_data)
    return True


async def _running_archive(db: AsyncSession, printer_id: int, status) -> PrintArchive | None:
    """The archive of the print running now: the newest "printing" one of the file the printer reports."""
    printing = getattr(status, "subtask_name", None) or getattr(status, "gcode_file", None)
    recent = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=PRINT_MAX_AGE)
    candidates = await db.execute(
        select(PrintArchive)
        .where(
            PrintArchive.printer_id == printer_id,
            PrintArchive.status == "printing",
            PrintArchive.created_at >= recent,
        )
        .order_by(PrintArchive.id.desc())
    )
    return next(
        (a for a in candidates.scalars() if _same_file(printing, a.filename) or _same_file(printing, a.print_name)),
        None,
    )


async def _archive_jobs(db: AsyncSession, archive: PrintArchive) -> list[FarmPrintJob]:
    jobs = await db.execute(
        select(FarmPrintJob)
        .join(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
        .where(PrintQueueItem.archive_id == archive.id)
    )
    return list(jobs.scalars())


async def record_excluded_objects(db: AsyncSession) -> int:
    """Read, on each running print's farm jobs, the objects the printer excluded and the pieces left.

    Bambu skip objects (ids of its slice_info), Klipper ``exclude_object`` (names); every tick while the
    print runs or is paused, and once more as it ends (``record_excluded_at_end``). The printer's list is
    the truth: read twice, an object counts once; one it restores is excluded no more. Names read before
    stay (a restart loses Bambu's). Returns how many prints.
    """
    read = 0
    for printer_id in (await db.execute(select(Printer.id).where(Printer.is_active.is_(True)))).scalars().all():
        try:  # one printer at a time: one that always fails never holds back the others
            if await _sample_excluded(db, printer_id):
                await db.commit()
                read += 1
        except Exception:
            await db.rollback()
            logger.exception("Printer %s: excluded objects not read", printer_id)
    return read


async def record_excluded_at_end(printer_id: int) -> None:
    """The last read of a print's excluded objects as it ends, before its archive closes
    (``main.on_print_complete``): one excluded after the last tick counts, and only then is the batch ``read``."""
    try:
        async with database.async_session() as db:
            if await _sample_excluded(db, printer_id, ending=True):
                await db.commit()
    except Exception:
        logger.exception("Printer %s: excluded objects not read at the end", printer_id)


async def _sample_excluded(db: AsyncSession, printer_id: int, ending: bool = False) -> bool:
    status = printer_manager.get_status(printer_id)
    # A printer that dropped keeps its last state: what it would say now is unknown
    if status is None or not getattr(status, "connected", True):
        return False
    if not ending and status.state not in ("RUNNING", "PAUSE"):
        return False
    archive = await _running_archive(db, printer_id, status)
    jobs = await _archive_jobs(db, archive) if archive is not None else []
    if not jobs:
        return False
    printable = getattr(status, "printable_objects", None) or {}
    names = {str(k): v.get("name") if isinstance(v, dict) else v for k, v in printable.items()}
    skipped = [str(oid) for oid in getattr(status, "skipped_objects", None) or []]
    for job in jobs:
        before = (job.excluded or {}).get("objects") or {}
        if any(before.get(oid) is None and oid not in names for oid in skipped):
            # Bambu after a restart: its objects are in the archive's 3MF
            names = {**await asyncio.to_thread(_archive_objects, archive.file_path or ""), **names}
        objects = {oid: before.get(oid) or names.get(oid) for oid in skipped}
        if job.excluded is not None and objects == before and not ending:
            continue  # nothing new: no write
        pieces = await _printed_pieces(db, job, list(objects.values()))
        job.excluded = {"objects": objects, "matched": pieces is not None, **({"end": True} if ending else {})}
        if pieces is not None:  # else the last valid count stays: the pieces never go back up for an unknown
            job.printed_pieces = pieces
    return True


@functools.lru_cache(maxsize=16)
def _archive_objects(file_path: str) -> dict[str, str]:
    """``{identify_id: name}`` of the objects in an archive's 3MF; {} when there is none."""
    from backend.app.services.archive import extract_printable_objects_from_3mf

    path = settings.base_dir / file_path
    if not path.is_file() or path.suffix != ".3mf":
        return {}
    return {str(k): v for k, v in extract_printable_objects_from_3mf(path.read_bytes()).items()}


async def _printed_pieces(db: AsyncSession, job: FarmPrintJob, excluded: list[str | None]) -> int | None:
    """The job's pieces less those excluded: complete sets for a set (a spare part isn't a unit).

    None when an excluded object has no name or isn't an object of the plate: the batch keeps its pieces.
    """
    planned = job.pieces if job.pieces is not None else job.expected_pieces
    if not excluded:
        return planned
    plate = await db.get(FarmPlate, job.plate_id) if job.plate_id else None
    comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == job.component_code))
    if plate is None or comp is None or None in excluded:
        return None
    objects = [y.object_name for y in plate.yields]
    lost = Counter(match_object(name, objects) for name in excluded)
    if None in lost or any(lost[y.object_name] > y.quantity for y in plate.yields):
        return None
    own = await product_shapes(db, {comp.project_id}) if comp.project_id is not None else {}
    full = _pieces(plate, comp, own)
    left = SimpleNamespace(
        status=plate.status,
        yields=[
            SimpleNamespace(
                object_name=y.object_name,
                quantity=y.quantity - lost[y.object_name],
                shape_id=y.shape_id,
                component_code=y.component_code,
                fingerprint=y.fingerprint,
            )
            for y in plate.yields
        ],
    )
    return max(planned - (full - _pieces(left, comp, own)), 0) if full else None


def _slot_mapping(used: list[dict], trays: list[dict], color_hex: str, tolerance: int) -> list[int] | None:
    """Each filament the plate uses on a tray of the component's colour and a compatible material, the others
    -1 (position = slot_id - 1, as upstream's ``ams_mapping``); None when one of them has no such tray."""

    def distance(tray: dict) -> float:
        return plan.color_distance(tray["color"].lstrip("#").lower()[:6], color_hex)

    mapping = [-1] * max(f["slot_id"] for f in used)
    for f in used:
        want = _canonical_filament_type(f.get("type") or "")
        fits = [
            t
            for t in trays
            if t.get("color") and (not want or _canonical_filament_type(t["type"]) == want) and distance(t) <= tolerance
        ]
        if not fits:
            return None
        mapping[f["slot_id"] - 1] = min(fits, key=distance)["global_tray_id"]
    return mapping


async def dispatch_once(db: AsyncSession) -> int:
    """Place waiting jobs on free printers that fit; returns how many queue items were created."""
    jobs = list(
        (await db.execute(select(FarmPrintJob).where(FarmPrintJob.status == "waiting").order_by(FarmPrintJob.id)))
        .scalars()
        .all()
    )
    if not jobs:
        return 0
    notes_before = {job.id: job.note for job in jobs}
    config = await plan.load_config(db)
    resolver = await PrinterModelResolver.load(db)
    printers_by_id = {
        p.id: p for p in (await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars().all()
    }
    snapshot = await plan.with_spools_set_by_hand(db, plan.printer_snapshot(list(printers_by_id.values())))
    excluded = await excluded_files(db)
    files = await component_files(db)
    own = await product_shapes(db)
    busy = set(
        (
            await db.execute(
                select(PrintQueueItem.printer_id).where(
                    PrintQueueItem.status.in_(ACTIVE_QUEUE), PrintQueueItem.printer_id.isnot(None)
                )
            )
        ).scalars()
    )
    busy |= scheduler.printers_refusing_uploads()  # it refused its last file: a job placed there would only wait
    created = 0
    for job in jobs:
        comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == job.component_code))
        color_hex = config["colors"].get((job.color or "").lower())
        if comp is None or color_hex is None:
            job.note = f"No reference colour for '{job.color}'"
            continue
        options = job.queue_options or {}
        start_at = _utc(options.get("scheduled_time"))
        if job.printer_id is None and start_at and start_at > datetime.now(timezone.utc).replace(tzinfo=None):
            job.note = f"Starts at {start_at:%Y-%m-%d %H:%M} UTC"
            continue
        mapped = options.get("ams_mapping")  # slots chosen by hand: no spool check, nothing forced
        reasons = []
        placed = False
        slicing = False
        # A chosen printer first takes a plate (a 3MF, often one per material) whose material and colour
        # are loaded; only when none is, the first one, for a manual start after changing the spool.
        for strict in (True, False) if job.printer_id is not None else (True,):
            if placed or (not strict and slicing):
                break
            for plate_id in job.plate_ids:
                plate = await db.get(FarmPlate, plate_id)
                if plate is None or plate.library_file_id in excluded or not _pieces(plate, comp, own):
                    continue
                if not may_print(files, comp.id, plate.library_file_id):  # queued before the 3MF left
                    continue
                sliced = await variant_file(db, plate, job.color)  # the slice in the component's colour
                if sliced is None:
                    reasons.append(await ask_variant(db, plate, job.color))  # being sliced, or why it failed
                    slicing = True
                    continue
                model = _plate_model(resolver, plate)
                read: tuple | None = None  # (path, plate number, used filaments) of the sliced file, once
                for printer in snapshot:
                    if printer["model"].lower() != model:
                        continue
                    if job.printer_id is not None:  # chosen by the user: its queue, whatever its state
                        if printer["id"] != job.printer_id:
                            continue
                    elif not printer["idle"] or printer["id"] in busy:
                        continue
                    klipper = normalize_provider(printers_by_id[printer["id"]].provider) != "bambu"
                    # Single-spool Klipper printers don't report what is loaded: queued for a manual start.
                    unreadable = klipper and not printer["loaded"]
                    if read is None:
                        path = await _local_path(db, sliced)
                        plate_no = _plate_in_file(path, plate.plate_index)
                        read = (path, plate_no, await asyncio.to_thread(extract_filament_requirements, path, plate_no))
                    path, plate_no, slots = read
                    auto = None  # trays of the component's colour for the filaments the plate uses
                    spool = None  # a spool of that colour, when the slots can't be mapped
                    if not (unreadable or mapped):
                        if slots and not printer.get("by_hand"):
                            auto = _slot_mapping(slots, _loaded_trays(printer["id"]), color_hex, config["tolerance"])
                        else:  # no slice_info, or a spool set by hand on a Klipper: the colour check only
                            spool = _matching_spool(printer, plate.materials or [], color_hex, config["tolerance"])
                    loaded = bool(mapped or auto or spool)
                    # ignore_spool (asked in Accoda for a chosen printer): sent and started like a fitting one
                    overridden = not loaded and bool(options.get("ignore_spool") and job.printer_id)
                    fits = loaded or overridden
                    if not fits and not unreadable and strict:
                        used = sorted({f.get("type") for f in slots if f.get("type")}) or plate.materials or []
                        reasons.append(f"{printer['name']}: no {job.color} {'+'.join(used)} loaded")
                        continue
                    send = sliced
                    if klipper and not sliced.filename.endswith(".gcode"):
                        try:
                            send = await gcode_for_klipper(db, sliced, path, plate_no)
                        except SliceNotLocal:
                            reasons.append(f"plate {plate.plate_index}: the sliced file can't be fetched from the NAS")
                            continue
                        if send is None:
                            reasons.append(f"{printer['name']}: no G-code for plate {plate_no} in the sliced file")
                            continue
                    ams = mapped or auto
                    # Slots unknown: the upstream scheduler maps them at start, forced to the spool's colour.
                    overrides = (
                        []
                        if spool is None or printer.get("by_hand")
                        else [
                            {
                                "slot_id": f["slot_id"],
                                "type": f.get("type") or spool["type"],
                                "color": f"#{spool['color']}",
                                "force_color_match": True,
                            }
                            for f in slots
                        ]
                    )
                    # Its fingerprint, or no queue item: without it the start couldn't check what it sends.
                    if (file_hash := await _file_sha(db, send)) is None:
                        reasons.append(f"plate {plate.plate_index}: the sliced file can't be read now")
                        continue
                    position = (await db.scalar(select(func.max(PrintQueueItem.position)))) or 0
                    item = PrintQueueItem(
                        printer_id=printer["id"],
                        library_file_id=send.id,
                        manual_start=bool(options.get("manual_start")) or not fits,
                        scheduled_time=start_at,
                        require_previous_success=bool(options.get("require_previous_success")),
                        auto_off_after=bool(options.get("auto_off_after")),
                        gcode_injection=bool(options.get("gcode_injection")),
                        ams_mapping=json.dumps(ams) if ams else None,
                        plate_id=plate_no,
                        position=position + 1,
                        required_filament_types=json.dumps(sorted({s.get("type") for s in slots if s.get("type")})),
                        filament_overrides=json.dumps(overrides) if overrides else None,
                        print_time_seconds=(sliced.file_metadata or {}).get("print_time_seconds")
                        or plate.print_time_seconds,
                    )
                    db.add(item)
                    await db.flush()
                    job.status, job.queue_item_id, job.printer_id = "queued", item.id, printer["id"]
                    job.plate_id, job.pieces = plate.id, _pieces(plate, comp, own)
                    # What this item prints: checked again before it starts.
                    job.queued_as = {"objects": _plate_objects(plate), "file_hash": file_hash}
                    if overridden:  # the queue says the colour was not checked
                        job.note = (
                            f"Sent to {printer['name']} without the spool check (Accoda): "
                            "material and colour not checked"
                        )[:255]
                    else:
                        job.note = (
                            None if fits else f"Load the {job.color} spool on {printer['name']}, then start it"[:255]
                        )
                    busy.add(printer["id"])
                    created += 1
                    placed = True
                    logger.info(
                        "Farm job %d: %s queued on %s (plate %d)", job.id, job.component_code, printer["name"], plate.id
                    )
                    job_events.record(
                        "queued",
                        f"In the queue of {printer['name']}"
                        + ("" if fits else f": load the {job.color} spool, then start it by hand"),
                        job_id=job.id,
                        queue_item_id=item.id,
                        printer_id=printer["id"],
                        level=None if fits else "warning",
                        detail={
                            "plate_id": plate.id,
                            "plate": plate_no,
                            "file": send.filename,
                            "library_file_id": send.id,
                            "file_hash": file_hash,
                            "manual_start": item.manual_start,
                            "ams_mapping": ams,
                            "filament_overrides": overrides,
                            "chosen_printer": options.get("printer_id") or job.printer_id,
                        },
                    )
                    break
                if placed:
                    break
        if not placed:
            fallback = "Chosen printer is not active" if job.printer_id else "No free printer of a matching model"
            job.note = ("; ".join(dict.fromkeys(reasons)) or fallback)[:255]
    for job in jobs:  # what each job waits for, only when it changed (not every tick)
        if job.status == "waiting" and job.note and job.note != notes_before.get(job.id):
            failed = any(w in job.note for w in ("failed", "not reachable", "timed out", "can't", "No reference"))
            job_events.record("waiting", job.note, job_id=job.id, level="warning" if failed else None)
    await db.commit()
    return created


async def cancel_job(db: AsyncSession, job: FarmPrintJob, who: dict | None = None) -> None:
    """Cancel a job; its queue item too while it has not started (the queue history says by whom)."""
    if job.source:
        raise HTTPException(409, "A print outside the queue is not cancelled: say it is not production")
    if job.queue_item_id:
        item = await db.get(PrintQueueItem, job.queue_item_id)
        if item is not None and item.status == "printing":
            raise HTTPException(409, "Already printing: stop it from the printer or the queue")
        if item is not None and item.status == "pending" and item.dispatching_at is not None:
            raise HTTPException(409, "Being sent to the printer right now: try again in a minute")
        if item is not None and item.status == "pending":
            item.status = "cancelled"
            item.completed_at = datetime.now(timezone.utc)
            item.error_message = "Cancelled with its farm job"
            item.failure_detail = {"phase": "dispatch", **(who or {})}
    job.status = "cancelled"
    await db.commit()
    job_events.record("cancelled", "Cancelled", job_id=job.id, queue_item_id=job.queue_item_id, detail=who)


async def _guarded(db: AsyncSession, what: str, step) -> None:
    try:
        await step(db)
    except Exception:
        logger.exception("Farm %s failed", what)
        await db.rollback()


class FarmJobDispatcher:
    """Periodic placement of farm jobs (same pattern as the farm library scheduler)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="farm-job-dispatcher")

    def stop(self) -> None:
        from backend.app.farm import alerts, auto_spools, library_problems, slicer_filaments

        if self._task is not None:
            self._task.cancel()
            self._task = None
        alerts.stop_check()
        slicer_filaments.stop_check()
        auto_spools.stop_check()
        library_problems.stop_check()
        notify.cancel_pending()

    async def _loop(self) -> None:
        from backend.app.farm import alerts, auto_spools, library_problems, printer_files, slicer_filaments
        from backend.app.farm.jobs import outside

        last_cleanup = 0.0
        last_history = -HISTORY_EVERY_SECONDS
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                async with database.async_session() as db:
                    await dispatch_once(db)
                    alerts.start_check()  # its own task: Seller Hub's speed never delays the dispatch
                    # Each on its own: one failing never stops the dispatch or the others.
                    await _guarded(db, "colour sampling", record_used_colors)
                    await _guarded(db, "excluded objects", record_excluded_objects)
                    await _guarded(db, "prints outside the queue", outside.scan_archives)
                    if time.monotonic() - last_history > HISTORY_EVERY_SECONDS:
                        last_history = time.monotonic()
                        await _guarded(db, "Klipper history", outside.scan_klipper_history)
                        # its own task, like the alerts: an import never delays the dispatch
                        slicer_filaments.start_check()
                        auto_spools.start_check()  # spools for the filaments the 3MFs use, own task too
                        library_problems.start_check()  # new 3MF versions checked, own task too
                    if time.monotonic() - last_cleanup > CLEANUP_EVERY_SECONDS:
                        last_cleanup = time.monotonic()
                        await printer_files.cleanup(db)
                        await _guarded(db, "job events clean-up", job_events.cleanup)
                        await _guarded(db, "archives left printing", outcome.close_stale_archives)
            except asyncio.CancelledError:
                break
            except Exception:  # pragma: no cover - defensive, keep the loop alive
                logger.exception("Farm job dispatcher failed")


farm_job_dispatcher = FarmJobDispatcher()
