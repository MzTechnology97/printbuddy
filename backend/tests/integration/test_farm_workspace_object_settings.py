"""Printfarm (fork): an object's and a part's own settings in a workspace copy (OrcaSlicer's object and part tabs).

Only the edited ``<metadata>`` lines of ``model_settings.config`` change: set in key order, changed in place or
dropped, every other byte as it was; values are checked as the project's are.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import object_settings, project
from backend.app.farm.workspace.object_settings import InvalidObjectSetting, ObjectSetting
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import (
    add_file,
    entries,
    make_project,
    open_ws,
    project_settings,
    save,
)

# As OrcaSlicer writes it (bbs_3mf.cpp): the object's keys after its name, a part's after its matrix, in key order.
SETTINGS = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2" auto_drop="1">
    <metadata key="name" value="Cube A"/>
    <metadata key="extruder" value="1"/>
    <metadata key="layer_height" value="0.12"/>
    <metadata key="wall_loops" value="4"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="Cube A"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
      <metadata key="sparse_infill_density" value="40%"/>
      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>
    </part>
    <part id="3" subtype="modifier_part">
      <metadata key="name" value="Box &amp; co"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
      <mesh_stat edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>
    </part>
    <part id="5" subtype="negative_part">
      <metadata key="name" value="Hole"/>
    </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
      <metadata key="instance_id" value="0"/>
      <metadata key="identify_id" value="77"/>
    </model_instance>
  </plate>
  <assemble>
   <assemble_item object_id="2" instance_id="0" transform="1 0 0 0 1 0 0 0 1 9 9 9" offset="0 0 0" />
  </assemble>
</config>
"""
CFG = project_settings()


def edit(*items: dict) -> tuple[bytes, list[dict]]:
    return object_settings.apply(SETTINGS, CFG, [ObjectSetting(**i) for i in items], "orcaslicer")


class TestApply:
    def test_a_changed_value_is_changed_in_place(self):
        out, applied = edit({"object_id": "2", "key": "wall_loops", "value": 6})
        assert out == SETTINGS.replace(b'"wall_loops" value="4"', b'"wall_loops" value="6"')
        assert applied == [
            {
                "key": "object_setting",
                "object_id": "2",
                "part_id": None,
                "setting": "wall_loops",
                "before": "4",
                "after": "6",
            }
        ]

    def test_a_new_key_goes_in_key_order_and_a_dropped_one_leaves_no_line(self):
        out, applied = edit(
            {"object_id": "2", "key": "sparse_infill_density", "value": "20"},  # a percentage stays one
            {"object_id": "2", "key": "layer_height", "value": None},
            {"object_id": "2", "key": "enable_support", "value": True},  # as the project writes it: 1 / 0
        )
        # extruder is one of the object's config keys too: Orca writes them all in key order
        assert out == (
            SETTINGS.replace(b'    <metadata key="layer_height" value="0.12"/>\n', b"")
            .replace(
                b'    <metadata key="extruder" value="1"/>\n',
                b'    <metadata key="enable_support" value="1"/>\n    <metadata key="extruder" value="1"/>\n',
            )
            .replace(
                b'    <metadata key="wall_loops" value="4"/>\n',
                b'    <metadata key="sparse_infill_density" value="20%"/>\n    <metadata key="wall_loops" value="4"/>\n',
            )
        )
        assert [(a["setting"], a["before"], a["after"]) for a in applied] == [
            ("sparse_infill_density", None, "20%"),
            ("layer_height", "0.12", None),
            ("enable_support", None, "1"),
        ]

    def test_a_parts_keys_go_after_its_matrix_and_a_modifier_has_them_too(self):
        out, _ = edit(
            {"object_id": "2", "part_id": "1", "key": "top_shell_layers", "value": "5"},
            {"object_id": "2", "part_id": "3", "key": "sparse_infill_density", "value": "80%"},
        )
        assert out == SETTINGS.replace(
            b'      <metadata key="sparse_infill_density" value="40%"/>\n',
            b'      <metadata key="sparse_infill_density" value="40%"/>\n      <metadata key="top_shell_layers" value="5"/>\n',
        ).replace(
            b'value="Box &amp; co"/>\n      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n',
            b'value="Box &amp; co"/>\n      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
            b'      <metadata key="sparse_infill_density" value="80%"/>\n',
        )

    def test_the_same_value_or_dropping_what_is_not_there_changes_nothing(self):
        assert edit({"object_id": "2", "key": "wall_loops", "value": "4"}) == (SETTINGS, [])
        assert edit({"object_id": "2", "part_id": "3", "key": "wall_loops", "value": None}) == (SETTINGS, [])

    def test_windows_line_breaks_stay(self):
        crlf = SETTINGS.replace(b"\n", b"\r\n")
        out, _ = object_settings.apply(
            crlf, CFG, [ObjectSetting(object_id="2", key="top_shell_layers", value="4")], "orcaslicer"
        )
        assert out == crlf.replace(  # in key order: before wall_loops
            b'    <metadata key="wall_loops" value="4"/>\r\n',
            b'    <metadata key="top_shell_layers" value="4"/>\r\n    <metadata key="wall_loops" value="4"/>\r\n',
        )

    @pytest.mark.parametrize(
        ("item", "error"),
        [
            ({"object_id": "2", "part_id": "1", "key": "layer_height", "value": "0.1"}, "a part can have"),
            ({"object_id": "2", "key": "nozzle_temperature", "value": "200"}, "an object can have"),  # a filament key
            ({"object_id": "2", "key": "no_such_key", "value": "1"}, "an object can have"),
            ({"object_id": "2", "part_id": "5", "key": "wall_loops", "value": "2"}, "has no settings of its own"),
            ({"object_id": "2", "key": "wall_loops", "value": "-1"}, "below the minimum"),
            ({"object_id": "2", "key": "wall_loops", "value": "two"}, "not a whole number"),
            ({"object_id": "2", "key": "sparse_infill_pattern", "value": "spiral"}, "not one of the options"),
            ({"object_id": "2", "key": "layer_height", "value": "nil"}, "inherited value"),
            ({"object_id": "9", "key": "wall_loops", "value": "2"}, "not one element"),
            ({"object_id": "2", "part_id": "7", "key": "wall_loops", "value": "2"}, "not one element"),
        ],
    )
    def test_refuses_what_the_slicer_would_not_take(self, item, error):
        with pytest.raises(InvalidObjectSetting, match=error):
            edit(item)

    def test_a_part_with_embossed_text_is_written_as_the_others(self):
        # OrcaSlicer writes the text's settings as <slic3rpe:text>, the prefix never declared: kept byte for byte
        density = b'      <metadata key="sparse_infill_density" value="40%"/>\n'
        text = SETTINGS.replace(density, density + b'      <slic3rpe:text text="HELLO" font_descriptor="Arial"/>\n')
        out, _ = object_settings.apply(
            text, CFG, [ObjectSetting(object_id="2", part_id="1", key="wall_loops", value="5")], "orcaslicer"
        )
        assert out == text.replace(density, density + b'      <metadata key="wall_loops" value="5"/>\n')

    def test_the_filament_of_an_object_a_part_and_a_modifier(self):
        # the project has 2 filaments; a part or a modifier may hold 0 ("default"), an object may not
        out, applied = edit(
            {"object_id": "2", "key": "extruder", "value": 2},
            {"object_id": "2", "part_id": "1", "key": "extruder", "value": "0"},
            {"object_id": "2", "part_id": "3", "key": "extruder", "value": "2"},
        )
        assert out == SETTINGS.replace(b'"extruder" value="1"', b'"extruder" value="2"').replace(
            b'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n      <metadata key="sparse',
            b'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
            b'      <metadata key="extruder" value="0"/>\n      <metadata key="sparse',
        ).replace(
            b'value="Box &amp; co"/>\n      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n',
            b'value="Box &amp; co"/>\n      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
            b'      <metadata key="extruder" value="2"/>\n',
        )
        assert [(a["part_id"], a["before"], a["after"]) for a in applied] == [
            (None, "1", "2"),
            ("1", None, "0"),
            ("3", None, "2"),
        ]
        for item, error in (
            ({"object_id": "2", "key": "extruder", "value": "0"}, "no filament 0"),
            ({"object_id": "2", "key": "extruder", "value": "3"}, "no filament 3"),
            ({"object_id": "2", "part_id": "1", "key": "extruder", "value": "two"}, "not a filament number"),
            ({"object_id": "2", "part_id": "5", "key": "extruder", "value": "1"}, "has no settings of its own"),
        ):
            with pytest.raises(InvalidObjectSetting, match=error):
                edit(item)

    def test_refuses_a_key_set_twice(self):
        with pytest.raises(InvalidObjectSetting, match="set twice"):
            edit(
                {"object_id": "2", "key": "wall_loops", "value": "2"},
                {"object_id": "2", "key": "wall_loops", "value": "3"},
            )


