"""Slice library projects with the settings embedded in the 3MF.

The project saved from the GUI already carries printer, process and filaments
(``Metadata/project_settings.config``), so no profile is resolved: the file goes
to the sidecar of the slicer that saved it (or the per-file override).
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import time
import uuid
import zipfile
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import library as lib
from backend.app.api.routes.settings import get_setting
from backend.app.core import database
from backend.app.core.config import settings as app_settings
from backend.app.farm import notify, variants
from backend.app.farm.detect import PROJECT_SETTINGS_ENTRY, SLICEABLE, SLICER_ORCA
from backend.app.farm.library_sync import _drop_thumbnail
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.nas import library as nas_library
from backend.app.farm.nas.access import library_path
from backend.app.farm.nas.cache import ensure_local
from backend.app.farm.plates.models import FarmPlate
from backend.app.models.library import LibraryFile
from backend.app.schemas.slicer import SliceResponse
from backend.app.services.slicer_api import (
    SlicerApiServerError,
    SlicerApiService,
    SlicerApiUnavailableError,
    SlicerInputError,
)

logger = logging.getLogger(__name__)


async def sidecar_url(db: AsyncSession, slicer: str) -> str:
    """Same settings as the upstream slice route, but chosen per file."""
    if slicer == SLICER_ORCA:
        return ((await get_setting(db, "orcaslicer_api_url")) or app_settings.slicer_api_url).strip()
    return ((await get_setting(db, "bambu_studio_api_url")) or app_settings.bambu_studio_api_url).strip()


def fix_printer_inherits(model_bytes: bytes) -> bytes:
    """Point the printer entry of ``inherits_group`` (the last one) back at the project's own printer.

    Re-slicing a project without external presets, the slicer CLI takes that entry as the printer's
    system name and refuses unless it is in ``print_compatible_printers`` ("The selected printer is
    not compatible with the process preset in the 3mf"); the GUI doesn't check it. Some OrcaSlicer
    betas write a filament there, and a user printer may name its system parent while the list holds
    the user printer. When the project's printer is in the list, an empty entry ("the printer preset
    itself") makes the CLI accept the pair it was saved with. Only the bytes sent to the slicer change.
    """
    with zipfile.ZipFile(io.BytesIO(model_bytes)) as zin:
        try:
            cfg = json.loads(zin.read(PROJECT_SETTINGS_ENTRY))
        except (KeyError, ValueError):
            return model_bytes
        group, compatible = cfg.get("inherits_group"), cfg.get("print_compatible_printers")
        if not (
            isinstance(group, list)
            and group
            and group[-1]
            and isinstance(compatible, list)
            and cfg.get("printer_settings_id") in compatible
            and group[-1] not in compatible
        ):
            return model_bytes
        logger.info("Farm slice: printer inherits %r replaced by the project's printer for the CLI", group[-1])
        group[-1] = ""
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in zin.infolist():
                data = json.dumps(cfg, indent=4) if info.filename == PROJECT_SETTINGS_ENTRY else zin.read(info.filename)
                zout.writestr(info, data)
    return out.getvalue()


def plate_key(plate: int | None) -> int:
    """``None`` (the project's default plate) and 1 are the same output; 0 is "all plates"."""
    return 1 if plate is None else plate


def output_name(base: str, plate: int | None, colour: str | None = None) -> str:
    key = plate_key(plate)
    name = base if key == 1 else f"{base} - all plates" if key == 0 else f"{base} - plate {key}"
    return f"{name} - {colour}.gcode.3mf" if colour else f"{name}.gcode.3mf"


async def previous_output(
    db: AsyncSession, project_id: int, plate: int | None, colour: str | None = None
) -> LibraryFile | None:
    """The output a new slice of this project plate (in this colour variant) replaces (the latest one)."""
    key, found = plate_key(plate), None
    rows = await db.execute(LibraryFile.active().where(LibraryFile.source_type == "sliced").order_by(LibraryFile.id))
    for out in rows.scalars():
        meta = out.file_metadata or {}
        if (
            meta.get("used_embedded_settings")
            and "extracted_from_library_file_id" not in meta  # Klipper .gcode taken out of an output
            and meta.get("sliced_from_library_file_id") == project_id
            and plate_key(meta.get("sliced_plate")) == key
            and colour_key(meta.get("sliced_color")) == colour_key(colour)
        ):
            found = out
    return found


def colour_key(colour: str | None) -> str | None:
    """How colour variants are compared (``sliced_color``): the name as a key of the farm's colour table."""
    return (colour or "").strip().lower() or None


def resolve_slicer(state: FarmLibraryFile) -> str:
    if state.status != "ok":
        raise HTTPException(status_code=409, detail=f"File is {state.status}, not sliceable")
    if state.slicer not in SLICEABLE:
        raise HTTPException(
            status_code=400,
            detail=f"No sidecar for slicer '{state.slicer}'. Set an override (orcaslicer / bambu_studio).",
        )
    return state.slicer


async def slice_embedded(
    db: AsyncSession,
    *,
    lib_file: LibraryFile,
    state: FarmLibraryFile,
    plate: int | None,
    job_id: int | None = None,
    colour: str | None = None,
) -> SliceResponse:
    """Slice with embedded settings and save the ``.gcode.3mf`` next to the source.

    With ``colour`` (a component's colour name) the plate prints with the 3MF's filament of that
    colour: if its objects are on another one, a copy moved to it is sliced, saved as its own output
    (see ``variants``); otherwise this is the plain output.
    """
    slicer = resolve_slicer(state)
    await ensure_local(db, lib_file)  # NAS projects are fetched into the cache now, on use
    src = library_path(lib_file.file_path)
    raw = await asyncio.to_thread(src.read_bytes)
    # The version id of the source (a zip-directory hash for NAS files): tells later whether the output is stale.
    source_sha = state.sha256
    model_bytes = await asyncio.to_thread(fix_printer_inherits, lib._sanitize_project_settings_sentinels(raw))
    filament = await variant_of(db, model_bytes, plate, colour) if colour_key(colour) else 0
    if filament:
        model_bytes = await asyncio.to_thread(variants.move_plate_to, model_bytes, plate_key(plate), filament)
    else:
        colour = None  # the plain output already prints in this colour (or the plate is multi-colour)

    progress_request_id = on_progress = None
    if job_id is not None:
        from backend.app.services.slice_dispatch import slice_dispatch

        progress_request_id = str(uuid.uuid4())

        def on_progress(snapshot: dict) -> None:
            slice_dispatch.set_progress(job_id, snapshot)

    service = SlicerApiService(await sidecar_url(db, slicer))
    try:
        result = await service.slice_without_profiles(
            model_bytes=model_bytes,
            model_filename=lib_file.filename,
            plate=plate,
            export_3mf=True,
            request_id=progress_request_id,
            on_progress=on_progress,
        )
    except SlicerInputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (SlicerApiServerError, SlicerApiUnavailableError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await service.close()

    base_name = lib_file.filename.removesuffix(".3mf")
    # A new slice of the same plate replaces the previous output (same row, same file): queued
    # prints and plates follow it, and printers get it under the same name.
    previous = await previous_output(db, lib_file.id, plate, colour)
    # NAS project: the output goes on the NAS next to it, the local file is only its cache copy.
    try:
        saved = await nas_library.save_sliced(
            db, lib_file, output_name(base_name, plate, colour), result.content, replaces=previous
        )
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Cannot save the sliced file on the NAS: {exc}"[:500]) from exc
    if saved is not None:
        out_path = saved.path
    else:
        reuse = previous is not None and not previous.is_external
        out_path = (
            library_path(previous.file_path) if reuse else lib.get_library_files_dir() / f"{uuid.uuid4().hex}.gcode.3mf"
        )
        tmp = out_path.with_name(f".{out_path.name}.part")
        tmp.write_bytes(result.content)
        os.replace(tmp, out_path)

    thumbnail = None
    parsed: dict = {}
    try:
        raw = lib.ThreeMFParser(str(out_path)).parse()
        thumb = raw.get("_thumbnail_data")
        if thumb:
            thumb_path = lib.get_library_thumbnails_dir() / f"{uuid.uuid4().hex}{raw.get('_thumbnail_ext', '.png')}"
            thumb_path.write_bytes(thumb)
            thumbnail = lib.to_relative_path(thumb_path)
        parsed = lib._clean_3mf_metadata(raw) or {}
    except Exception as exc:
        logger.warning("Failed to parse sliced 3MF metadata for %s: %s", base_name, exc)

    filament_g = result.filament_used_g or parsed.get("filament_used_grams") or 0.0
    filament_mm = result.filament_used_mm or parsed.get("filament_used_mm") or 0.0
    metadata = dict(lib._without_print_name(parsed) or {})
    metadata.update(
        {
            "print_time_seconds": result.print_time_seconds,
            "filament_used_g": filament_g,
            "filament_used_mm": filament_mm,
            "used_embedded_settings": True,
            "sliced_from_library_file_id": lib_file.id,
            "sliced_from_sha256": source_sha,
            "sliced_plate": plate,
            "sliced_with": slicer,
            "sliced_color": colour.strip() if colour else None,  # as the component writes it: "Nero"
            "sliced_filament": filament or None,
        }
    )
    new_file = previous or LibraryFile(file_type="gcode", source_type="sliced")
    if previous is not None:
        _drop_thumbnail(previous.thumbnail_path)
    else:
        new_file.folder_id = saved.folder_id if saved else lib_file.folder_id
        new_file.filename = saved.filename if saved else output_name(base_name, plate, colour)
        db.add(new_file)
    new_file.is_external = saved is not None
    new_file.file_path = str(out_path) if saved else lib.to_relative_path(out_path)
    new_file.file_size = len(result.content)
    new_file.file_hash = hashlib.sha256(result.content).hexdigest()
    new_file.thumbnail_path = thumbnail
    new_file.file_metadata = metadata
    if saved is not None:
        await db.flush()
        await nas_library.track_sliced(db, new_file, saved, result.content)
    await db.commit()
    return SliceResponse(
        library_file_id=new_file.id,
        name=new_file.filename,
        print_time_seconds=result.print_time_seconds,
        filament_used_g=filament_g,
        filament_used_mm=filament_mm,
        used_embedded_settings=True,
    )


# ---------------------------------------------------------------- colour variants

_variant_cache: dict[tuple, int | str] = {}  # (file, version, plate, colour, hex, tolerance) -> filament or error
_variant_asked: dict[tuple[int, int, str], float] = {}  # (library_file_id, plate, colour) -> when slicing was asked


async def variant_of(db: AsyncSession, source: bytes | Path, plate: int | None, colour: str) -> int:
    """The filament to move the plate to for ``colour`` (0: slice it as it is); 409 when the 3MF lacks it."""
    from backend.app.farm.plan import service as plan

    config = await plan.load_config(db)
    hex_ = config["colors"].get(colour_key(colour))
    if not hex_:
        raise HTTPException(status_code=409, detail=f"il colore {colour} non è nella tabella colori della farm")
    try:
        return await asyncio.to_thread(
            variants.variant_filament, source, plate_key(plate), colour.strip(), hex_, config["tolerance"]
        )
    except variants.MissingColour as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


async def _variant_filament(
    db: AsyncSession, lib_file: LibraryFile, state: FarmLibraryFile, plate: int, colour: str
) -> int:
    """``variant_of`` for the current version of the project, read once per version (and colour table)."""
    from backend.app.farm.plan import service as plan

    config = await plan.load_config(db)
    key = (
        lib_file.id,
        state.sha256,
        plate,
        colour_key(colour),
        config["colors"].get(colour_key(colour)),
        config["tolerance"],
    )
    if key not in _variant_cache:
        await ensure_local(db, lib_file)
        try:
            _variant_cache[key] = await variant_of(db, library_path(lib_file.file_path), plate, colour)
        except HTTPException as exc:
            _variant_cache[key] = str(exc.detail)
    found = _variant_cache[key]
    if isinstance(found, str):
        raise HTTPException(status_code=409, detail=found)
    return found


async def variant_slice(db: AsyncSession, plate: FarmPlate, color: str | None) -> LibraryFile | None:
    """The sliced output of this plate in the component's colour, for the current project version.

    The plain output when the plate is already on the filament of that colour (or is multi-colour);
    None when not sliced yet, or when the 3MF has no filament of that colour (see ``ask_variant_slicing``).
    """
    from backend.app.farm.plates.service import _latest_slices

    lib_file = await db.get(LibraryFile, plate.library_file_id)
    state = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == plate.library_file_id))
    if lib_file is None or state is None:
        return None
    variant = None
    if colour_key(color):
        try:
            if await _variant_filament(db, lib_file, state, plate.plate_index, color):
                variant = colour_key(color)
        except HTTPException:
            return None
    return (await _latest_slices(db, lib_file, state.sha256, variant)).get(plate.plate_index)


async def ask_variant_slicing(db: AsyncSession, plate: FarmPlate, color: str | None) -> None:
    """``ask_slicing`` for the component's colour: a missing colour fails the slice job with its reason."""
    from backend.app.farm.jobs import service as jobs
    from backend.app.services.slice_dispatch import http_exception_to_job_error, slice_dispatch

    if not colour_key(color):
        await jobs.ask_slicing(db, plate)
        return
    key = (plate.library_file_id, plate.plate_index, colour_key(color))
    if time.monotonic() - _variant_asked.get(key, -jobs.RESLICE_AFTER_SECONDS) < jobs.RESLICE_AFTER_SECONDS:
        return
    _variant_asked[key] = time.monotonic()
    lib_file = await db.get(LibraryFile, plate.library_file_id)
    if lib_file is None:
        return
    name = lib_file.filename

    async def run(job_id: int):
        async with database.async_session() as task_db:
            task_file = await task_db.get(LibraryFile, key[0])
            state = await task_db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == key[0]))
            try:
                response = await slice_embedded(
                    task_db, lib_file=task_file, state=state, plate=key[1], job_id=job_id, colour=color
                )
            except Exception as exc:
                notify.slicing_done(key[0], name, key[1], color, notify.QUEUE_SLICE, exc)
                if isinstance(exc, HTTPException):
                    raise http_exception_to_job_error(exc) from exc
                raise
        notify.slicing_done(key[0], name, key[1], color, notify.QUEUE_SLICE)
        return response.model_dump()

    task = await slice_dispatch.enqueue(kind="library_file", source_id=key[0], source_name=f"{name} ({color})", run=run)
    jobs.slice_jobs[key] = task.id
