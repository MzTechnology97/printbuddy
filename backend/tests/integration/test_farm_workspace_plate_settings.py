"""Printfarm (fork): a plate's own settings in a workspace copy (OrcaSlicer's Plate Settings dialog and tab).

Only the edited ``<metadata>`` lines of the plate change: set after the line Orca writes before them, changed in
place or dropped, every other byte as it was; values are checked against the slicer's choices and the project's
filaments.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import plate_settings, project
from backend.app.farm.workspace.plate_settings import InvalidPlateSetting, PlateSetting
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import (
    add_file,
    entries,
    make_project,
    open_ws,
    project_settings,
    save,
)

# As OrcaSlicer writes it (bbs_3mf.cpp): plater_id, plater_name, locked, then the plate's own settings
SETTINGS = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2" auto_drop="1">
    <metadata key="name" value="Cube A"/>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
    <metadata key="print_sequence" value="by object"/>
    <metadata key="filament_map_mode" value="Auto For Flush"/>
    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
      <metadata key="instance_id" value="0"/>
      <metadata key="identify_id" value="77"/>
    </model_instance>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/>
    <metadata key="plater_name" value="Back"/>
    <metadata key="locked" value="false"/>
  </plate>
</config>
"""
CFG = project_settings()  # two filaments


def edit(*items: dict) -> tuple[bytes, list[dict]]:
    return plate_settings.apply(SETTINGS, CFG, [PlateSetting(**i) for i in items], "orcaslicer")


def plate(out: bytes, n: int) -> list[str]:
    block = out.split(b"<plate>")[n].split(b"<model_instance>")[0].split(b"</plate>")[0]
    return [line.strip().decode() for line in block.splitlines() if b"<metadata" in line]


