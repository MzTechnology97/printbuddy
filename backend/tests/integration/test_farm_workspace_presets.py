"""Fork (printfarm): the 3MF Workspace's preset lists and resolved preset values (farm/workspace/presets.py)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from backend.app.api.routes import library
from backend.app.farm.workspace import presets, project, settings as ws
from backend.app.models.library import LibraryFile
from backend.app.models.local_preset import LocalPreset
from backend.app.models.orca_base_cache import OrcaBaseProfile
from backend.app.services import orca_profiles
from backend.tests.integration.test_farm_workspace import (
    add_file,
    entries,
    make_project,
    open_ws,
    project_settings,
    save,
)

API = "/api/v1/farm/workspace/presets"
BASES = {
    "Test Process 0.20 @Base": {"inherits": "fdm_test_common", "layer_height": "0.2", "wall_loops": "2"},
    "fdm_test_common": {"layer_height": "0.3", "sparse_infill_density": "15%", "wall_loops": "3"},
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing here goes out: any outbound request fails the test."""

    class Offline:
        def AsyncClient(*args, **kwargs):  # noqa: N802 - httpx's name
            raise AssertionError("an outbound request was made")

    monkeypatch.setattr(orca_profiles, "httpx", Offline)


@pytest.fixture
async def bases(db_session):
    """System presets come from the cached OrcaSlicer base profiles: test rows."""
    db_session.add_all(OrcaBaseProfile(name=n, profile_type="process", setting=json.dumps(d)) for n, d in BASES.items())
    await db_session.commit()


async def cache_rows(db) -> int:
    return len((await db.execute(select(OrcaBaseProfile))).scalars().all())


@pytest.mark.asyncio
async def test_lists_the_user_presets_of_a_section(async_client, db_session):
    db_session.add_all(
        [
            LocalPreset(name="My PLA", preset_type="filament", setting=json.dumps({"nozzle_temperature": ["210"]})),
            LocalPreset(
                name="Fine 0.12",
                preset_type="process",
                setting=json.dumps({"layer_height": "0.12"}),
                inherits="Test Process 0.20 @Base",
            ),
        ]
    )
    await db_session.commit()
    resp = await async_client.get(API, params={"section": "process"})
    assert resp.status_code == 200, resp.text
    assert [(p["name"], p["inherits"]) for p in resp.json()["user"]] == [("Fine 0.12", "Test Process 0.20 @Base")]
    assert (await async_client.get(API, params={"section": "plates"})).status_code == 422


