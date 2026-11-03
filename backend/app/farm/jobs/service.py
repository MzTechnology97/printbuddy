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
import json
import logging
import os
import re
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

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
from backend.app.farm.plates.service import _latest_slices
from backend.app.farm.products.folders import component_files, excluded_files, may_print
from backend.app.farm.products.models import FarmComponent
from backend.app.farm.products.shapes import ProductShapes, plate_yield, product_shapes
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.printer import Printer
from backend.app.services.filament_requirements import extract_filament_requirements
from backend.app.services.print_scheduler import _canonical_filament_type, scheduler
from backend.app.services.printer_manager import printer_manager
from backend.app.services.printer_providers.factory import normalize_provider

logger = logging.getLogger(__name__)

TICK_SECONDS = 30
HISTORY_EVERY_SECONDS = 10 * 60  # Klipper prints done while Printbuddy was off
CLEANUP_EVERY_SECONDS = 24 * 3600  # old queue files on the printers
RESLICE_AFTER_SECONDS = 30 * 60  # a failed slice is tried again after this
ACTIVE_QUEUE = ("pending", "printing")
EXTRACTED_VERSION = 2  # 2: with the plate's preview and layer info; older extractions are redone
QUEUE_FLAGS = ("manual_start", "require_previous_success", "auto_off_after", "gcode_injection")
USED_COLORS_KEY = "farm_used_colors"  # in a print's archive extra_data: {colour: samples taken while printing}
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
    name = lib_file.filename

    async def run(job_id: int):
        async with database.async_session() as task_db:
            task_file = await task_db.get(LibraryFile, key[0])
            state = await task_db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == key[0]))
            try:
                response = await slice_embedded(task_db, lib_file=task_file, state=state, plate=key[1], job_id=job_id)
            except Exception as exc:
                notify.slicing_done(key[0], name, key[1], None, notify.QUEUE_SLICE, exc)
                if isinstance(exc, HTTPException):
                    raise http_exception_to_job_error(exc) from exc
                raise
        notify.slicing_done(key[0], name, key[1], None, notify.QUEUE_SLICE)
        return response.model_dump()

    await slice_dispatch.enqueue(kind="library_file", source_id=key[0], source_name=name, run=run)


async def variant_file(db: AsyncSession, plate: FarmPlate, color: str | None) -> LibraryFile | None:
    """The plate's sliced output in the component's colour (``farm.slicing``); None until sliced."""
    from backend.app.farm.slicing import variant_slice

    return await variant_slice(db, plate, color)


async def ask_variant(db: AsyncSession, plate: FarmPlate, color: str | None) -> None:
    from backend.app.farm.slicing import ask_variant_slicing

    await ask_variant_slicing(db, plate, color)


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
            if await variant_file(db, plate, comp.color) is None:
                await ask_variant(db, plate, comp.color)
    await db.commit()
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
        elif item_status in ACTIVE_QUEUE:
            out[job.component_code] = out.get(job.component_code, 0) + (job.pieces or 0)
    return out


def klipper_gcode_name(sliced_name: str, plate: int) -> str:
    """``K2-Citroen AMI - plate 2.gcode.3mf`` -> ``K2-Citroen-AMI-plate-2.gcode`` (the printer adds its fingerprint)."""
    base = sliced_name.removesuffix(".gcode.3mf").removesuffix(".3mf")
    if not re.search(rf"plate[\s_-]*{plate}(?!\d)", base, re.IGNORECASE):
        base += f" plate {plate}"
    return f"{re.sub(r'[^A-Za-z0-9]+', '-', base).strip('-') or 'plate'}.gcode"


async def gcode_for_klipper(db: AsyncSession, sliced: LibraryFile, path: Path, plate: int) -> LibraryFile | None:
    """Moonraker prints plain G-code: the plate's ``Metadata/plate_N.gcode`` from the sliced 3MF, kept once.

    A sliced output replaced by a new slice keeps its row, so the extracted G-code is refreshed in
    its own row when the output's bytes change: Klipper queue items follow it like Bambu ones.
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


def colour_name(color: str, colors: dict[str, str], tolerance: int) -> str:
    """The colour table's name nearest a spool's colour, as components name it ("Nero"); else ``#rrggbb``."""
    color = color.lstrip("#").lower()[:6]
    near = min(colors.items(), key=lambda kv: plan.color_distance(kv[1], color), default=None)
    if near is not None and plan.color_distance(near[1], color) <= tolerance:
        return near[0].capitalize()
    return f"#{color}"


