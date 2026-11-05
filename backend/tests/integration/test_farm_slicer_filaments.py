"""Printfarm (fork): the slicer server's filaments imported as local presets. Made-up profiles, no network."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.api.routes.settings import get_setting
from backend.app.farm import slicer_filaments
from backend.app.models.local_preset import LocalPreset
from backend.app.models.slot_preset import SlotPresetMapping
from backend.app.models.spool import Spool


def write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def manifest(root, stamp, **wrong):
    counts = {f: len(list((root / f).rglob("*.json"))) for f in slicer_filaments.SOURCES}
    counts.update(wrong)
    write(root, "manifest.json", {"hash": stamp, "counts": counts})


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
    manifest(root, stamp)


def _session_factory(db_session):
    class _Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


@pytest.fixture
def lookups(db_session):
    """The test DB for the import's sessions; Orca bases from a fake GitHub, never the real one."""
    asked = []

    async def resolve(data, profile_type, db, depth=0):
        asked.append(data["inherits"])
        if data["inherits"].endswith("@System"):
            return {**data, "filament_type": ["PETG-CF"]}
        return data  # not found

    with (
        patch("backend.app.core.database.async_session", _session_factory(db_session)),
        patch.object(slicer_filaments.orca_profiles, "resolve_preset", resolve),
    ):
        yield asked


async def presets(db_session):
    rows = (await db_session.execute(select(LocalPreset).order_by(LocalPreset.name))).scalars().all()
    return {r.name: r for r in rows}


@pytest.mark.asyncio
async def test_imports_custom_and_catalogue_filaments_with_brand_and_type(db_session, tmp_path, lookups):
    export(tmp_path, "v1")
    assert await slicer_filaments.sync(tmp_path) == {"added": 4, "updated": 0, "removed": 0, "total": 4}
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
    assert lookups == ["Marca PETG-CF @System"]


@pytest.mark.asyncio
async def test_nothing_new_nothing_done(db_session, tmp_path, lookups):
    assert await slicer_filaments.sync(tmp_path) is None  # no export yet
    export(tmp_path, "v1")
    assert (await slicer_filaments.sync(tmp_path))["added"] == 4
    assert await slicer_filaments.sync(tmp_path) is None  # same manifest: not read again


@pytest.mark.asyncio
async def test_changes_update_in_place_and_gone_ones_go_unless_a_spool_or_slot_uses_them(
    db_session, tmp_path, lookups, printer_factory
):
    write(tmp_path, "orca/user/filament/Su uno slot.json", {"name": "Su uno slot", "filament_type": ["PLA"]})
    for i in (1, 2):  # filaments that stay, so this is not "most of them gone"
        write(tmp_path, f"orca/user/filament/Resta {i}.json", {"name": f"Resta {i}", "filament_type": ["PLA"]})
    export(tmp_path, "v1")
    await slicer_filaments.sync(tmp_path)
    before = await presets(db_session)
    printer = await printer_factory(name="X1C")
    db_session.add_all(
        [
            Spool(material="ASA", slicer_filament=str(before["A mano"].id)),
            SlotPresetMapping(
                printer_id=printer.id,
                ams_id=0,
                tray_id=1,
                preset_id=str(before["Su uno slot"].id),
                preset_name="Su uno slot",
                preset_source="local",
            ),
        ]
    )
    await db_session.commit()

    # On the PC: one filament changed, three deleted (one on a spool, one on an AMS slot).
    write(
        tmp_path,
        "bambu/user/filament/Marca PLA @BBL X1C.json",
        {"name": "Marca PLA @BBL X1C", "inherits": "Bambu PLA Basic @BBL X1C", "filament_vendor": ["Marca2"]},
    )
    for name in ("A mano", "Su uno slot", "Marca PETG-CF @K1"):
        (tmp_path / f"orca/user/filament/{name}.json").unlink()
    manifest(tmp_path, "v2")
    assert await slicer_filaments.sync(tmp_path) == {"added": 0, "updated": 1, "removed": 1, "total": 4}

    after = await presets(db_session)
    assert after["Marca PLA @BBL X1C"].id == before["Marca PLA @BBL X1C"].id  # the spools' link holds
    assert after["Marca PLA @BBL X1C"].filament_vendor == "Marca2"
    assert "Marca PETG-CF @K1" not in after
    assert after["A mano"].id == before["A mano"].id  # still on a spool: kept
    assert after["Su uno slot"].id == before["Su uno slot"].id  # still on an AMS slot: kept


@pytest.mark.asyncio
async def test_a_filament_moved_to_the_other_slicer_keeps_its_id(db_session, tmp_path, lookups):
    export(tmp_path, "v1")
    await slicer_filaments.sync(tmp_path)
    before = (await presets(db_session))["Marca PLA @BBL X1C"]
    (tmp_path / "bambu/user/filament/Marca PLA @BBL X1C.json").unlink()
    write(
        tmp_path, "orca/user/filament/Marca PLA @BBL X1C.json", {"name": "Marca PLA @BBL X1C", "filament_type": ["PLA"]}
    )
    manifest(tmp_path, "v2")
    await slicer_filaments.sync(tmp_path)
    after = (await presets(db_session))["Marca PLA @BBL X1C"]
    assert (after.id, after.source) == (before.id, "OrcaSlicer PC")


