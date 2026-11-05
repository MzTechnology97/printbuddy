"""Printfarm (fork): the slicer server's filaments imported as local presets. Made-up profiles, no network."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.farm import slicer_filaments
from backend.app.models.local_preset import LocalPreset
from backend.app.models.spool import Spool


def write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def export(root, stamp):
    """What the slicer server writes: system catalogue, the PC's own filaments, then the manifest."""
    write(
        root,
        "bambu/system/BBL/filament/fdm_filament_pla.json",
        {"name": "fdm_filament_pla", "instantiation": "false", "filament_type": ["PLA"], "nozzle_temperature": ["220"]},
    )
    write(
        root,
        "bambu/system/BBL/filament/Bambu PLA Basic @BBL X1C.json",
        {
            "name": "Bambu PLA Basic @BBL X1C",
            "inherits": "fdm_filament_pla",
            "filament_vendor": ["Bambu Lab"],
            "instantiation": "true",
        },
    )
    write(
        root,
        "bambu/user/filament/Marca PLA @BBL X1C.json",
        {
            "name": "Marca PLA @BBL X1C",
            "inherits": "Bambu PLA Basic @BBL X1C",
            "filament_vendor": ["Marca"],
            "default_filament_colour": ["#112233"],
        },
    )
    write(
        root,
        "orca/user/filament/Marca PETG-CF @K1.json",
        {"name": "Marca PETG-CF @K1", "inherits": "Marca PETG-CF @System", "filament_vendor": ["Marca"]},
    )
    write(root, "orca/user/filament/A mano.json", {"name": "A mano", "filament_type": ["ASA"]})
    write(root, "manifest.json", {"hash": stamp})


@pytest.fixture
def no_github():
    """Orca bases are looked up on GitHub by upstream's resolver: never in tests."""

    async def resolve(data, profile_type, db, depth=0):
        return {**data, "filament_type": ["PETG-CF"]}

    with patch.object(slicer_filaments.orca_profiles, "resolve_preset", resolve):
        yield


async def presets(db_session):
    rows = (await db_session.execute(select(LocalPreset).order_by(LocalPreset.name))).scalars().all()
    return {r.name: r for r in rows}


@pytest.mark.asyncio
async def test_imports_custom_and_catalogue_filaments_with_brand_and_type(db_session, tmp_path, no_github):
    export(tmp_path, "v1")
    assert await slicer_filaments.sync(db_session, tmp_path) == {"added": 4, "updated": 0, "removed": 0, "total": 4}
    got = await presets(db_session)
    # The catalogue's bases are not filaments one picks; everything else is there, with its source.
    assert sorted(got) == ["A mano", "Bambu PLA Basic @BBL X1C", "Marca PETG-CF @K1", "Marca PLA @BBL X1C"]
    user = got["Marca PLA @BBL X1C"]
    assert (user.source, user.filament_vendor, user.filament_type, user.nozzle_temp_max) == (
        "Bambu Studio PC",
        "Marca",
        "PLA",
        220,  # type and temperature inherited from the catalogue
    )
    assert json.loads(user.default_filament_colour) == ["#112233"]
    orca = got["Marca PETG-CF @K1"]
    assert (orca.source, orca.filament_vendor, orca.filament_type) == ("OrcaSlicer PC", "Marca", "PETG-CF")
    assert got["Bambu PLA Basic @BBL X1C"].source == "Bambu Studio"
    assert {r.preset_type for r in got.values()} == {"filament"}


@pytest.mark.asyncio
async def test_nothing_new_nothing_done(db_session, tmp_path, no_github):
    assert await slicer_filaments.sync(db_session, tmp_path) is None  # no export yet
    export(tmp_path, "v1")
    assert (await slicer_filaments.sync(db_session, tmp_path))["added"] == 4
    assert await slicer_filaments.sync(db_session, tmp_path) is None  # same manifest: not read again


@pytest.mark.asyncio
async def test_changes_update_in_place_and_gone_ones_go_unless_a_spool_uses_them(db_session, tmp_path, no_github):
    export(tmp_path, "v1")
    await slicer_filaments.sync(db_session, tmp_path)
    before = await presets(db_session)
    db_session.add(Spool(material="ASA", slicer_filament=str(before["A mano"].id)))
    await db_session.commit()

    # On the PC: one filament changed, two deleted (one of them is on a spool).
    write(
        tmp_path,
        "bambu/user/filament/Marca PLA @BBL X1C.json",
        {"name": "Marca PLA @BBL X1C", "inherits": "Bambu PLA Basic @BBL X1C", "filament_vendor": ["Marca2"]},
    )
    (tmp_path / "orca/user/filament/A mano.json").unlink()
    (tmp_path / "orca/user/filament/Marca PETG-CF @K1.json").unlink()
    write(tmp_path, "manifest.json", {"hash": "v2"})
    assert await slicer_filaments.sync(db_session, tmp_path) == {"added": 0, "updated": 1, "removed": 1, "total": 2}

    after = await presets(db_session)
    assert after["Marca PLA @BBL X1C"].id == before["Marca PLA @BBL X1C"].id  # the spools' link holds
    assert after["Marca PLA @BBL X1C"].filament_vendor == "Marca2"
    assert "Marca PETG-CF @K1" not in after
    assert after["A mano"].id == before["A mano"].id  # still on a spool: kept


@pytest.mark.asyncio
async def test_presets_imported_or_added_by_hand_are_never_touched(db_session, tmp_path, no_github):
    mine = LocalPreset(name="A mano", preset_type="filament", source="manual", setting="{}", filament_type="PLA")
    db_session.add(mine)
    await db_session.commit()
    export(tmp_path, "v1")
    assert (await slicer_filaments.sync(db_session, tmp_path))["added"] == 3
    got = (await db_session.execute(select(LocalPreset).where(LocalPreset.name == "A mano"))).scalars().all()
    assert [(r.id, r.source, r.filament_type) for r in got] == [(mine.id, "manual", "PLA")]


@pytest.mark.asyncio
async def test_a_name_in_both_slicers_is_imported_once(db_session, tmp_path, no_github):
    export(tmp_path, "v1")
    write(tmp_path, "bambu/user/filament/A mano.json", {"name": "A mano", "filament_type": ["PLA"]})
    await slicer_filaments.sync(db_session, tmp_path)
    got = (await db_session.execute(select(LocalPreset).where(LocalPreset.name == "A mano"))).scalars().all()
    assert [r.source for r in got] == ["OrcaSlicer PC"]  # Orca, the slicer in use, wins


@pytest.mark.asyncio
async def test_a_broken_export_is_skipped(db_session, tmp_path, no_github):
    export(tmp_path, "v1")
    (tmp_path / "orca/user/filament/rotto.json").write_text("{non json", encoding="utf-8")
    (tmp_path / "manifest.json").write_text("{scrittura a metà", encoding="utf-8")
    assert await slicer_filaments.sync(db_session, tmp_path) is None  # manifest unreadable: next time
    write(tmp_path, "manifest.json", {"hash": "v1"})
    assert (await slicer_filaments.sync(db_session, tmp_path))["added"] == 4  # the broken file alone is skipped