class TestApply:
    def test_each_key_goes_after_the_line_orca_writes_before_it(self):
        out, applied = edit(
            {"plate": 1, "key": "spiral_mode", "value": True},
            {"plate": 1, "key": "bed_type", "value": "Textured PEI Plate"},
            {"plate": 1, "key": "first_layer_print_sequence", "value": "2 1"},
            {"plate": 1, "key": "plater_name", "value": "Front & centre"},
        )
        assert plate(out, 1) == [
            '<metadata key="plater_id" value="1"/>',
            '<metadata key="plater_name" value="Front &amp; centre"/>',
            '<metadata key="locked" value="false"/>',
            '<metadata key="bed_type" value="Textured PEI Plate"/>',
            '<metadata key="print_sequence" value="by object"/>',
            '<metadata key="first_layer_print_sequence" value="2 1"/>',
            '<metadata key="spiral_mode" value="true"/>',
            '<metadata key="filament_map_mode" value="Auto For Flush"/>',
            '<metadata key="thumbnail_file" value="Metadata/plate_1.png"/>',
        ]
        assert [(a["setting"], a["before"], a["after"]) for a in applied] == [
            ("spiral_mode", None, "true"),
            ("bed_type", None, "Textured PEI Plate"),
            ("first_layer_print_sequence", None, "2 1"),
            ("plater_name", "", "Front & centre"),
        ]
        assert out.split(b"<plate>")[2] == SETTINGS.split(b"<plate>")[2]  # the other plate as it was

    def test_the_other_layers_ranges_write_their_count_and_go_together(self):
        out, _ = edit({"plate": 2, "key": "other_layers_print_sequence", "value": "2 5 2 1 6 2147483646 1 2"})
        assert plate(out, 2)[3:] == [
            '<metadata key="other_layers_print_sequence" value="2 5 2 1 6 2147483646 1 2"/>',
            '<metadata key="other_layers_print_sequence_nums" value="2"/>',
        ]
        back, _ = plate_settings.apply(
            out, CFG, [PlateSetting(plate=2, key="other_layers_print_sequence")], "orcaslicer"
        )
        assert back == SETTINGS

    def test_same_as_global_drops_the_line_and_a_value_is_changed_in_place(self):
        out, applied = edit({"plate": 1, "key": "print_sequence", "value": None})
        assert out == SETTINGS.replace(b'    <metadata key="print_sequence" value="by object"/>\n', b"")
        assert applied[0]["before"] == "by object" and applied[0]["after"] is None
        out, _ = edit({"plate": 1, "key": "print_sequence", "value": "by layer"})
        assert out == SETTINGS.replace(b'value="by object"', b'value="by layer"')

    def test_windows_line_breaks_stay(self):
        crlf = SETTINGS.replace(b"\n", b"\r\n")
        out, _ = plate_settings.apply(crlf, CFG, [PlateSetting(plate=2, key="spiral_mode", value=False)], "orcaslicer")
        locked = b'value="Back"/>\r\n    <metadata key="locked" value="false"/>\r\n'
        assert out == crlf.replace(locked, locked + b'    <metadata key="spiral_mode" value="false"/>\r\n')

    @pytest.mark.parametrize(
        ("item", "error"),
        [
            ({"plate": 3, "key": "spiral_mode", "value": True}, "no plate 3"),
            ({"plate": 1, "key": "bed_type", "value": "Glass Plate"}, "is not one of"),
            ({"plate": 1, "key": "print_sequence", "value": "by default"}, "is not one of"),
            ({"plate": 1, "key": "first_layer_print_sequence", "value": "1 1"}, "each of the 2 filaments once"),
            ({"plate": 1, "key": "first_layer_print_sequence", "value": "1 2 3"}, "each of the 2 filaments once"),
            ({"plate": 1, "key": "other_layers_print_sequence", "value": "1 5 1 2"}, "from layer 2 up"),
            ({"plate": 1, "key": "other_layers_print_sequence", "value": "6 5 1 2"}, "ends after it begins"),
            ({"plate": 1, "key": "other_layers_print_sequence", "value": "2 5 1"}, "ranges of 4 numbers"),
            ({"plate": 1, "key": "spiral_mode", "value": "1"}, "not true or false"),
            ({"plate": 1, "key": "plater_name", "value": "a\nb"}, "no control characters"),
            ({"plate": 1, "key": "plater_name", "value": None}, "can't be removed"),
            ({"plate": 1, "key": "locked", "value": None}, "can't be removed"),
            ({"plate": 1, "key": "locked", "value": "yes"}, "not true or false"),
            ({"plate": 1, "key": "bed_type", "value": True}, "not a value it takes"),
        ],
    )
    def test_refuses_what_the_slicer_would_not_take(self, item, error):
        with pytest.raises(InvalidPlateSetting, match=error):
            edit(item)

    def test_refuses_a_key_set_twice(self):
        with pytest.raises(InvalidPlateSetting, match="set twice"):
            edit({"plate": 1, "key": "spiral_mode", "value": True}, {"plate": 1, "key": "spiral_mode", "value": False})


@pytest.mark.integration
class TestSaveCopy:
    async def test_a_copy_with_plate_settings_changes_only_model_settings(self, async_client, db_session):
        original = make_project("orca")
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        edits = [
            {"plate": 1, "key": "plater_name", "value": "Left"},
            {"plate": 2, "key": "print_sequence", "value": None},
            {"plate": 2, "key": "bed_type", "value": "Engineering Plate"},
        ]
        resp = await save(async_client, doc, {}, plate_settings=edits)
        assert resp.status_code == 200, resp.text
        assert [(c["key"], c["plate"], c["setting"], c["before"], c["after"]) for c in resp.json()["changes"]] == [
            ("plate_setting", 1, "plater_name", "First", "Left"),
            ("plate_setting", 2, "print_sequence", "by object", None),
            ("plate_setting", 2, "bed_type", None, "Engineering Plate"),
        ]
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        assert [n for n in before if before[n] != after[n]] == [project.MODEL_SETTINGS_ENTRY]
        copy = await open_ws(async_client, row.id)
        meta = {p["index"]: (p["name"], p["metadata"]) for p in copy["plates"]}
        assert meta[1][0] == "Left"
        assert "print_sequence" not in meta[2][1] and meta[2][1]["bed_type"] == "Engineering Plate"

    async def test_a_refused_plate_setting_saves_nothing(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca"))
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, plate_settings=[{"plate": 1, "key": "bed_type", "value": "Glass"}])
        assert resp.status_code == 422 and "A plate's settings were refused" in resp.json()["detail"]