def used_color(extra: dict | None) -> str | None:
    """The colour most sampled while the print ran (see ``record_used_colors``)."""
    samples = (extra or {}).get(USED_COLORS_KEY) or {}
    return max(samples, key=samples.get) if samples else None


async def record_used_colors(db: AsyncSession) -> int:
    """Sample, on each running print's archive and farm job, the colour of the filament being fed.

    The AMS/CFS slot in use (or the external spool); a Klipper printer without CFS reports none,
    so its spool set by hand. The batch's colour is the one sampled most. Returns how many prints.
    """
    config = await plan.load_config(db)
    by_hand = {s.printer_id: s for s in (await db.execute(select(FarmPrinterSpool))).scalars()}
    sampled = 0
    for printer in (await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars().all():
        status = printer_manager.get_status(printer.id)
        if status is None or status.state != "RUNNING":
            continue
        trays = _loaded_trays(printer.id)
        if trays:
            tray = next((t for t in trays if t["global_tray_id"] == getattr(status, "tray_now", None)), None)
            if tray is None or not tray.get("color"):
                continue  # between two slots, or a tray without colour
            name = colour_name(tray["color"], config["colors"], config["tolerance"])
        elif printer.id in by_hand:
            name = by_hand[printer.id].color_name
        else:
            continue
        archive = await db.scalar(
            select(PrintArchive)
            .where(PrintArchive.printer_id == printer.id, PrintArchive.status == "printing")
            .order_by(PrintArchive.id.desc())
            .limit(1)
        )
        if archive is None:
            continue
        samples = dict((archive.extra_data or {}).get(USED_COLORS_KEY) or {})
        samples[name] = samples.get(name, 0) + 1
        archive.extra_data = {**(archive.extra_data or {}), USED_COLORS_KEY: samples}
        jobs = await db.execute(
            select(FarmPrintJob)
            .join(PrintQueueItem, PrintQueueItem.id == FarmPrintJob.queue_item_id)
            .where(PrintQueueItem.archive_id == archive.id)
        )
        for job in jobs.scalars():
            job.used_color = used_color(archive.extra_data)
        sampled += 1
    if sampled:
        await db.commit()
    return sampled


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
                    await ask_variant(db, plate, job.color)
                    slicing = True
                    reasons.append(f"plate {plate.plate_index} of file {plate.library_file_id} is being sliced")
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
                    fits = bool(mapped or auto or spool)
                    if not fits and not unreadable and strict:
                        used = sorted({f.get("type") for f in slots if f.get("type")}) or plate.materials or []
                        reasons.append(f"{printer['name']}: no {job.color} {'+'.join(used)} loaded")
                        continue
                    send = sliced
                    if klipper and not sliced.filename.endswith(".gcode"):
                        send = await gcode_for_klipper(db, sliced, path, plate_no)
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
                    job.note = None if fits else f"Load the {job.color} spool on {printer['name']}, then start it"[:255]
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
            fallback = "Chosen printer is not active" if job.printer_id else "No free printer of a matching model"
            job.note = ("; ".join(dict.fromkeys(reasons)) or fallback)[:255]
    await db.commit()
    return created


async def cancel_job(db: AsyncSession, job: FarmPrintJob) -> None:
    """Cancel a job; its queue item too while it has not started."""
    if job.source:
        raise HTTPException(409, "A print outside the queue is not cancelled: say it is not production")
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
        from backend.app.farm import alerts, printer_files
        from backend.app.farm.jobs import outside

        last_cleanup = 0.0
        last_history = -HISTORY_EVERY_SECONDS
        while True:
            try:
                await asyncio.sleep(TICK_SECONDS)
                async with database.async_session() as db:
                    await dispatch_once(db)
                    await alerts.check(db)  # never raises
                    await record_used_colors(db)
                    await outside.scan_archives(db)
                    if time.monotonic() - last_history > HISTORY_EVERY_SECONDS:
                        last_history = time.monotonic()
                        await outside.scan_klipper_history(db)
                    if time.monotonic() - last_cleanup > CLEANUP_EVERY_SECONDS:
                        last_cleanup = time.monotonic()
                        await printer_files.cleanup(db)
            except asyncio.CancelledError:
                break
            except Exception:  # pragma: no cover - defensive, keep the loop alive
                logger.exception("Farm job dispatcher failed")


farm_job_dispatcher = FarmJobDispatcher()
