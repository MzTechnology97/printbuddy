"""Printfarm (fork): objects' height ranges in a workspace copy (OrcaSlicer's Height Range Modifier).

Orca keeps them in ``Metadata/layer_config_ranges.xml`` by the objects' order in the build; a copy rewrites the edited
objects' blocks only, adds the file when the project has none, and a duplicated plate's copies take their objects'.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import layer_ranges, project
from backend.app.farm.workspace.layer_ranges import InvalidLayerRanges, LayerRange, RangeEdit
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import (
    add_file,
    entries,
    make_project,
    open_ws,
    project_settings,
    save,
)
from backend.tests.integration.test_farm_workspace_plate_objects import CFG, MODEL

# objects 2 and 4 in the build: Orca's ids 1 and 2
RANGES = b"""<?xml version="1.0" encoding="utf-8"?>
<objects>
 <object id="2">
  <range min_z="0" max_z="2">
   <option opt_key="extruder">0</option>
   <option opt_key="layer_height">0.1</option>
  </range>
 </object>
</objects>
"""
SETTINGS = {"layer_height": "0.2", "sparse_infill_density": "15%", "wall_loops": "3"}


def edit(object_id: str, *ranges: tuple[float, float, dict]) -> RangeEdit:
    return RangeEdit(object_id=object_id, ranges=[LayerRange(min_z=a, max_z=b, options=o) for a, b, o in ranges])


def run(xml: bytes | None, *edits: RangeEdit):
    return layer_ranges.apply(xml, MODEL, list(edits), SETTINGS, "orcaslicer", 2)


class TestApply:
    def test_parse_names_objects_by_their_place_in_the_build(self):
        assert layer_ranges.object_order(MODEL) == ["2", "4"]
        assert layer_ranges.parse(RANGES, ["2", "4"]) == {
            "4": [{"min_z": 0.0, "max_z": 2.0, "options": {"extruder": "0", "layer_height": "0.1"}}]
        }

    def test_a_new_file_as_orca_writes_it(self):
        out, applied = run(
            None, edit("2", (5, 15, {"layer_height": "0.1", "sparse_infill_density": "80%", "extruder": 0}))
        )
        assert out == (
            b'<?xml version="1.0" encoding="utf-8"?>\n<objects>\n <object id="1">\n'
            b'  <range min_z="5" max_z="15">\n'
            b'   <option opt_key="extruder">0</option>\n'
            b'   <option opt_key="layer_height">0.1</option>\n'
            b'   <option opt_key="sparse_infill_density">80%</option>\n'
            b"  </range>\n </object>\n</objects>\n"
        )
        assert applied == [{"key": "layer_ranges", "object_id": "2", "ranges": 1}]

    def test_a_new_block_goes_before_the_next_object_and_the_other_stays(self):
        out, _ = run(RANGES, edit("2", (0, 1.5, {"layer_height": "0.12"})))
        assert out.index(b'<object id="1">') < out.index(b'<object id="2">')
        assert RANGES[RANGES.index(b' <object id="2">') :] in out  # byte for byte

    def test_an_edited_block_is_replaced_and_an_emptied_one_dropped(self):
        out, _ = run(RANGES, edit("4", (0, 2, {"layer_height": "0.1"}), (2, 4, {"layer_height": "0.28"})))
        assert layer_ranges.parse(out, ["2", "4"])["4"][1] == {
            "min_z": 2.0,
            "max_z": 4.0,
            "options": {"layer_height": "0.28"},
        }
        dropped, applied = run(RANGES, edit("4"))
        assert dropped == b'<?xml version="1.0" encoding="utf-8"?>\n<objects>\n</objects>\n'
        assert applied == [{"key": "layer_ranges", "object_id": "4", "ranges": 0}]

    @pytest.mark.parametrize(
        ("ranges", "error"),
        [
            ([(3, 2, {"layer_height": "0.1"})], "ends below where it starts"),
            ([(2, 4, {"layer_height": "0.1"}), (0, 3, {"layer_height": "0.1"})], "the same or out of order"),
            ([(0, 2, {"layer_height": "0.1"}), (0, 2, {"layer_height": "0.2"})], "the same or out of order"),
            ([(0, 2, {"sparse_infill_density": "80%"})], "has no layer height"),
            ([(0, 2, {"layer_height": "0.1", "printer_model": "X"})], "not a setting a height range can have"),
            ([(0, 2, {"layer_height": "0.1", "extruder": 3})], "range 1"),
        ],
    )
    def test_refuses(self, ranges, error):
        with pytest.raises(InvalidLayerRanges, match=error):
            run(None, edit("2", *ranges))

    def test_overlapping_ranges_are_kept_as_orca_keeps_them(self):
        out, _ = run(None, edit("2", (0, 3, {"layer_height": "0.1"}), (2, 4, {"layer_height": "0.3"})))
        assert [(r["min_z"], r["max_z"]) for r in layer_ranges.parse(out, ["2", "4"])["2"]] == [(0, 3), (2, 4)]

    def test_refuses_an_object_not_in_the_model_or_given_twice(self):
        with pytest.raises(InvalidLayerRanges, match="no object 9"):
            run(None, edit("9"))
        with pytest.raises(InvalidLayerRanges, match="given twice"):
            run(None, edit("2"), edit("2"))


@pytest.mark.integration
class TestSaveCopy:
    async def test_the_copy_adds_the_file_and_reopens_with_the_ranges(self, async_client, db_session):
        original = make_project("orca", project_settings())
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        assert all(o["ranges"] == [] for o in doc["objects"])
        ranges = [{"min_z": 5, "max_z": 15, "options": {"layer_height": "0.1", "extruder": 0}}]
        resp = await save(async_client, doc, {}, layer_ranges=[{"object_id": "4", "ranges": ranges}])
        assert resp.status_code == 200, resp.text
        assert {"key": "layer_ranges", "object_id": "4", "ranges": 1} in resp.json()["changes"]
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        assert list(after) == [*before, project.LAYER_RANGES_ENTRY]  # added last, every other entry as it was
        assert all(after[n] == before[n] for n in before)
        assert b'<object id="2">' in after[project.LAYER_RANGES_ENTRY]
        copy = await open_ws(async_client, row.id)
        by_id = {o["id"]: o["ranges"] for o in copy["objects"]}
        assert by_id["2"] == []
        assert by_id["4"] == [{"min_z": 5.0, "max_z": 15.0, "options": {"extruder": "0", "layer_height": "0.1"}}]

    async def test_a_duplicated_plate_s_copies_take_their_objects_ranges(self, async_client, db_session):
        original = make_project(
            "orca",
            project_settings(printable_area=CFG["printable_area"]),
            extra={project.LAYER_RANGES_ENTRY: RANGES.decode()},
        )
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, plate_structure=[{"op": "duplicate", "plate": 2, "objects": ["4"]}])
        assert resp.status_code == 200, resp.text
        assert not [c for c in resp.json()["changes"] if c.get("key") == "layer_ranges"]
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        copy = await open_ws(async_client, row.id)
        new = next(o for o in copy["objects"] if o["id"] not in ("2", "4"))
        assert new["ranges"] == next(o["ranges"] for o in copy["objects"] if o["id"] == "4")
        assert new["ranges"][0]["options"]["layer_height"] == "0.1"

    async def test_bad_ranges_are_refused_with_the_reason(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca", project_settings()))
        doc = await open_ws(async_client, f.id)
        ranges = [{"min_z": 0, "max_z": 2, "options": {"sparse_infill_density": "80%"}}]
        resp = await save(async_client, doc, {}, layer_ranges=[{"object_id": "2", "ranges": ranges}])
        assert resp.status_code == 422
        assert "has no layer height" in resp.json()["detail"]
