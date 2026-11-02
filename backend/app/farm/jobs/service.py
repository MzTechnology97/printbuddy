"""Accoda: farm jobs with alternative plates, turned into upstream queue items on a free printer.

The upstream queue item has one file and one printer. A farm job keeps the list of plates
(one per printer model, usually); every tick the dispatcher looks for an idle printer of a
model that has one of them sliced, with the plate's material and the component's colour
loaded, and only then creates the queue item, pinned to that printer, with the colour of
the loaded spool forced. Starting it stays with the upstream scheduler, so its plate-clear
confirmation still applies: nothing prints without the user saying the plate is clear.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import database
from backend.app.core.config import settings
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plan import service as plan
from backend.app.farm.plates.models import FarmPlate
from backend.app.farm.plates.printer_map import PrinterModelResolver
from backend.app.farm.plates.service import _latest_slices
from backend.app.farm.products.models import FarmComponent
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.filament_requirements import extract_filament_requirements
from backend.app.services.print_scheduler import _canonical_filament_type
from backend.app.services.printer_providers.factory import normalize_provider

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
CLEANUP_EVERY_SECONDS = 24 * 3600  # old queue files on the printers
RESLICE_AFTER_SECONDS = 30 * 60  # a failed slice is tried again after this
ACTIVE_QUEUE = ("pending", "printing")
_slicing: dict[tuple[int, int], float] = {}  # (library_file_id, plate) -> when slicing was asked


def _pieces(plate: FarmPlate, comp: FarmComponent) -> int:
    return sum(
        y.quantity
        for y in plate.yields
        if (comp.shape_id and y.shape_id == comp.shape_id) or (not comp.shape_id and y.component_code == comp.code)
    )


async def sliced_file(db: AsyncSession, plate: FarmPlate) -> LibraryFile | None:
    """The sliced output of this plate for the current version of its project, if any."""
    lib_file = await db.get(LibraryFile, plate.library_file_id)
    state = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == plate.library_file_id))
    if lib_file is None or state is None:
        return None
    return (await _latest_slices(db, lib_file, state.sha256)).get(plate.plate_index)


async def ask_slicing(db: AsyncSession, plate: FarmPlate) -> None:
    """Slice the plate on its slicer's sidecar in the background (once; again after a while if it failed)."""
    key = (plate.library_file_id, plate.plate_index)
    if time.monotonic() - _slicing.get(key, -RESLICE_AFTER_SECONDS) < RESLICE_AFTER_SECONDS:
        return
    _slicing[key] = time.monotonic()
    from backend.app.farm.slicing import slice_embedded
    from backend.app.services.slice_dispatch import http_exception_to_job_error, slice_dispatch

    lib_file = await db.get(LibraryFile, plate.library_file_id)
    if lib_file is None:
        return

    async def run(job_id: int):
        async with database.async_session() as task_db:
            task_file = await task_db.get(LibraryFile, key[0])
            state = await task_db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == key[0]))
            try:
                response = await slice_embedded(task_db, lib_file=task_file, state=state, plate=key[1], job_id=job_id)
            except HTTPException as exc:
                raise http_exception_to_job_error(exc) from exc
        return response.model_dump()

    await slice_dispatch.enqueue(kind="library_file", source_id=key[0], source_name=lib_file.filename, run=run)


async def create_jobs(db: AsyncSession, requests: list[dict]) -> list[FarmPrintJob]:
    """One job per request ``{component_code, plate_ids}``; plates not sliced yet are sliced now."""
    jobs = []
    for req in requests:
        comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == req["component_code"]))
        if comp is None or comp.retired_at is not None:
            raise HTTPException(422, f"Unknown or retired component {req['component_code']}")
        plates = []
        for plate_id in dict.fromkeys(req["plate_ids"]):
            plate = await db.get(FarmPlate, plate_id)
            if plate is None or plate.status != "confirmed" or not _pieces(plate, comp):
                raise HTTPException(422, f"Plate {plate_id} is not a confirmed plate of {comp.code}")
            plates.append(plate)
        if not plates:
            raise HTTPException(422, f"No plate for {comp.code}")
        job = FarmPrintJob(
            component_code=comp.code,
            color=comp.color,
            plate_ids=[p.id for p in plates],
            expected_pieces=_pieces(plates[0], comp),
        )
        db.add(job)
        jobs.append(job)
        for plate in plates:
            if await sliced_file(db, plate) is None:
                await ask_slicing(db, plate)
    await db.commit()
    return jobs


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
        elif item_status in ACTIVE_QUEUE:
            out[job.component_code] = out.get(job.component_code, 0) + (job.pieces or 0)
    return out


async def gcode_for_klipper(db: AsyncSession, sliced: LibraryFile, path: Path, plate: int) -> LibraryFile | None:
    """Moonraker prints plain G-code: the plate's ``Metadata/plate_N.gcode`` from the sliced 3MF, kept once."""
    from backend.app.api.routes import library as lib

    name = f"{sliced.filename.removesuffix('.gcode.3mf').removesuffix('.3mf')}_plate{plate}.gcode"
    for candidate in (await db.execute(LibraryFile.active().where(LibraryFile.filename == name))).scalars():
        meta = candidate.file_metadata or {}
        if meta.get("extracted_from_library_file_id") == sliced.id and meta.get("plate") == plate:
            return candidate
    try:
        with zipfile.ZipFile(path) as zf:
            data = zf.read(f"Metadata/plate_{plate}.gcode")
    except (KeyError, OSError, zipfile.BadZipFile):
        return None
    out = lib.get_library_files_dir() / f"{uuid.uuid4().hex}.gcode"
    await asyncio.to_thread(out.write_bytes, data)
    gcode = LibraryFile(
        folder_id=sliced.folder_id,
        filename=name,
        file_path=lib.to_relative_path(out),
        file_type="gcode",
        file_size=len(data),
        source_type="sliced",
        file_metadata={
            **(sliced.file_metadata or {}),
            "extracted_from_library_file_id": sliced.id,
            "plate": plate,
        },
    )
    db.add(gcode)
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


