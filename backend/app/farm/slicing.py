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
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import library as lib
from backend.app.api.routes.settings import get_setting
from backend.app.core import database
from backend.app.core.config import settings as app_settings
from backend.app.farm import notify, variants
from backend.app.farm.detect import PROJECT_SETTINGS_ENTRY, SLICEABLE, SLICER_BAMBU, SLICER_ORCA
from backend.app.farm.library_sync import _drop_thumbnail
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.nas import library as nas_library
from backend.app.farm.nas.access import library_path
from backend.app.farm.nas.cache import ensure_local
from backend.app.farm.plates.models import FarmPlate
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
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


async def _queued(db: AsyncSession, output: LibraryFile) -> bool:
    """Whether a queue item that hasn't started prints this output, or a Klipper G-code taken out of it."""
    rows = await db.execute(
        select(LibraryFile)
        .join(PrintQueueItem, PrintQueueItem.library_file_id == LibraryFile.id)
        .where(PrintQueueItem.status == "pending")
    )
    return any(
        f.id == output.id or (f.file_metadata or {}).get("extracted_from_library_file_id") == output.id
        for f in rows.scalars()
    )


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
    filename = output_name(base_name, plate, colour)
    # A new slice of the same plate replaces the previous output (same row, same file): plates
    # follow it, and printers get it under the same name. Not one a queued print still waits on:
    # that keeps its bytes, and the new slice gets a row and a name of its own (``jobs.service``
    # moves the print to it when its pieces are the same).
    previous = await previous_output(db, lib_file.id, plate, colour)
    if previous is not None and await _queued(db, previous):
        previous = None
        filename = filename.replace(".gcode.3mf", f" - {uuid.uuid4().hex[:8]}.gcode.3mf")
    # NAS project: the output goes on the NAS next to it, the local file is only its cache copy.
    try:
        saved = await nas_library.save_sliced(db, lib_file, filename, result.content, replaces=previous)
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
        new_file.filename = saved.filename if saved else filename
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
    found = (await _latest_slices(db, lib_file, state.sha256, variant)).get(plate.plate_index)
    if found is not None:  # sliced, by the queue or another way (re-slice, by hand): no failure to tell any more
        _failed.pop(_queue_key(plate.library_file_id, plate.plate_index, color), None)
    return found


async def ask_variant_slicing(db: AsyncSession, plate: FarmPlate, color: str | None) -> bool:
    """``ask_slicing`` for the component's colour: a missing colour fails the slice job with its reason.

    True when a slice was queued now (False: asked a short while ago, or the 3MF is gone).
    """
    from backend.app.farm.jobs import service as jobs
    from backend.app.services.slice_dispatch import http_exception_to_job_error, slice_dispatch

    if not colour_key(color):
        return await jobs.ask_slicing(db, plate)
    key = (plate.library_file_id, plate.plate_index, colour_key(color))
    if time.monotonic() - _variant_asked.get(key, -jobs.RESLICE_AFTER_SECONDS) < jobs.RESLICE_AFTER_SECONDS:
        return False
    _variant_asked[key] = time.monotonic()
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
                    task_db, lib_file=task_file, state=state, plate=key[1], job_id=job_id, colour=color
                )
            except Exception as exc:
                queue_slice_ended(key[0], name, key[1], color, state.slicer if state else None, exc)
                if isinstance(exc, HTTPException):
                    raise http_exception_to_job_error(exc) from exc
                raise
        queue_slice_ended(key[0], name, key[1], color, state.slicer if state else None)
        return response.model_dump()

    task = await slice_dispatch.enqueue(kind="library_file", source_id=key[0], source_name=f"{name} ({color})", run=run)
    from backend.app.farm import job_events

    job_events.record(
        "slice_requested",
        f"Slice asked: {name}, plate {key[1]} in {color}",
        file_id=key[0],
        plate=key[1],
        colour=key[2],
        detail={"slice_job": getattr(task, "id", None), "file": name, "colour": color},
    )
    return True


# ---------------------------------------------------------------- why a queue slice is still missing

