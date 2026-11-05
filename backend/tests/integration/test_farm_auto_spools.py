"""Printfarm (fork): spools for the filaments the production 3MFs use. Made-up projects; the 3MF reading is
stubbed (its parts have their own tests), the rules on spools are not."""

from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.api.routes.settings import set_setting
from backend.app.farm import auto_spools
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.models import FarmPlate
from backend.app.models.library import LibraryFile
from backend.app.models.local_preset import LocalPreset
from backend.app.models.spool import Spool

# Slot -> (material, colour, profile, vendor) of each made-up project.
PROJECTS = {
    "maniglia.3mf": {1: ("PETG-CF", "#000000", "IEMAI PETG-CF @Sovol Zero", "IEMAI")},
    "gancio.3mf": {1: ("PETG-CF", "#000000", "IEMAI PETG-CF @K1", "IEMAI"), 2: ("PLA", "#FF00FF", "Strano", "X")},
    "tappo.3mf": {1: ("PLA", "#FFFFFF", "Bambu PLA Basic @BBL X1C", "Bambu Lab")},
}


def settings_of(name: str) -> dict:
    slots = PROJECTS[name]
    cols = list(zip(*(slots[i] for i in sorted(slots)), strict=True))
    return dict(
        zip(
            ("filament_type", "filament_colour", "filament_settings_id", "filament_vendor"),
            map(list, cols),
            strict=True,
        )
    )


@pytest.fixture
async def farm(db_session):
    """Confirmed plates of the projects above, each using all its slots; the colour table names black and white."""
    for n, name in enumerate(PROJECTS, 1):
        lib = LibraryFile(filename=name, file_path=f"/nas/{name}", file_type="3mf", file_size=1)
        db_session.add(lib)
        await db_session.flush()
        state = FarmLibraryFile(watch_folder_id=1, library_file_id=lib.id, path=f"/nas/{name}", mtime=1, size=1)
        state.sha256 = f"v{n}"
        db_session.add_all([state, FarmPlate(library_file_id=lib.id, plate_index=1, status="confirmed")])
    db_session.add(
        LocalPreset(
            name="IEMAI PETG-CF @Sovol Zero",
            preset_type="filament",
            source="OrcaSlicer PC",
            setting="{}",
            filament_vendor="IEMAI",
        )
    )
    await set_setting(db_session, "farm_plan_colors", '{"nero": "000000", "bianco": "ffffff"}')
    await db_session.commit()

    async def used(db, plate, lib_file, state):
        return set(PROJECTS[lib_file.filename])

    async def no_fetch(db, lib_file):
        return None

    class Session:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    with (
        patch.object(auto_spools, "_used_slots", used),
        patch.object(auto_spools, "_read_settings", lambda path: settings_of(str(path).rsplit("/", 1)[-1])),
        patch("backend.app.farm.nas.cache.ensure_local", no_fetch),
        patch("backend.app.farm.nas.access.library_path", lambda p: p),
        patch("backend.app.core.database.async_session", lambda: Session()),
    ):
        yield


async def spools(db_session):
    return (await db_session.execute(select(Spool).order_by(Spool.id))).scalars().all()


@pytest.mark.asyncio
async def test_one_spool_per_filament_used_linked_to_its_profile(db_session, farm):
    created = await auto_spools.sync()
    # IEMAI black is in two projects: one spool. The odd colour is not in the farm's table: no spool.
    assert sorted((c["brand"], c["material"], c["color_name"]) for c in created) == [
        ("Bambu Lab", "PLA", "Bianco"),
        ("IEMAI", "PETG-CF", "Nero"),
    ]
    iemai = next(s for s in await spools(db_session) if s.brand == "IEMAI")
    preset = (await db_session.execute(select(LocalPreset))).scalar_one()
    assert (iemai.rgba, iemai.label_weight, iemai.weight_used, iemai.note) == ("000000FF", 1000, 0, auto_spools.NOTE)
    assert (iemai.slicer_filament, iemai.slicer_filament_name) == (str(preset.id), "IEMAI PETG-CF @Sovol Zero")


@pytest.mark.asyncio
async def test_never_a_duplicate_of_a_spool_in_inventory(db_session, farm):
    db_session.add_all(
        [
            Spool(material="PETG-CF", brand="Iemai", rgba="000000FF", color_name="Black"),  # same, other spelling
            Spool(material="PLA", brand="Bambu", rgba="FFFFFFFF", archived_at=None),  # "Bambu" is "Bambu Lab"
        ]
    )
    await db_session.commit()
    assert await auto_spools.sync() == []
    assert len(await spools(db_session)) == 2


@pytest.mark.asyncio
async def test_a_spool_removed_by_hand_is_not_made_again(db_session, farm):
    await auto_spools.sync()
    for spool in await spools(db_session):
        await db_session.delete(spool)
    await db_session.commit()
    await set_setting(db_session, "farm_plan_tolerance", "81")  # something changed: it looks again
    await db_session.commit()
    assert await auto_spools.sync() == []
    assert await spools(db_session) == []


@pytest.mark.asyncio
async def test_nothing_new_nothing_done_and_it_can_be_turned_off(db_session, farm):
    await set_setting(db_session, auto_spools.ENABLED_KEY, "0")
    await db_session.commit()
    assert await auto_spools.sync() is None
    await set_setting(db_session, auto_spools.ENABLED_KEY, "1")
    await db_session.commit()
    assert len(await auto_spools.sync()) == 2
    assert await auto_spools.sync() is None  # nothing changed since


