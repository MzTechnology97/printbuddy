"""Printfarm (fork): the plates of a workspace copy (OrcaSlicer's Add plate, Remove the current plate, Move plate to
the front, Lock).

The plates' blocks are added, removed and moved as they are, numbered again in their order, every other byte kept;
the per-plate wipe tower lists follow Orca's; a removed plate's objects stay in the model.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import plate_structure, project
from backend.app.farm.workspace.plate_structure import InvalidPlateStructure, PlateOp
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import (
    add_file,
    entries,
    make_project,
    open_ws,
    project_settings,
    save,
)

ONE = b"""  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value="Left"/>
    <metadata key="locked" value="false"/>
    <metadata key="thumbnail_file" value="Metadata/plate_1.png"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
      <metadata key="instance_id" value="0"/>
    </model_instance>
  </plate>
"""
TWO = (
    ONE.replace(b'"plater_id" value="1"', b'"plater_id" value="2"')
    .replace(b"Left", b"Right")
    .replace(b"plate_1", b"plate_2")
    .replace(b'"object_id" value="2"', b'"object_id" value="4"')
)
SETTINGS = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="2"/>\n'
    + ONE
    + TWO
    + b"  <assemble/>\n</config>\n"
)
CFG = {"wipe_tower_x": ["165", "170"], "wipe_tower_y": ["250", "240"], "layer_height": "0.2"}
NEW = b"""  <plate>
    <metadata key="plater_id" value="{n}"/>
    <metadata key="plater_name" value=""/>
    <metadata key="locked" value="false"/>
  </plate>
