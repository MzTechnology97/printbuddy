"""Slice library projects with the settings embedded in the 3MF.

The project saved from the GUI already carries printer, process and filaments
(``Metadata/project_settings.config``), so no profile is resolved: the file goes
to the sidecar of the slicer that saved it (or the per-file override).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes import library as lib
from backend.app.api.routes.settings import get_setting
from backend.app.core.config import settings as app_settings
from backend.app.farm.detect import SLICEABLE, SLICER_ORCA
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.nas.cache import ensure_local
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
) -> SliceResponse:
    """Slice with embedded settings and save the ``.gcode.3mf`` next to the source."""
    slicer = resolve_slicer(state)
    await ensure_local(db, lib_file)  # NAS projects are fetched into the cache now, on use
    src = Path(lib_file.file_path)
    raw = await asyncio.to_thread(src.read_bytes)
    # The version id of the source (a zip-directory hash for NAS files): tells later whether the output is stale.
    source_sha = state.sha256
    model_bytes = lib._sanitize_project_settings_sentinels(raw)

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
    out_path = lib.get_library_files_dir() / f"{uuid.uuid4().hex}.gcode.3mf"
    out_path.write_bytes(result.content)

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
        }
    )
    new_file = LibraryFile(
        folder_id=lib_file.folder_id,
        filename=f"{base_name}.gcode.3mf",
        file_path=lib.to_relative_path(out_path),
        file_type="gcode",
        file_size=len(result.content),
        file_hash=hashlib.sha256(result.content).hexdigest(),
        thumbnail_path=thumbnail,
        file_metadata=metadata,
        source_type="sliced",
    )
    db.add(new_file)
    await db.commit()
    return SliceResponse(
        library_file_id=new_file.id,
        name=new_file.filename,
        print_time_seconds=result.print_time_seconds,
        filament_used_g=filament_g,
        filament_used_mm=filament_mm,
        used_embedded_settings=True,
    )