async def dispatch_once(db: AsyncSession) -> int:
    """Place waiting jobs on free printers that fit; returns how many queue items were created."""
    jobs = list(
        (await db.execute(select(FarmPrintJob).where(FarmPrintJob.status == "waiting").order_by(FarmPrintJob.id)))
        .scalars()
        .all()
    )
    if not jobs:
        return 0
    config = await plan.load_config(db)
    resolver = await PrinterModelResolver.load(db)
    printers_by_id = {
        p.id: p for p in (await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars().all()
    }
    snapshot = plan.printer_snapshot(list(printers_by_id.values()))
    busy = set(
        (
            await db.execute(
                select(PrintQueueItem.printer_id).where(
                    PrintQueueItem.status.in_(ACTIVE_QUEUE), PrintQueueItem.printer_id.isnot(None)
                )
            )
        ).scalars()
    )
    created = 0
    for job in jobs:
        comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == job.component_code))
        color_hex = config["colors"].get((job.color or "").lower())
        if comp is None or color_hex is None:
            job.note = f"No reference colour for '{job.color}'"
            continue
        reasons = []
        placed = False
        for plate_id in job.plate_ids:
            plate = await db.get(FarmPlate, plate_id)
            if plate is None or plate.status != "confirmed":
                continue
            sliced = await sliced_file(db, plate)
            if sliced is None:
                await ask_slicing(db, plate)
                reasons.append(f"plate {plate.plate_index} of file {plate.library_file_id} is being sliced")
                continue
            model = (resolver.resolve(plate.printer_model).target_model or plate.printer_model or "").lower()
            for printer in snapshot:
                if printer["model"].lower() != model or not printer["idle"] or printer["id"] in busy:
                    continue
                klipper = normalize_provider(printers_by_id[printer["id"]].provider) != "bambu"
                # Single-spool Klipper printers don't report what is loaded: queued for a manual start.
                unreadable = klipper and not printer["loaded"]
                spool = (
                    None
                    if unreadable
                    else _matching_spool(printer, plate.materials or [], color_hex, config["tolerance"])
                )
                if spool is None and not unreadable:
                    reasons.append(f"{printer['name']}: no {job.color} {'+'.join(plate.materials or [])} loaded")
                    continue
                path = await _local_path(db, sliced)
                plate_no = _plate_in_file(path, plate.plate_index)
                send = sliced
                if klipper and not sliced.filename.endswith(".gcode"):
                    send = await gcode_for_klipper(db, sliced, path, plate_no)
                    if send is None:
                        reasons.append(f"{printer['name']}: no G-code for plate {plate_no} in the sliced file")
                        continue
                slots = await asyncio.to_thread(extract_filament_requirements, path, plate_no)
                overrides = (
                    []
                    if spool is None
                    else [
                        {
                            "slot_id": s["slot_id"],
                            "type": s.get("type") or spool["type"],
                            "color": f"#{spool['color']}",
                            "force_color_match": True,
                        }
                        for s in slots
                    ]
                )
                position = (await db.scalar(select(func.max(PrintQueueItem.position)))) or 0
                item = PrintQueueItem(
                    printer_id=printer["id"],
                    library_file_id=send.id,
                    manual_start=spool is None,
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
                job.plate_id, job.pieces = plate.id, _pieces(plate, comp)
                job.note = None if spool else f"Check the {job.color} spool on {printer['name']}, then start it"[:255]
                busy.add(printer["id"])
                created += 1
                placed = True
                logger.info(
                    "Farm job %d: %s queued on %s (plate %d)", job.id, job.component_code, printer["name"], plate.id
                )
                break
            if placed:
                break
        if not placed:
            job.note = ("; ".join(dict.fromkeys(reasons)) or "No free printer of a matching model")[:255]
    await db.commit()
    return created


async def cancel_job(db: AsyncSession, job: FarmPrintJob) -> None:
    """Cancel a job; its queue item too while it has not started."""
    if job.queue_item_id:
        item = await db.get(PrintQueueItem, job.queue_item_id)
        if item is not None and item.status == "printing":
            raise HTTPException(409, "Already printing: stop it from the printer or the queue")
        if item is not None and item.status == "pending":
            item.status = "cancelled"
    job.status = "cancelled"
    await db.commit()


class FarmJobDispatcher:
    """Periodic placement of farm jobs (same pattern as the farm library scheduler)."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="farm-job-dispatcher")

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        from backend.app.farm import printer_files

        last_cleanup = 0.0
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                async with database.async_session() as db:
                    await dispatch_once(db)
                    if time.monotonic() - last_cleanup > CLEANUP_EVERY_SECONDS:
                        last_cleanup = time.monotonic()
                        await printer_files.cleanup(db)
            except asyncio.CancelledError:
                break
            except Exception:  # pragma: no cover - defensive, keep the loop alive
                logger.exception("Farm job dispatcher failed")


farm_job_dispatcher = FarmJobDispatcher()