@pytest.mark.integration
class TestSaveCopy:
    async def test_a_copy_with_own_settings_changes_only_model_settings(self, async_client, db_session):
        original = make_project("bambu")
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        own = [
            {"object_id": "4", "key": "wall_loops", "value": "2"},  # changed
            {"object_id": "2", "key": "layer_height", "value": "0.12"},  # new, on an object without any
            {"object_id": "4", "part_id": "1", "key": "sparse_infill_density", "value": None},  # dropped
        ]
        resp = await save(async_client, doc, {}, object_settings=own)
        assert resp.status_code == 200, resp.text
        assert [(c["key"], c["setting"], c["before"], c["after"]) for c in resp.json()["changes"]] == [
            ("object_setting", "wall_loops", "5", "2"),
            ("object_setting", "layer_height", None, "0.12"),
            ("object_setting", "sparse_infill_density", "40%", None),
        ]
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        assert [n for n in before if before[n] != after[n]] == [project.MODEL_SETTINGS_ENTRY]
        copy = await open_ws(async_client, row.id)
        overrides = {o["id"]: o["overrides"] for o in copy["objects"]}
        assert overrides["2"] == [{"key": "layer_height", "value": "0.12", "part": None, "part_id": None}]
        assert overrides["4"] == [{"key": "wall_loops", "value": "2", "part": None, "part_id": None}]

    async def test_an_objects_name_is_not_a_setting_of_its_own(self, async_client, db_session):
        # the project's process preset has a "name" too: an object's and a part's are theirs, not overrides
        f = await add_file(db_session, make_project("orca", project_settings(name="0.20mm Standard @Test")))
        doc = await open_ws(async_client, f.id)
        assert {o["id"]: [x["key"] for x in o["overrides"]] for o in doc["objects"]} == {
            "2": [],
            "4": ["wall_loops", "sparse_infill_density"],
        }

    async def test_a_refused_setting_saves_nothing(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca"))
        doc = await open_ws(async_client, f.id)
        resp = await save(
            async_client, doc, {}, object_settings=[{"object_id": "2", "key": "wall_loops", "value": "-3"}]
        )
        assert resp.status_code == 422 and "below the minimum" in resp.json()["detail"]
