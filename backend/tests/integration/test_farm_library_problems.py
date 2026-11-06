"""Printfarm (fork): production 3MFs that will not print, told when saved. Made-up projects; no slicer, no notification sent."""

from datetime import datetime
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from backend.app.farm import (
    library_problems as lp,
    slicing,  # noqa: F401  (loaded before library_path is patched below)
)
from backend.app.farm.library_problems import FarmLibraryProblem
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.models import FarmPlate, FarmPrinterModelMap
from backend.app.models.library import LibraryFile


def problems(objects, *, model_ok=True):
    return [(plate, code) for plate, code, _ in lp.static_problems(objects, model="Sovol Zero", model_ok=model_ok)]


def test_each_problem_without_slicing():
    assert problems({1: 2}) == []
    assert problems({1: 2, 2: 0}) == [(2, "empty_plate")]
    assert problems({1: 2}, model_ok=False) == [(None, "printer_model")]


@pytest.fixture
async def project(db_session):
    lib = LibraryFile(filename="maniglia.3mf", file_path="/nas/maniglia.3mf", file_type="3mf", file_size=1)
    db_session.add(lib)
    await db_session.flush()
    state = FarmLibraryFile(watch_folder_id=1, library_file_id=lib.id, path="/nas/maniglia.3mf", mtime=1, size=1)
    state.sha256, state.status = "v1", "ok"
    db_session.add(state)
    db_session.add(FarmPlate(library_file_id=lib.id, plate_index=1, status="confirmed"))  # a production file
    await db_session.commit()
    sent = []

    async def no_fetch(db, lib_file):
        return None

    with (
        patch("backend.app.farm.nas.cache.ensure_local", no_fetch),
        patch("backend.app.farm.nas.access.library_path", lambda p: p),
        patch.object(lp.notify, "notify", lambda event, key, **values: sent.append((event, key, values))),
    ):
        yield lib, state, sent


async def stored(db_session):
    rows = (await db_session.execute(select(FarmLibraryProblem).order_by(FarmLibraryProblem.id))).scalars().all()
    return [(r.plate, r.code) for r in rows]


def old_problem(file_id, code):
    return FarmLibraryProblem(library_file_id=file_id, plate=1, code=code, message="old", since=datetime(2026, 11, 1))


@pytest.mark.asyncio
async def test_a_new_version_is_checked_once_problems_told_once_per_file_and_gone_when_fixed(db_session, project):
    lib, state, sent = project
    with patch.object(lp, "read_project", lambda path: {1: 2, 2: 0, 3: 0}):
        assert await lp.check_new_versions(db_session) == 1
        assert await lp.check_new_versions(db_session) == 0  # same version: not read again
    assert await stored(db_session) == [(2, "empty_plate"), (3, "empty_plate")]
    assert len(sent) == 1 and sent[0][0] == "farm_library_problem"  # one message for the file, not one per plate
    assert "plate 2: plate 2 has no objects" in sent[0][2]["problems"]

    state.sha256 = "v2"  # the owner fixed it and saved it again
    await db_session.commit()
    with patch.object(lp, "read_project", lambda path: {1: 2, 2: 0}):
        await lp.check_new_versions(db_session)
    assert await stored(db_session) == [(2, "empty_plate")]
    assert len(sent) == 1  # nothing new: not told again


@pytest.mark.asyncio
async def test_problems_no_longer_checked_go_without_a_message(db_session, project):
    lib, state, sent = project
    for code in ("unknown_profile", "brand_spelling", "no_colour"):  # stored by the first version
        db_session.add(old_problem(lib.id, code))
    await db_session.commit()
    assert await lp.list_problems(db_session) == []  # hidden at once
    with patch.object(lp, "read_project", lambda path: {1: 2}):
        await lp.check_new_versions(db_session)
    assert await stored(db_session) == [] and sent == []  # and deleted at the next round


@pytest.mark.asyncio
async def test_a_failed_reslice_is_a_problem_explained_and_a_good_one_clears_it(db_session, project):
    lib, state, sent = project
    error = HTTPException(502, "Slicer CLI failed (500): G-code conflicts detected after slicing. try moving the wipe")
    await lp.record_slice(db_session, lib.id, 1, None, error)
    rows = (await db_session.execute(select(FarmLibraryProblem))).scalars().all()
    assert [(r.plate, r.code) for r in rows] == [(1, "slice_failed")]
    assert "paths of different objects overlap on the plate" in rows[0].message and "wipe" not in rows[0].message
    assert sent == []  # farm_slicing_failed already tells it

    await lp.record_slice(db_session, lib.id, 2, None)  # another plate sliced fine: plate 1 stays
    assert await stored(db_session) == [(1, "slice_failed")]
    await lp.record_slice(db_session, lib.id, 1, None)  # fixed: re-sliced fine
    assert await stored(db_session) == []