@pytest.mark.asyncio
async def test_resolves_a_user_preset_over_its_parents(async_client, db_session, bases):
    row = LocalPreset(
        name="Fine 0.12",
        preset_type="process",
        setting=json.dumps({"inherits": "Test Process 0.20 @Base", "layer_height": "0.12"}),
        inherits="Test Process 0.20 @Base",
    )
    db_session.add(row)
    await db_session.commit()
    resp = await async_client.get(f"{API}/values", params={"section": "process", "source": "user", "id": row.id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Fine 0.12" and body["inherits"] == "Test Process 0.20 @Base"
    # the child's value wins, then its parent's, then the grandparent's
    assert body["values"] == {"layer_height": "0.12", "wall_loops": "2", "sparse_infill_density": "15%"}
    # a preset of another section is not found there
    assert body["missing_parent"] is None
    other = await async_client.get(f"{API}/values", params={"section": "filament", "source": "user", "id": row.id})
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_a_user_preset_whose_parent_is_unknown_says_so(async_client, db_session, bases):
    row = LocalPreset(
        name="Orphan",
        preset_type="process",
        setting=json.dumps({"layer_height": "0.12"}),
        inherits="Not a system preset",
    )
    db_session.add(row)
    await db_session.commit()
    resp = await async_client.get(f"{API}/values", params={"section": "process", "source": "user", "id": row.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["missing_parent"] == "Not a system preset"
    assert resp.json()["values"] == {"layer_height": "0.12"}  # only its own: the rest is unknown


@pytest.mark.asyncio
async def test_resolves_a_system_preset_by_name(async_client, bases):
    resp = await async_client.get(
        f"{API}/values", params={"section": "process", "source": "system", "name": "Test Process 0.20 @Base"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["values"] == {"layer_height": "0.2", "wall_loops": "2", "sparse_infill_density": "15%"}
    missing = await async_client.get(f"{API}/values", params={"section": "process", "source": "system", "name": "Nope"})
    assert missing.status_code == 404  # not cached: never fetched
    other = {"section": "filament", "source": "system", "name": "Test Process 0.20 @Base"}
    assert (await async_client.get(f"{API}/values", params=other)).status_code == 404  # another section's
    assert (
        await async_client.get(f"{API}/values", params={"section": "process", "source": "system"})
    ).status_code == 422


# --- preset switch (OrcaSlicer's preset combos): from the base-profile cache only, identity changed together ---

CACHED = {
    "fdm_test_process": ("process", {"instantiation": "false", "layer_height": "0.3", "wall_loops": "3"}),
    "0.12mm Fine @Test": (
        "process",
        {
            "inherits": "fdm_test_process",
            "layer_height": "0.12",
            "sparse_infill_density": "20%",
            "compatible_printers": ["Test Printer 0.4 nozzle"],
        },
    ),
    "0.20mm Other @Test": ("process", {"inherits": "fdm_test_process", "compatible_printers": ["Other Printer"]}),
    "Test PETG HF": (
        "filament",
        {
            "filament_type": ["PETG"],
            "nozzle_temperature": ["250"],
            "filament_id": "GFT01",
            "compatible_printers": ["Test Printer 0.4 nozzle"],
        },
    ),
    "Test Printer 0.4 nozzle": (
        "machine",
        {"printer_model": "Bambu Lab P1S", "nozzle_diameter": ["0.4"], "machine_start_gcode": "G28 ; system"},
    ),
    "Test Printer 0.6 nozzle": ("machine", {"printer_model": "Bambu Lab P1S", "nozzle_diameter": ["0.6"]}),
}


def _switch_target(name: str, section: str, index: int = 0, inherits: str = "", values: dict | None = None):
    cache = {n: (k, json.loads(json.dumps(d))) for n, (k, d) in CACHED.items()}
    data = values if values is not None else cache[name][1]
    return presets._target(
        section, name, inherits, {**data, "inherits": inherits or data.get("inherits")}, cache, index
    )


class TestSwitch:
    def test_a_process_switch_takes_the_preset_and_its_identity(self):
        cfg, applied = ws.switch_preset(project_settings(), _switch_target("0.12mm Fine @Test", "process"))
        assert (cfg["layer_height"], cfg["wall_loops"], cfg["sparse_infill_density"]) == ("0.12", "3", "20%")
        assert cfg["print_settings_id"] == "0.12mm Fine @Test"
        # a system preset is its own parent: nothing inherited, nothing differs
        assert cfg["inherits_group"] == ["", "", "", ""]
        assert cfg["different_settings_to_system"] == ["", "", "nozzle_temperature", "retraction_length"]
        # the filaments and the printer, and what no preset holds, stay as they are
        assert cfg["filament_type"] == ["PLA", "PETG"] and cfg["vendor_private_key"] == "kept as it is"
        assert {a["key"] for a in applied} == {
            "layer_height",
            "sparse_infill_density",
            "print_settings_id",
            "inherits_group",
            "different_settings_to_system",
        }

    def test_a_filament_switch_changes_its_slot_only(self):
        user = {"nozzle_temperature": ["245"], "filament_type": ["PETG"]}  # a user preset over the system one
        target = _switch_target("My PETG", "filament", index=1, inherits="Test PETG HF", values=user)
        cfg, _ = ws.switch_preset(project_settings(filament_ids=["GFA00", "GFA01"]), target)
        assert cfg["nozzle_temperature"] == ["220", "245"] and cfg["filament_type"] == ["PLA", "PETG"]
        assert cfg["filament_settings_id"] == ["Test PLA", "My PETG"]
        assert cfg["inherits_group"] == ["0.20mm Standard @Test", "", "Test PETG HF", ""]
        # the keys this user preset changes from its system parent, its section's only
        assert cfg["different_settings_to_system"] == ["wall_loops", "", "nozzle_temperature", "retraction_length"]
        assert cfg["filament_ids"] == ["GFA00", "GFT01"]

    def test_a_printer_switch_only_within_the_model_and_nozzle(self):
        cfg, _ = ws.switch_preset(project_settings(), _switch_target("Test Printer 0.4 nozzle", "printer"))
        assert cfg["machine_start_gcode"] == "G28 ; system"
        assert cfg["printer_settings_id"] == "Test Printer 0.4 nozzle"
        with pytest.raises(ws.InvalidChange, match="other_printer"):
            ws.switch_preset(project_settings(), _switch_target("Test Printer 0.6 nozzle", "printer"))

    def test_refused_whole_rather_than_half_applied(self):
        with pytest.raises(ws.InvalidChange, match="incompatible"):
            ws.switch_preset(project_settings(), _switch_target("0.20mm Other @Test", "process"))
        layout = _switch_target("Test PETG HF", "filament", index=1)
        layout["values"]["filament_extruder_variant"] = ["Direct Drive Standard", "Direct Drive High Flow"]
        layout["values"]["filament_diameter"] = ["1.75"]
        cfg = project_settings()
        with pytest.raises(ws.InvalidChange, match="multi-variant"):
            ws.switch_preset(cfg, layout)
        assert cfg == project_settings()

    def test_an_edit_after_the_switch_is_recorded_against_it(self):
        cfg, _ = ws.switch_preset(project_settings(), _switch_target("0.12mm Fine @Test", "process"))
        cfg, _ = ws.apply_changes(cfg, {"wall_loops": "4"})
        assert cfg["different_settings_to_system"][0] == "wall_loops"


@pytest.fixture
async def cached(db_session):
    db_session.add_all(OrcaBaseProfile(name=n, profile_type=k, setting=json.dumps(d)) for n, (k, d) in CACHED.items())
    orphan = LocalPreset(name="Orphan", preset_type="process", setting="{}", inherits="Not cached")
    db_session.add(orphan)
    await db_session.commit()
    return orphan


@pytest.mark.asyncio
async def test_offers_only_what_resolves_and_fits_the_printer(async_client, cached):
    identity = {"printer": "Test Printer 0.4 nozzle", "model": "Bambu Lab P1S", "nozzle": "0.4"}
    resp = await async_client.get(f"{API}/offered", params={"section": "process", **identity})
    assert resp.status_code == 200, resp.text
    # a base profile (instantiation false) is never offered
    assert [(p["name"], p["reason"]) for p in resp.json()["presets"]] == [
        ("Orphan", "unresolved"),
        ("0.12mm Fine @Test", None),
        ("0.20mm Other @Test", "incompatible"),
    ]
    printers = (await async_client.get(f"{API}/offered", params={"section": "printer", **identity})).json()
    assert [(p["name"], p["reason"]) for p in printers["presets"]] == [
        ("Test Printer 0.4 nozzle", None),
        ("Test Printer 0.6 nozzle", "other_printer"),
    ]


@pytest.mark.asyncio
async def test_previews_and_saves_a_switch_on_the_copy_only(async_client, db_session, cached):
    fine = {"section": "process", "source": "system", "name": "0.12mm Fine @Test"}
    preview = await async_client.post(f"{API}/switch", json={"settings": project_settings(), "presets": [fine]})
    assert preview.status_code == 200, preview.text
    assert preview.json()["values"]["layer_height"] == "0.12"
    orphan = {"section": "process", "source": "user", "id": cached.id}
    refused = await async_client.post(f"{API}/switch", json={"settings": project_settings(), "presets": [orphan]})
    assert refused.status_code == 422  # its parent isn't cached: not applied in part

    original = make_project()
    f = await add_file(db_session, original)
    doc = await open_ws(async_client, f.id)
    petg = {"section": "filament", "index": 1, "source": "system", "name": "Test PETG HF"}
    resp = await save(async_client, doc, {"wall_loops": "4"}, presets=[fine, petg])
    assert resp.status_code == 200, resp.text
    row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
    cfg = json.loads(entries(library.to_absolute_path(row.file_path).read_bytes())[project.PROJECT_SETTINGS_ENTRY])
    assert (cfg["print_settings_id"], cfg["layer_height"], cfg["wall_loops"]) == ("0.12mm Fine @Test", "0.12", "4")
    assert cfg["filament_settings_id"] == ["Test PLA", "Test PETG HF"] and cfg["nozzle_temperature"] == ["220", "250"]
    assert cfg["different_settings_to_system"] == ["wall_loops", "", "", "retraction_length"]
    assert library.to_absolute_path(f.file_path).read_bytes() == original  # the original is never touched
    rejected = await save(async_client, doc, {}, presets=[orphan])
    assert rejected.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["../../../../../../../attacker/repo/main/evil", "a/b", "a\\b", "..", "x\x00y", "line\nbreak"],
)
async def test_a_path_or_control_character_is_never_a_preset_name(async_client, db_session, bases, name):
    before = await cache_rows(db_session)
    resp = await async_client.get(f"{API}/values", params={"section": "process", "source": "system", "name": name})
    assert resp.status_code == 422, resp.text
    switch = {"settings": project_settings(), "presets": [{"section": "process", "source": "system", "name": name}]}
    assert (await async_client.post(f"{API}/switch", json=switch)).status_code == 422
    assert await cache_rows(db_session) == before  # nothing fetched, nothing cached


@pytest.mark.asyncio
async def test_reading_presets_writes_nothing(async_client, db_session, bases):
    before = await cache_rows(db_session)
    for params in (
        {"section": "process", "source": "system", "name": "Test Process 0.20 @Base"},
        {"section": "process", "source": "system", "name": "Not cached"},
    ):
        await async_client.get(f"{API}/values", params=params)
    assert await cache_rows(db_session) == before


@pytest.mark.asyncio
async def test_the_base_profile_fetch_refuses_a_path(db_session):
    for name in ("../../x", "a/b", "a\\b", "x\x00", None):
        assert await orca_profiles.fetch_and_cache_base_profile(name, "process", db_session) is None


class TestSwitchedValuesAreChecked:
    @pytest.mark.parametrize(
        ("key", "value", "error"),
        [
            ("layer_height", "abc", "not a number"),
            ("outer_wall_speed", "-5", "below the minimum"),
            ("sparse_infill_pattern", "grid\x01", "not one of the options"),
        ],
    )
    def test_a_value_that_is_not_one_refuses_the_whole_switch(self, key, value, error):
        target = _switch_target("0.12mm Fine @Test", "process")
        target["values"][key] = value
        cfg = project_settings()
        with pytest.raises(ws.InvalidChange, match=error) as exc:
            ws.switch_preset(cfg, target)
        assert key in exc.value.errors and cfg == project_settings()

    def test_old_values_read_as_orca_reads_them(self):
        target = _switch_target("0.12mm Fine @Test", "process")
        target["values"].update({"enable_arc_fitting": "1", "tree_support_wall_count": "-1"})
        cfg, _ = ws.switch_preset(project_settings(enable_arc_fitting="disabled", tree_support_wall_count="2"), target)
        assert (cfg["enable_arc_fitting"], cfg["tree_support_wall_count"]) == ("enabled", "0")

    def test_a_printer_presets_thumbnail_size_list_is_the_projects_thumbnails_string(self):
        target = _switch_target("Test Printer 0.4 nozzle", "printer")
        target["values"]["thumbnail_size"] = ["50x50"]  # as the BBL machine profiles hold it
        cfg, _ = ws.switch_preset(project_settings(thumbnails="96x96/PNG"), target)
        assert cfg["thumbnails"] == "50x50"

    def test_inherited_nil_stays_for_a_filament(self):
        target = _switch_target("Test PETG HF", "filament", index=1)
        target["values"]["filament_retraction_length"] = ["nil"]
        cfg, _ = ws.switch_preset(project_settings(filament_retraction_length=["0.5", "0.6"]), target)
        assert cfg["filament_retraction_length"] == ["0.5", "nil"]


class TestSaveUserPreset:
    URL = f"{API}/user"
    GOOD = {
        "inherits": "0.12mm Fine @Test",
        "name": "Fine 0.10",
        "from": "User",
        "type": "process",
        "layer_height": "0.1",
    }

    async def test_saves_the_parent_and_the_changes_as_given(self, async_client, db_session, cached):
        resp = await async_client.post(self.URL, json={"section": "process", "name": "Fine 0.10", "setting": self.GOOD})
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LocalPreset, resp.json()["id"])
        assert row.inherits == "0.12mm Fine @Test" and json.loads(row.setting) == self.GOOD  # not resolved
        again = {"section": "process", "name": "Fine 0.10", "setting": self.GOOD}
        assert (await async_client.post(self.URL, json=again)).status_code == 409  # exists: overwrite by id only
        again["id"] = row.id
        assert (await async_client.post(self.URL, json=again)).status_code == 200

    @pytest.mark.parametrize(
        ("name", "setting"),
        [
            ("Fine 0.10", {"layer_height": "abc"}),  # not a number
            ("Fine 0.10", {"outer_wall_speed": "-5"}),  # below the minimum
            ("Fine 0.10", {"nozzle_temperature": ["220"]}),  # a filament key in a process preset
            ("Fine 0.10", {"print_settings_id": "x"}),  # identity
            ("Fine 0.10", {"inherits": "../../evil"}),  # a path as the parent
            ("0.12mm Fine @Test", {"layer_height": "0.1"}),  # a system preset's name
            ("bad:name", {"layer_height": "0.1"}),  # check_preset_name
            (" lead", {"layer_height": "0.1"}),
        ],
    )
    async def test_refuses_what_the_slicer_would_not_take(self, async_client, db_session, cached, name, setting):
        resp = await async_client.post(self.URL, json={"section": "process", "name": name, "setting": setting})
        assert resp.status_code == 422, resp.text