"""


def run(*ops: dict, settings: bytes = SETTINGS, cfg: dict = CFG):
    out, cfg, _, applied = plate_structure.apply(settings, cfg, [PlateOp(**o) for o in ops])
    return out, cfg, applied


def numbered(block: bytes, n: int) -> bytes:
    return (
        block.replace(b'"plater_id" value="1"', b'"plater_id" value="X"')
        .replace(b'"plater_id" value="2"', b'"plater_id" value="X"')
        .replace(b'"plater_id" value="X"', f'"plater_id" value="{n}"'.encode())
    )


class TestApply:
    def test_moving_a_plate_to_the_front_renumbers_them_and_leaves_the_wipe_towers(self):
        out, cfg, applied = run({"op": "front", "plate": 2})
        assert out == SETTINGS.replace(ONE + TWO, numbered(TWO, 1) + numbered(ONE, 2))
        assert cfg == CFG
        assert applied == [{"key": "plate", "op": "front", "plate": 2, "plates": 2}]

    def test_removing_a_plate_drops_its_block_and_its_wipe_tower_not_its_objects(self):
        out, cfg, _ = run({"op": "remove", "plate": 1})
        assert out == SETTINGS.replace(ONE + TWO, numbered(TWO, 1))
        assert b'<object id="2"/>' in out  # the object stays, on no plate
        assert cfg["wipe_tower_x"] == ["170"] and cfg["wipe_tower_y"] == ["240"] and cfg["layer_height"] == "0.2"

    def test_a_new_plate_is_empty_and_gets_the_first_wipe_tower(self):
        out, cfg, _ = run({"op": "add"}, {"op": "add"})
        assert out == SETTINGS.replace(ONE + TWO, ONE + TWO + NEW.replace(b"{n}", b"3") + NEW.replace(b"{n}", b"4"))
        assert cfg["wipe_tower_x"] == ["165", "170", "165", "165"]
        # a shorter list (a plate added when the tower was off) is filled with its first value, as Orca resizes it
        assert run({"op": "add"}, cfg={"wipe_tower_x": ["9"]})[1]["wipe_tower_x"] == ["9", "9", "9"]

    def test_ops_count_the_plates_as_they_are_after_the_ones_before(self):
        out, _, applied = run({"op": "add"}, {"op": "front", "plate": 3}, {"op": "remove", "plate": 2})
        assert [b.count(b"Left") + 2 * b.count(b"Right") for b in out.split(b"<plate>")[1:]] == [0, 2]
        assert [a["plates"] for a in applied] == [3, 3, 2]

    def test_windows_line_breaks_stay(self):
        out, _, _ = run({"op": "add"}, settings=SETTINGS.replace(b"\n", b"\r\n"))
        assert b"\n" not in out.replace(b"\r\n", b"")

    def test_the_plates_move_as_orca_lays_them_out(self):
        bed = (256.0, 256.0)  # a stride of 307.2 mm
        moves = lambda count, *ops: plate_structure.moves(count, [PlateOp(**o) for o in ops], bed)[:2]  # noqa: E731
        # plate 2 to the front: the two plates trade places
        assert moves(2, {"op": "front", "plate": 2}) == ({0: (307.2, 0.0), 1: (-307.2, 0.0)}, (0.0, 0.0))
        # plate 1 removed: its objects go where the objects on no plate are (after the one plate left), plate 2
        # takes its place, and the objects on no plate move from after two plates to after one
        delta, outside = moves(2, {"op": "remove", "plate": 1})
        assert delta == {0: (307.2, 0.0), 1: (-307.2, 0.0)} and outside == (307.2, 307.2)
        # a fifth plate: 3 columns instead of 2: plate 3 goes up to the first row, plate 4 one column left; the
        # objects on no plate stay
        delta, outside = moves(4, {"op": "add"})
        assert [tuple(round(v, 6) for v in delta[p]) for p in range(4)] == [(0, 0), (0, 0), (614.4, 307.2), (-307.2, 0)]
        assert outside == (0.0, 0.0)

    @pytest.mark.parametrize(
        ("ops", "error"),
        [
            ([{"op": "remove", "plate": 3}], "no plate 3"),
            ([{"op": "remove", "plate": 1}, {"op": "remove", "plate": 1}], "last plate can't be removed"),
            ([{"op": "front", "plate": 1}], "already at the front"),
            ([{"op": "add"}] * 35, "at most 36 plates"),
        ],
    )
    def test_refuses_what_orca_would_not_do(self, ops, error):
        with pytest.raises(InvalidPlateStructure, match=error):
            run(*ops)


@pytest.mark.integration
class TestSaveCopy:
    async def test_a_copy_with_plates_changed_changes_only_its_files(self, async_client, db_session):
        original = make_project("orca", project_settings(printable_area=["0x0", "256x0", "256x256", "0x256"]))
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        resp = await save(
            async_client,
            doc,
            {},
            plate_structure=[{"op": "add"}, {"op": "front", "plate": 2}],
            plate_settings=[
                {"plate": 3, "key": "locked", "value": True},
                {"plate": 1, "key": "plater_name", "value": "Now first"},
            ],
        )
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        # the plates, and their instances moved with them (3 plates still fit 2 columns: the two trade places)
        assert sorted(n for n in before if before[n] != after[n]) == [project.MODEL_ENTRY, project.MODEL_SETTINGS_ENTRY]
        assert b'objectid="2" transform="1 0 0 0 1 0 0 0 1 357.2 50 5"' in after[project.MODEL_ENTRY]
        copy = await open_ws(async_client, row.id)
        assert [(p["index"], p["name"], p["object_ids"], p["metadata"].get("locked")) for p in copy["plates"]] == [
            (1, "Now first", ["4"], None),
            (2, "First", ["2"], None),
            (3, None, [], "true"),
        ]

    async def test_a_refused_change_saves_nothing(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca"))
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, plate_structure=[{"op": "front", "plate": 1}])
        assert resp.status_code == 422 and "already at the front" in resp.json()["detail"]

    async def test_the_plates_instances_move_with_them_and_a_removed_plates_stay(self, async_client, db_session):
        area = ["0x0", "256x0", "256x256", "0x256"]
        original = make_project("orca", project_settings(printable_area=area))
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        # object 2 on plate 1 at x 50, object 4 on plate 2 at x 300; plate 1 goes: its object is kept, off the plate
        resp = await save(async_client, doc, {}, plate_structure=[{"op": "remove", "plate": 1}])
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        after = entries(library.to_absolute_path(row.file_path).read_bytes())
        assert (
            b'<item objectid="2" transform="1 0 0 0 1 0 0 0 1 357.2 50 5" printable="1"/>' in after["3D/3dmodel.model"]
        )
        assert (
            b'<item objectid="4" transform="1 0 0 0 1 0 0 0 1 -7.2 50 5" printable="1"/>' in after["3D/3dmodel.model"]
        )
        copy = await open_ws(async_client, row.id)
        assert [(p["index"], p["object_ids"]) for p in copy["plates"]] == [(1, ["4"])]
        assert {o["id"] for o in copy["objects"]} == {"2", "4"}  # nothing is lost

    async def test_without_a_printable_area_the_plates_cannot_move(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca"))
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, plate_structure=[{"op": "front", "plate": 2}])
        assert resp.status_code == 422 and "printable area" in resp.json()["detail"]