@pytest.mark.asyncio
async def test_presets_imported_or_added_by_hand_are_never_touched(db_session, tmp_path, lookups):
    mine = LocalPreset(name="A mano", preset_type="filament", source="manual", setting="{}", filament_type="PLA")
    db_session.add(mine)
    await db_session.commit()
    export(tmp_path, "v1")
    assert (await slicer_filaments.sync(tmp_path))["added"] == 3
    got = (await db_session.execute(select(LocalPreset).where(LocalPreset.name == "A mano"))).scalars().all()
    assert [(r.id, r.source, r.filament_type) for r in got] == [(mine.id, "manual", "PLA")]


@pytest.mark.asyncio
async def test_a_name_in_both_slicers_is_imported_once(db_session, tmp_path, lookups):
    write(tmp_path, "bambu/user/filament/A mano.json", {"name": "A mano", "filament_type": ["PLA"]})
    export(tmp_path, "v1")
    await slicer_filaments.sync(tmp_path)
    got = (await db_session.execute(select(LocalPreset).where(LocalPreset.name == "A mano"))).scalars().all()
    assert [r.source for r in got] == ["OrcaSlicer PC"]  # Orca, the slicer in use, wins


@pytest.mark.asyncio
async def test_an_incomplete_or_broken_export_changes_nothing(db_session, tmp_path, lookups):
    export(tmp_path, "v1")
    await slicer_filaments.sync(tmp_path)
    before = await presets(db_session)

    # A copy cut short: files missing for the manifest.
    (tmp_path / "orca/user/filament/A mano.json").unlink()
    (tmp_path / "orca/user/filament/Marca PETG-CF @K1.json").unlink()
    manifest(tmp_path, "v2", **{"orca/user": 2})
    assert await slicer_filaments.sync(tmp_path) is None
    # An unreadable file with a valid manifest.
    (tmp_path / "orca/user/filament/rotto.json").write_text("{non json", encoding="utf-8")
    manifest(tmp_path, "v3")
    assert await slicer_filaments.sync(tmp_path) is None
    # A manifest being written.
    (tmp_path / "manifest.json").write_text("{scrittura a metà", encoding="utf-8")
    assert await slicer_filaments.sync(tmp_path) is None

    assert {n: r.id for n, r in (await presets(db_session)).items()} == {n: r.id for n, r in before.items()}
    assert await get_setting(db_session, slicer_filaments.HASH_KEY) == "v1"  # tried again next time


@pytest.mark.asyncio
async def test_an_export_that_lost_most_filaments_removes_none(db_session, tmp_path, lookups, caplog):
    export(tmp_path, "v1")
    await slicer_filaments.sync(tmp_path)
    for path in list((tmp_path / "orca").rglob("*.json")) + list((tmp_path / "bambu").rglob("*.json")):
        path.unlink()
    manifest(tmp_path, "v2")  # an empty export, consistent with its manifest
    assert (await slicer_filaments.sync(tmp_path))["removed"] == 0
    assert len(await presets(db_session)) == 4
    assert "none removed" in caplog.text


@pytest.mark.asyncio
async def test_an_import_cut_short_is_done_again(db_session, tmp_path, lookups):
    export(tmp_path, "v1")
    real = slicer_filaments._fields
    calls = []

    def breaks(*args):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("disco pieno")
        return real(*args)

    with (
        patch.object(slicer_filaments, "BATCH", 1),
        patch.object(slicer_filaments, "_fields", breaks),
        pytest.raises(RuntimeError),
    ):
        await slicer_filaments.sync(tmp_path)
    await db_session.rollback()
    assert await get_setting(db_session, slicer_filaments.HASH_KEY) is None  # not marked as done
    assert (await slicer_filaments.sync(tmp_path))["total"] == 4
    assert sorted(await presets(db_session)) == [
        "A mano",
        "Bambu PLA Basic @BBL X1C",
        "Marca PETG-CF @K1",
        "Marca PLA @BBL X1C",
    ]


@pytest.mark.asyncio
async def test_github_not_answering_stops_the_lookups(db_session, tmp_path, lookups):
    export(tmp_path, "v1")
    for i in range(3):  # three more Orca filaments, each on its own missing base
        write(tmp_path, f"orca/user/filament/Altro {i}.json", {"name": f"Altro {i}", "inherits": f"Base {i}"})
    write(tmp_path, "orca/user/filament/Stessa base.json", {"name": "Stessa base", "inherits": "Base 0"})
    manifest(tmp_path, "v1")
    clock = iter([0.0, 30.0])  # the first miss takes 30 s: GitHub is not answering
    with patch.object(slicer_filaments, "monotonic", lambda: next(clock, 60.0)):
        counts = await slicer_filaments.sync(tmp_path)
    assert counts["added"] == 8  # all imported, with their own fields
    assert lookups == ["Base 0"]  # one slow miss, then no more; "Base 0" is not asked twice


@pytest.mark.asyncio
async def test_the_local_preset_list_works_without_loading_the_settings(async_client, db_session):
    db_session.add(LocalPreset(name="Grande", preset_type="filament", source="Bambu Studio", setting='{"x": 1}'))
    await db_session.commit()
    got = (await async_client.get("/api/v1/local-presets/")).json()
    assert [p["name"] for p in got["filament"]] == ["Grande"]
    assert "setting" not in got["filament"][0]