@pytest.mark.asyncio
async def test_no_spool_without_a_colour_the_farm_names(db_session, farm):
    PROJECTS["senza.3mf"] = {1: ("ASA", "", "Senza colore", "Y"), 2: ("ASA", "#zzzzzz", "Rotto", "Y")}
    try:
        lib = LibraryFile(filename="senza.3mf", file_path="/nas/senza.3mf", file_type="3mf", file_size=1)
        db_session.add(lib)
        await db_session.flush()
        state = FarmLibraryFile(watch_folder_id=1, library_file_id=lib.id, path="/nas/senza.3mf", mtime=1, size=1)
        db_session.add_all([state, FarmPlate(library_file_id=lib.id, plate_index=1, status="confirmed")])
        await db_session.commit()
        created = await auto_spools.sync()
    finally:
        del PROJECTS["senza.3mf"]
    assert all(c["material"] != "ASA" for c in created)


def test_brands_compare_like_inventory():
    assert auto_spools.same_brand("Bambu Lab", "BambuLab")
    assert auto_spools.same_brand("Bambu", "Bambu Lab")
    assert auto_spools.same_brand(None, "IEMAI")  # a spool without a brand counts
    assert not auto_spools.same_brand("Sunlu", "IEMAI")


@pytest.mark.asyncio
async def test_an_unreadable_3mf_is_skipped_not_the_whole_run(db_session, farm):
    from xml.etree import ElementTree as ET

    async def used(db, plate, lib_file, state):
        if lib_file.filename == "tappo.3mf":
            raise ET.ParseError("unbound prefix: line 33, column 3")  # seen in a production project
        return set(PROJECTS[lib_file.filename])

    with patch.object(auto_spools, "_used_slots", used):
        created = await auto_spools.sync()
    assert [(c["brand"], c["material"]) for c in created] == [("IEMAI", "PETG-CF")]


@pytest.mark.parametrize(
    ("ftype", "profile", "material"),
    [
        ("PETG", "CC3D PBT-Pro @P1S", "PBT"),  # the project keeps a generic type, the profile says what it is
        ("PETG", "IEMAI PETG-CF @SV08", "PETG-CF"),
        ("ABS", "eSUN-ABS-CF @BBL P2S", "ABS-CF"),
        ("PETG-CF", "CC3D-PBT-CF @X1C", "PBT-CF"),
        ("PLA", "Bambu PLA Basic @BBL X1C", "PLA"),
        ("PCTG", "3DJake PCTG Silver", "PCTG"),
        ("ASA", None, "ASA"),
    ],
)
def test_material_from_the_profile_name(ftype, profile, material):
    assert auto_spools.material_of(ftype, profile) == material


@pytest.mark.asyncio
async def test_a_generic_vendor_gives_way_to_the_profiles_brand(db_session, farm):
    PROJECTS["tipo.3mf"] = {1: ("ABS", "#000000", "eSUN-ABS-CF @BBL P2S", "Generic")}
    try:
        lib = LibraryFile(filename="tipo.3mf", file_path="/nas/tipo.3mf", file_type="3mf", file_size=1)
        db_session.add(lib)
        await db_session.flush()
        state = FarmLibraryFile(watch_folder_id=1, library_file_id=lib.id, path="/nas/tipo.3mf", mtime=1, size=1)
        db_session.add_all([state, FarmPlate(library_file_id=lib.id, plate_index=1, status="confirmed")])
        await db_session.commit()
        created = await auto_spools.sync()
    finally:
        del PROJECTS["tipo.3mf"]
    assert ("eSUN", "ABS-CF", "Nero") in [(c["brand"], c["material"], c["color_name"]) for c in created]


@pytest.mark.parametrize(
    ("profile", "brand"),
    [
        ("CC3D PBT-PRO @ X2D 0.4", "CC3D"),
        ("Qidi PET-CF @X2D 0.4", "Qidi"),
        ("Bambu Lab-PLA Basic", "Bambu Lab"),
        ("ASA-Carbon Fiber CF10 (IEMAI)", None),
        ("Strano", None),
    ],
)
def test_brand_from_the_profile_name(profile, brand):
    assert auto_spools.brand_from_name(profile) == brand


def test_cf_is_not_added_twice():
    assert auto_spools.material_of("PETG-CF", "IEMAI PETG-CF CF @ H2D 0.4 nozzle") == "PETG-CF"


@pytest.mark.parametrize(
    ("hex_", "profile", "name"),
    [
        ("404040", None, "grigio scuro"),  # was "marrone scuro", the nearest hue in the table
        ("161616", None, "nero"),
        ("8a8a8a", None, "grigio"),
        ("a7a9aa", None, "grigio chiaro"),
        ("ffffff", None, "bianco"),
        ("c0c0c0", "Bambu PLA Basic-Silver @BBL P1S", "grigio"),  # the profile names it
        ("000000", "Bambu ABS-GF Black E3D", "nero"),
        ("82573e", "Jayo Wood PLA @K1", "marrone"),  # a real brown, by the table
        ("ff00ff", None, None),  # not a farm colour
    ],
)
def test_colour_names(hex_, profile, name):
    from backend.app.farm.plan.service import DEFAULT_COLORS

    assert auto_spools.colour_name(hex_, profile, DEFAULT_COLORS, 80) == name


def test_pbt_glued_to_pro_is_pbt():
    assert auto_spools.material_of("PETG", "CC3D-PBTPro @X1C") == "PBT"
    assert auto_spools.brand_from_name("CC3D-PBTPro @X1C") == "CC3D"
