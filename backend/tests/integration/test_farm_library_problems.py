"""Printfarm (fork): 3MFs that will not work, told when saved. Made-up projects; no slicer, no notification sent."""

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
from backend.app.models.library import LibraryFile

BRANDS = lp.known_brands(
    ["Qidi PET-CF @X2D", "Qidi PET-CF @K1", "Quidi PET-CF@K1", "SUNLU PLA @K1", "SUNLU PETG @K2", "Sunlu ASA @K1"],
    ["Siraya Tech", "Siraya Tech", "IEMAI"],
)
GOOD = {"filament_settings_id": ["SUNLU PLA @K1"], "filament_colour": ["#000000"]}


def problems(cfg, objects=None, slots=None, *, model_ok=True, presets=("SUNLU PLA @K1",)):
    objects = {1: 2} if objects is None else objects
    slots = {1: {1}} if slots is None else slots
    found = lp.static_problems(
        cfg, objects, slots, model="Sovol Zero", model_ok=model_ok, presets=set(presets), brands=BRANDS
    )
    return [(plate, code) for plate, code, _ in found], [m for _, _, m in found]


def test_a_good_file_has_no_problems():
    assert problems(GOOD) == ([], [])


def test_each_problem_without_slicing():
    assert problems(GOOD, {1: 2, 2: 0})[0] == [(2, "empty_plate")]
    assert problems(GOOD, model_ok=False)[0] == [(None, "printer_model")]
    assert problems({**GOOD, "filament_colour": [""]})[0] == [(1, "no_colour")]
    codes, messages = problems({**GOOD, "filament_settings_id": ["Mio PLA @K1"]})
    assert codes == [(1, "unknown_profile")] and "'Mio PLA @K1' is not among the imported ones" in messages[0]


@pytest.mark.parametrize(
    ("profile", "hint"),
    [
        ("Quidi PET-CF@K1", "did you mean 'Qidi'"),  # close to a brand most profiles use
        ("Siraya Ttech PET-CF@Sovol", "did you mean 'Siraya Tech'"),
        ("Sunlu ASA @K1", "usually written 'SUNLU'"),  # same brand, spelt unlike most
        ("SUNLU PLA @K1", None),
        ("IEMAI PETG-CF @K1", None),
    ],
)
def test_brand_spelling(profile, hint):
    found = lp.brand_problem(profile, BRANDS)
    assert (hint in found) if hint else found is None


@pytest.fixture
async def project(db_session):
    lib = LibraryFile(filename="maniglia.3mf", file_path="/nas/maniglia.3mf", file_type="3mf", file_size=1)
    db_session.add(lib)
    await db_session.flush()
    state = FarmLibraryFile(watch_folder_id=1, library_file_id=lib.id, path="/nas/maniglia.3mf", mtime=1, size=1)
    state.sha256, state.status = "v1", "ok"
    db_session.add(state)
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


@pytest.mark.asyncio
async def test_a_new_version_is_checked_once_problems_told_once_per_file_and_gone_when_fixed(db_session, project):
    lib, state, sent = project
    bad = ({"filament_settings_id": ["Mio PLA"], "filament_colour": [""]}, {1: 2, 2: 0}, {1: {1}})
    with patch.object(lp, "read_project", lambda path: bad):
        assert await lp.check_new_versions(db_session) == 1
        assert await lp.check_new_versions(db_session) == 0  # same version: not read again
    assert await stored(db_session) == [(2, "empty_plate"), (1, "no_colour"), (1, "unknown_profile")]
    assert len(sent) == 1 and sent[0][0] == "farm_library_problem"  # one message for the file, not one per plate
    assert "plate 2: plate 2 has no objects" in sent[0][2]["problems"]

    state.sha256 = "v2"  # the owner fixed it and saved it again
    await db_session.commit()
    fixed = ({**GOOD, "filament_settings_id": ["Mio PLA"]}, {1: 2}, {1: {1}})
    with patch.object(lp, "read_project", lambda path: fixed):
        await lp.check_new_versions(db_session)
    assert await stored(db_session) == [(1, "unknown_profile")]
    assert len(sent) == 1  # nothing new: not told again


@pytest.mark.asyncio
async def test_a_failed_reslice_is_a_problem_explained_and_a_good_one_clears_it(db_session, project):
    lib, state, sent = project
    error = HTTPException(502, "Slicer CLI failed (500): G-code conflicts detected after slicing. try moving the wipe")
    await lp.record_slice(db_session, lib.id, 1, None, error)
    rows = (await db_session.execute(select(FarmLibraryProblem))).scalars().all()
    assert [(r.plate, r.code) for r in rows] == [(1, "slice_failed")]
    assert "paths of different objects overlap on the plate" in rows[0].message and "wipe" not in rows[0].message
    assert len(sent) == 1

    await lp.record_slice(db_session, lib.id, 2, None)  # another plate sliced fine: plate 1 stays
    assert await stored(db_session) == [(1, "slice_failed")]
    await lp.record_slice(db_session, lib.id, 1, None)  # fixed: re-sliced fine
    assert await stored(db_session) == []


@pytest.mark.asyncio
async def test_the_list_for_the_page(async_client, db_session, project):
    lib, state, sent = project
    with patch.object(lp, "read_project", lambda path: (GOOD, {1: 0}, {})):
        await lp.check_new_versions(db_session)
    got = (await async_client.get("/api/v1/farm/library-problems")).json()
    assert [(p["filename"], p["plate"], p["code"]) for p in got] == [("maniglia.3mf", 1, "empty_plate")]
