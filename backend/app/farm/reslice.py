"""Re-slice a project's outputs when its 3MF changes (saved again under the same name).

A new slice of a plate replaces the previous output: same library row, same file on the NAS
(see ``slicing.slice_embedded``). Queued prints point at that row, so they print the new G-code;
the printer gets it under the same name, replacing the old copy in its cache or storage. Plates
then take time and grams from the new output.

Only plates that already had an output are re-sliced. If the project is saved again while its
re-slice runs, the job slices the newest version once more before finishing (the last save wins).
"""

from __future__ import annotations

import logging

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core import database
from backend.app.farm.models import FarmLibraryFile
from backend.app.models.library import LibraryFile

logger = logging.getLogger(__name__)

_running: set[int] = set()  # project ids with a re-slice job in progress


async def sliced_plates(db: AsyncSession, project_ids: set[int]) -> dict[int, list[int | None]]:
    """Plates that already have a project-settings output, by project id."""
    from backend.app.farm.slicing import plate_key

    found: dict[int, dict[int, int | None]] = {}
    rows = await db.execute(LibraryFile.active().where(LibraryFile.source_type == "sliced"))
    for out in rows.scalars():
        meta = out.file_metadata or {}
        pid = meta.get("sliced_from_library_file_id")
        if meta.get("used_embedded_settings") and pid in project_ids and "extracted_from_library_file_id" not in meta:
            plate = meta.get("sliced_plate")
            found.setdefault(pid, {})[plate_key(plate)] = plate
    return {pid: list(plates.values()) for pid, plates in found.items()}


async def _reslice(db: AsyncSession, project_id: int, plates: list[int | None], job_id: int) -> dict:
    from backend.app.farm.plates.service import PlateReadError, refresh_plates
    from backend.app.farm.slicing import slice_embedded
    from backend.app.services.slice_dispatch import http_exception_to_job_error

    results, error = [], None
    while True:
        lib_file = await db.get(LibraryFile, project_id)
        state = await db.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == project_id))
        if lib_file is None or state is None:
            return {"resliced": results}
        version = state.sha256
        for plate in plates:
            try:
                response = await slice_embedded(db, lib_file=lib_file, state=state, plate=plate, job_id=job_id)
                results.append(response.model_dump())
            except HTTPException as exc:
                logger.warning("Auto re-slice of %s plate %s failed: %s", lib_file.filename, plate, exc.detail)
                error = exc
        await db.refresh(state)  # saved again while slicing? then slice the newest version too
        if state.sha256 == version:
            break
    try:
        await refresh_plates(db, lib_file, state)  # time and grams from the new outputs
    except PlateReadError as exc:
        logger.info("Auto re-slice: plates of %s not refreshed: %s", lib_file.filename, exc)
    if error is not None and not results:
        raise http_exception_to_job_error(error)
    return {"resliced": results}


async def schedule(db: AsyncSession, project_ids: list[int]) -> None:
    """Queue a re-slice job for each changed project that has outputs (one at a time per project)."""
    from backend.app.farm.products.folders import excluded_files
    from backend.app.services.slice_dispatch import slice_dispatch

    excluded = await excluded_files(db)
    for project_id, plates in (await sliced_plates(db, set(project_ids) - excluded)).items():
        if project_id in _running:
            continue  # the running job re-checks the version before it ends
        lib_file = await db.get(LibraryFile, project_id)
        if lib_file is None:
            continue
        _running.add(project_id)

        async def run(job_id: int, project_id: int = project_id, plates: list = plates) -> dict:
            try:
                async with database.async_session() as task_db:
                    return await _reslice(task_db, project_id, plates, job_id)
            finally:
                _running.discard(project_id)

        try:
            await slice_dispatch.enqueue(
                kind="library_file", source_id=project_id, source_name=lib_file.filename, run=run
            )
            logger.info("Auto re-slice queued for %s (plates %s)", lib_file.filename, plates)
        except Exception:
            _running.discard(project_id)
            logger.exception("Auto re-slice of %s could not be queued", lib_file.filename)