@pytest.mark.asyncio
async def test_the_list_for_the_page(async_client, db_session, project):
    lib, state, sent = project
    with patch.object(lp, "read_project", lambda path: {1: 0}):
        await lp.check_new_versions(db_session)
    got = (await async_client.get("/api/v1/farm/library-problems")).json()
    assert [(p["filename"], p["plate"], p["code"]) for p in got] == [("maniglia.3mf", 1, "empty_plate")]


@pytest.mark.asyncio
async def test_recording_a_reslice_never_breaks_it(db_session, project):
    lib, state, sent = project

    async def busy(*args, **kwargs):
        raise RuntimeError("database is locked")

    with patch.object(lp, "_replace", busy):
        await lp.record_slice(db_session, lib.id, 1, None, HTTPException(502, "boom"))  # nothing raised
    assert sent == [] and await stored(db_session) == []


@pytest.mark.asyncio
async def test_mapping_the_printer_model_clears_its_problem(db_session, project):
    lib, state, sent = project
    state.printer_model = "Sovol Zero"
    await db_session.commit()
    with patch.object(lp, "read_project", lambda path: {1: 2}):
        await lp.check_new_versions(db_session)
        assert await stored(db_session) == [(None, "printer_model")]
        db_session.add(FarmPrinterModelMap(source_model="sovol zero", target_model="K1"))  # mapped by hand
        await db_session.commit()
        await lp.check_new_versions(db_session)  # the mapping changed: same file version checked again
    assert await stored(db_session) == []


async def another_file(db_session, name):
    f = LibraryFile(filename=name, file_path=f"/nas/{name}", file_type="3mf", file_size=1)
    db_session.add(f)
    await db_session.flush()
    st = FarmLibraryFile(watch_folder_id=1, library_file_id=f.id, path=f"/nas/{name}", mtime=1, size=1)
    st.sha256, st.status = name, "ok"
    db_session.add(st)
    db_session.add(FarmPlate(library_file_id=f.id, plate_index=1, status="confirmed"))
    return f


@pytest.mark.asyncio
async def test_one_odd_file_does_not_stop_the_round(db_session, project):
    lib, state, sent = project
    await another_file(db_session, "altro.3mf")
    await db_session.commit()

    def read(path):
        if path.endswith("maniglia.3mf"):
            raise RuntimeError("something unexpected in this file")
        return {1: 0}

    with patch.object(lp, "read_project", read):
        assert await lp.check_new_versions(db_session) == 2
    assert await stored(db_session) == [(1, "empty_plate")]  # the other file was still checked


@pytest.mark.asyncio
async def test_many_files_with_new_problems_send_one_summary(db_session, project):
    lib, state, sent = project
    for n in range(6):
        await another_file(db_session, f"f{n}.3mf")
    await db_session.commit()
    with patch.object(lp, "read_project", lambda path: {1: 0}):
        await lp.check_new_versions(db_session)
    assert len(await stored(db_session)) == 7
    assert len(sent) == 1 and sent[0][2]["problems"].startswith("7 file da sistemare")


@pytest.mark.asyncio
async def test_only_files_the_farm_prints_from_are_checked(db_session, project):
    lib, state, sent = project
    other = await another_file(db_session, "18650 Case Holder.3mf")
    plate = (await db_session.execute(select(FarmPlate).where(FarmPlate.library_file_id == other.id))).scalar_one()
    plate.status = "proposed"  # nobody confirmed it, no product uses it: not the farm's
    db_session.add(old_problem(other.id, "empty_plate"))
    await db_session.commit()

    with patch.object(lp, "read_project", lambda path: {1: 0}):
        assert await lp.list_problems(db_session) == []  # hidden at once, before any round
        assert await lp.check_new_versions(db_session) == 1  # only the confirmed one
    assert await stored(db_session) == [(1, "empty_plate")]  # the old problem of the other file is gone
    assert len(sent) == 1 and sent[0][2]["filename"] == "maniglia.3mf"  # and it was not told
    await lp.record_slice(db_session, other.id, 1, None, HTTPException(502, "boom"))
    assert len(await stored(db_session)) == 1

    plate.status = "confirmed"  # it becomes a production file: checked at the next round
    await db_session.commit()
    with patch.object(lp, "read_project", lambda path: {1: 0}):
        assert await lp.check_new_versions(db_session) == 1
    assert len(await stored(db_session)) == 2

    plate.status = "proposed"  # and no longer: its problems go
    await db_session.commit()
    await lp.check_new_versions(db_session)
    assert await stored(db_session) == [(1, "empty_plate")]


@pytest.mark.asyncio
async def test_a_file_in_a_products_folder_is_a_production_file(db_session, project):
    lib, state, sent = project
    other = await another_file(db_session, "staffa.3mf")
    for plate in (await db_session.execute(select(FarmPlate))).scalars():
        plate.status = "proposed"
    await db_session.commit()

    async def in_product(db):
        return {other.id: {1}}

    with patch("backend.app.farm.products.folders.file_products", in_product):
        assert await lp.production_files(db_session) == {other.id}