SLICER_NAMES = {SLICER_ORCA: "OrcaSlicer", SLICER_BAMBU: "Bambu Studio"}
EARLY_RETRY_SECONDS = 60  # a sidecar back after "not reachable": sliced again at most this often
PROBE_TIMEOUT_SECONDS = 2.0  # /health of a sidecar: short, the dispatcher waits for it
PROBE_BACKOFF_SECONDS = (60, 300)  # after a failed probe: 60 s, doubling up to 5 minutes
_failed: dict[tuple[int, int, str | None], dict] = {}  # (file, plate, colour) -> the last queue slice that failed
_versions: dict[int, str | None] = {}  # file -> the project version the remembered failures and waits are of
_health: dict[str, tuple[float, bool, float]] = {}  # sidecar url -> (next probe, answered, wait after a failure)


def failure_kind(error: BaseException) -> str:
    """``unreachable``: the sidecar couldn't be connected to (refused, no route, connect timeout). ``timeout``: it
    took the request but didn't answer in time, and may still be slicing it. ``error``: anything else, its own."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "unreachable"
        if isinstance(seen, httpx.TimeoutException):
            return "timeout"
        seen = seen.__cause__
    return "error"


# The slicer CLI's words for a project it refuses, and what they mean. The GUI only warns of path conflicts
# (at a layer, naming the objects) and still slices; the CLI stops, with a hint about the wipe tower even
# when there is none. Seen with tree supports of copies placed close together.
_EXPLAINED = (
    (
        "G-code conflicts detected",
        "paths of different objects overlap on the plate, usually the supports of objects placed too close: "
        "space the objects out in the project (Orca shows the conflict as a warning at a layer) and save it",
    ),
)


def explain(reason: str) -> str:
    return next((text for marker, text in _EXPLAINED if marker in reason), reason)


def _queue_key(file_id: int, plate: int | None, colour: str | None) -> tuple[int, int, str | None]:
    return (file_id, plate_key(plate), colour_key(colour))


def queue_slice_ended(
    file_id: int, filename: str, plate: int | None, colour: str | None, slicer: str | None, error=None
) -> None:
    """A slice asked for the queue ended (also notified, ``notify.slicing_done``).

    A failure is logged and kept for the job's note, with its kind (``failure_kind``); a success forgets it.
    """
    notify.slicing_done(file_id, filename, plate, colour, notify.QUEUE_SLICE, error)
    key = _queue_key(file_id, plate, colour)
    from backend.app.farm import job_events

    asked = _asked_at(key)
    took = round(time.monotonic() - asked) if asked is not None else None
    where = f"{filename}, plate {key[1]}" + (f" in {colour}" if colour else "")
    name = SLICER_NAMES.get(slicer, slicer or "an unknown slicer")
    if error is None:
        _failed.pop(key, None)
        job_events.record(
            "slice_done",
            f"Sliced with {name}: {where}",
            file_id=file_id,
            plate=key[1],
            colour=key[2],
            detail={"slicer": slicer, "seconds": took, "file": filename},
        )
        return
    reason = str(getattr(error, "detail", None) or f"{type(error).__name__}: {error}")
    kind = failure_kind(error)
    logger.warning(
        "Farm slice of %s plate %s%s on %s failed%s: %s",
        filename,
        key[1],
        f" ({colour})" if colour else "",
        SLICER_NAMES.get(slicer, slicer or "an unknown slicer"),
        {"unreachable": " (slicer not reachable)", "timeout": " (timed out)"}.get(kind, ""),
        reason,
    )
    _failed[key] = {"reason": explain(reason)[:200], "slicer": slicer, "kind": kind, "at": time.monotonic()}
    job_events.record(
        "slice_failed",
        f"{name} failed on {where}: {explain(reason)}",
        file_id=file_id,
        plate=key[1],
        colour=key[2],
        detail={
            "slicer": slicer,
            "kind": kind,
            "seconds": took,
            "file": filename,
            "error": reason,  # the slicer's whole answer, for the debug details
            "error_type": type(error).__name__,
            "cause": repr(error.__cause__) if error.__cause__ else None,
        },
    )


async def _answers(db: AsyncSession, slicer: str) -> bool:
    """Whether the slicer's sidecar answers its /health now.

    One short probe per sidecar per dispatcher tick, however many jobs wait on it; after a failed probe
    the next one waits ``PROBE_BACKOFF_SECONDS`` (doubling), so a host that doesn't answer at all doesn't
    slow every tick down.
    """
    from backend.app.farm.jobs.service import TICK_SECONDS

    url = await sidecar_url(db, slicer)
    next_at, ok, wait = _health.get(url, (0.0, False, 0.0))
    if time.monotonic() < next_at:
        return ok
    service = SlicerApiService(url)
    try:
        await asyncio.wait_for(service.health(), PROBE_TIMEOUT_SECONDS)
        ok = True
    except Exception:  # refused, no answer in time, or any answer but a good one
        ok = False
    finally:
        await service.close()
    first, most = PROBE_BACKOFF_SECONDS
    wait = 0.0 if ok else min(most, wait * 2 if wait else first)
    _health[url] = (time.monotonic() + (wait or TICK_SECONDS), ok, wait)
    return ok


def _asked_at(key: tuple[int, int, str | None]) -> float | None:
    from backend.app.farm.jobs import service as jobs

    return _variant_asked.get(key) if key[2] else jobs._slicing.get(key[:2])


def _forget_asked(key: tuple[int, int, str | None]) -> None:
    from backend.app.farm.jobs import service as jobs

    if key[2]:
        _variant_asked.pop(key, None)
    else:
        jobs._slicing.pop(key[:2], None)


async def _forget_old_version(db: AsyncSession, file_id: int) -> None:
    """The project was saved again (new version): its old failures and waits no longer hold, slice it now."""
    from backend.app.farm.jobs import service as jobs

    sha = await db.scalar(select(FarmLibraryFile.sha256).where(FarmLibraryFile.library_file_id == file_id))
    if _versions.setdefault(file_id, sha) == sha:
        return
    _versions[file_id] = sha
    for store in (_failed, _variant_asked, jobs._slicing):
        for key in [k for k in store if k[0] == file_id]:
            store.pop(key)


async def queue_slice_note(db: AsyncSession, plate: FarmPlate, color: str | None) -> str:
    """Ask for the plate's slice in the component's colour (``ask_variant_slicing``) and say what the job waits for.

    "is being sliced", or why the last slice failed and when it is tried again: a sidecar that couldn't be
    reached is tried again as soon as its /health answers; a slicer error, after ``RESLICE_AFTER_SECONDS``.
    """
    from backend.app.farm.jobs.service import RESLICE_AFTER_SECONDS

    await _forget_old_version(db, plate.library_file_id)
    key = _queue_key(plate.library_file_id, plate.plate_index, color)
    failed = _failed.get(key)
    if (
        failed is not None
        and failed["kind"] == "unreachable"
        and failed["slicer"]
        and time.monotonic() - failed["at"] >= EARLY_RETRY_SECONDS
        and await _answers(db, failed["slicer"])
    ):
        _forget_asked(key)  # back: sliced again now, not after RESLICE_AFTER_SECONDS
    if await ask_variant_slicing(db, plate, color):
        _failed.pop(key, None)
        failed = None
    where = f"plate {plate.plate_index} of file {plate.library_file_id}"
    if failed is None:
        return f"{where} is being sliced"
    asked = _asked_at(key)
    left = RESLICE_AFTER_SECONDS - (time.monotonic() - asked) if asked is not None else 0
    at = f"{datetime.now(timezone.utc) + timedelta(seconds=max(0, left)):%H:%M} UTC"
    if failed["kind"] == "unreachable":
        name = SLICER_NAMES.get(failed["slicer"], failed["slicer"] or "slicer")
        return f"{where}: {name} not reachable, sliced again as soon as it answers (at the latest at {at})"
    # The time before the reason: a job note is cut at 255 characters, and the reason can be long.
    if failed["kind"] == "timeout":
        return f"{where}: slicing timed out, trying again at {at}: {failed['reason']}"
    return f"{where}: slicing failed, trying again at {at}: {failed['reason']}"
