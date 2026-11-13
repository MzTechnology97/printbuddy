"""Printfarm (fork): objects between plates in a workspace copy (OrcaSlicer's Duplicate Current Plate, and an instance
moved onto another plate).

A duplicate copies its plate's objects as Orca's share-mesh save writes them (a new object pointing at the same
meshes, its settings block, a build item per instance, moved by the plates' distance) and lists them on a new plate;
an instance moved onto another plate is listed there with its own bytes. Every other byte stays.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import plate_objects, plate_structure, project
from backend.app.farm.workspace.plate_objects import InvalidCopy
from backend.app.farm.workspace.plate_structure import InstancePlate, InvalidPlateStructure, PlateOp
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import (
    add_file,
    entries,
    make_project,
    open_ws,
    project_settings,
    save,
)

# Bambu Studio's layout: objects with components in their own files, UUIDs, one element a line, identify_id
MODEL = b"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">
 <resources>
  <object id="2" p:UUID="00000001-61cb-4c03-9d28-80fed5dfa1dc" type="model">
   <components>
    <component p:path="/3D/Objects/object_1.model" objectid="1" p:UUID="00010000-b206-40ff-9872-83e8017abed1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>
   </components>
  </object>
  <object id="4" p:UUID="00000002-61cb-4c03-9d28-80fed5dfa1dc" type="model">
   <components>
    <component p:path="/3D/Objects/object_2.model" objectid="3" p:UUID="00020000-b206-40ff-9872-83e8017abed1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>
   </components>
  </object>
 </resources>
 <build p:UUID="2c7c17d8-22b5-4d84-8835-1976022ea369">
  <item objectid="2" p:UUID="00000002-b1ec-4553-aec9-835e5b724bb4" transform="1 0 0 0 1 0 0 0 1 50 60 5" printable="1"/>
  <item objectid="4" p:UUID="00000004-b1ec-4553-aec9-835e5b724bb4" transform="1 0 0 0 1 0 0 0 1 400 60 5" printable="1"/>
  <item objectid="4" p:UUID="00000005-b1ec-4553-aec9-835e5b724bb4" transform="1 0 0 0 1 0 0 0 1 450 60 5" printable="1"/>
 </build>
</model>
"""
SETTINGS = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2">
    <metadata key="name" value="Cube A"/>
    <metadata key="wall_loops" value="4"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="Cube A"/>
    </part>
  </object>
  <object id="4">
    <metadata key="name" value="Cube B"/>
    <part id="3" subtype="normal_part">
      <metadata key="name" value="Cube B"/>
    </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="plater_name" value="Left"/>
    <metadata key="bed_type" value="Cool Plate"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
      <metadata key="instance_id" value="0"/>
      <metadata key="identify_id" value="287"/>
    </model_instance>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/>
    <metadata key="plater_name" value="Right"/>
    <model_instance>
      <metadata key="object_id" value="4"/>
      <metadata key="instance_id" value="0"/>
      <metadata key="identify_id" value="297"/>
    </model_instance>
    <model_instance>
      <metadata key="object_id" value="4"/>
      <metadata key="instance_id" value="1"/>
      <metadata key="identify_id" value="307"/>
    </model_instance>
  </plate>
  <assemble>
   <assemble_item object_id="2" instance_id="0" transform="1 0 0 0 1 0 0 0 1 0 0 0" offset="0 0 0" />
  </assemble>
</config>
"""
CFG = {
    "printable_area": ["0x0", "256x0", "256x256", "0x256"],
    "wipe_tower_x": ["165", "170"],
    "wipe_tower_y": ["250", "240"],
}


def plates(settings: bytes) -> list[list[tuple[str, int]]]:
    m = plate_structure._membership(settings)
    return [sorted(k for k, p in m.items() if p == i) for i in range(settings.count(b"<plate>"))]


def item(model: bytes, object_id: str, instance: int = 0) -> list[float]:
    return [round(v, 6) for v in plate_structure._instances(model)[(object_id, instance)][9:11]]


class TestCopyObjects:
    def test_a_copy_shares_the_meshes_with_new_ids_and_uuids(self):
        spec = (1, 0, 0, 0, 1, 0, 0, 0, 1, 50, -247.2, 5)
        model, settings, ids = plate_objects.copy_objects(MODEL, SETTINGS, [("2", [spec])])
        assert ids == ["5"]  # past every object and mesh id
        assert (
            b'  <object id="5" p:UUID="00000005-71cb-4c03-9d28-80fed5dfa1dc" type="model">\n   <components>\n'
            b'    <component p:path="/3D/Objects/object_1.model" objectid="1"'
            b' p:UUID="00050000-b206-40ff-9872-83e8017abed1"'
        ) in model
        assert (
            b'  <item objectid="5" p:UUID="00000500-b1ec-4553-aec9-835e5b724bb4"'
            b' transform="1 0 0 0 1 0 0 0 1 50 -247.2 5" printable="1"/>\n </build>'
        ) in model
        # everything else as it was
        new_object = model[model.index(b'  <object id="5"') : model.index(b" </resources>")]
        new_item = model[model.index(b'  <item objectid="5"') : model.index(b" </build>")]
        assert model.replace(new_object, b"").replace(new_item, b"") == MODEL
        # the settings block under its new id, its own settings and parts kept, after the last object
        block = SETTINGS[SETTINGS.index(b'  <object id="2">') : SETTINGS.index(b'  <object id="4">')]
        assert block.replace(b'<object id="2">', b'<object id="5">') + b"  <plate>" in settings
        assert settings.count(b"<assemble_item") == 1  # the assembly view is left as it is

    def test_an_object_with_its_own_mesh_is_copied_with_it(self):
        model = (
            b'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources>'
            b'<object id="1" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/></vertices></mesh></object>'
            b'</resources><build><item objectid="1" transform="1 0 0 0 1 0 0 0 1 9 9 0"/></build></model>'
        )
        settings = b'<config><object id="1"><part id="1"/></object><plate><metadata key="plater_id" value="1"/></plate></config>'
        out, out_settings, ids = plate_objects.copy_objects(
            model, settings, [("1", [(1, 0, 0, 0, 1, 0, 0, 0, 1, 1, 2, 0)])]
        )
        assert ids == ["2"]
        assert b'<object id="2" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/>' in out
        assert b'<item objectid="2" transform="1 0 0 0 1 0 0 0 1 1 2 0"/></build>' in out
        assert b'<object id="1"><part id="1"/></object><object id="2"><part id="1"/></object><plate>' in out_settings

    def test_an_object_not_in_the_model_is_refused(self):
        with pytest.raises(InvalidCopy, match="object 9 is not in the model"):
            plate_objects.copy_objects(MODEL, SETTINGS, [("9", [])])


class TestAssign:
    def test_a_moved_instance_goes_to_its_plate_with_its_own_bytes(self):
        out, applied = plate_structure.assign(SETTINGS, MODEL, [InstancePlate(object_id="2", instance=0, plate=2)])
        assert plates(out) == [[], [("2", 0), ("4", 0), ("4", 1)]]
        assert b'<metadata key="identify_id" value="287"/>\n    </model_instance>\n  </plate>\n  <assemble>' in out
        assert b'<metadata key="bed_type" value="Cool Plate"/>\n  </plate>' in out  # plate 1 keeps its settings
        assert applied == [{"key": "plate_object", "object_id": "2", "instance": 0, "before": 1, "after": 2}]

    def test_off_every_plate_and_back_with_a_new_identify_id(self):
        out, _ = plate_structure.assign(SETTINGS, MODEL, [InstancePlate(object_id="4", instance=1, plate=0)])
        assert plates(out) == [[("2", 0)], [("4", 0)]]
        back, applied = plate_structure.assign(out, MODEL, [InstancePlate(object_id="4", instance=1, plate=1)])
        assert plates(back) == [[("2", 0), ("4", 1)], [("4", 0)]]
        assert (
            b'    <model_instance>\n      <metadata key="object_id" value="4"/>\n'
            b'      <metadata key="instance_id" value="1"/>\n'
            b'      <metadata key="identify_id" value="298"/>\n    </model_instance>\n  </plate>'
        ) in back
        assert applied[0]["before"] == 0

    def test_the_plate_it_is_already_on_changes_nothing(self):
        moved = [InstancePlate(object_id="2", instance=0, plate=1)]
        assert plate_structure.assign(SETTINGS, MODEL, moved) == (SETTINGS, [])

    @pytest.mark.parametrize(
        ("moved", "error"),
        [
            ([{"object_id": "2", "instance": 1, "plate": 2}], "no instance 1"),
            ([{"object_id": "2", "instance": 0, "plate": 3}], "no plate 3"),
            ([{"object_id": "2", "instance": 0, "plate": 2}] * 2, "moved twice"),
        ],
    )
    def test_refuses(self, moved, error):
        with pytest.raises(InvalidPlateStructure, match=error):
            plate_structure.assign(SETTINGS, MODEL, [InstancePlate(**m) for m in moved])


class TestDuplicate:
    @staticmethod
    def run(*ops: dict):
        return plate_structure.apply(SETTINGS, CFG, [PlateOp(**o) for o in ops], MODEL)

    def test_a_duplicate_copies_the_plates_objects_onto_a_new_plate(self):
        out, cfg, model, applied = self.run({"op": "duplicate", "plate": 1})
        # 3 plates in 2 columns: the new one starts the second row, 307.2 mm to the front
        assert plates(out) == [[("2", 0)], [("4", 0), ("4", 1)], [("5", 0)]]
        assert item(model, "5") == [50.0, -247.2]
        assert item(model, "2") == [50.0, 60.0] and item(model, "4") == [400.0, 60.0]  # the others stay
        assert b'<object id="5">\n    <metadata key="name" value="Cube A"/>\n    <metadata key="wall_loops"' in out
        # a new plate (none of the plate's own settings, as duplicate_plate) listing its copy, the next identify_id
        new = out[out.index(b'"plater_id" value="3"') :]
        assert b"bed_type" not in new and b'<metadata key="identify_id" value="308"/>' in new
        assert cfg["wipe_tower_x"] == ["165", "170", "165"]
        assert applied == [
            {"key": "plate", "op": "duplicate", "plate": 1, "plates": 3, "objects": 1, "copies": [["2", "5"]]}
        ]

    def test_every_instance_is_copied_and_the_objects_given_are_the_ones(self):
        out, _, model, _ = self.run({"op": "duplicate", "plate": 2, "objects": ["4"]})
        assert plates(out)[2] == [("5", 0), ("5", 1)]
        assert [item(model, "5", k) for k in (0, 1)] == [[92.8, -247.2], [142.8, -247.2]]
        # the objects the plate held when it was duplicated, whatever the file lists on it
        out, _, _, applied = self.run({"op": "duplicate", "plate": 1, "objects": []})
        assert plates(out)[2] == [] and applied[0]["objects"] == 0

    def test_copies_move_with_their_plate_and_an_empty_new_plate_duplicates_into_an_empty_one(self):
        out, _, model, _ = self.run({"op": "duplicate", "plate": 1}, {"op": "front", "plate": 3})
        assert plates(out) == [[("5", 0)], [("2", 0)], [("4", 0), ("4", 1)]]
        assert item(model, "5") == [50.0, 60.0] and item(model, "2") == [357.2, 60.0]
        out, _, _, applied = self.run({"op": "add"}, {"op": "duplicate", "plate": 3})
        assert plates(out)[3] == [] and applied[1]["objects"] == 0
        with pytest.raises(InvalidPlateStructure, match="no objects to copy"):
            self.run({"op": "add"}, {"op": "duplicate", "plate": 3, "objects": ["2"]})

    def test_copies_come_from_where_the_instances_were_when_duplicated(self):
        moved = MODEL.replace(b"1 50 60 5", b"1 500 60 5")  # object 2 placed onto plate 2's bed
        ops = [PlateOp(op="duplicate", plate=1)]
        # placed after the duplicate (not in the op): the copy is where the file had it
        _, _, model, _ = plate_structure.apply(SETTINGS, CFG, ops, moved, MODEL)
        assert item(model, "5") == [50.0, -247.2] and item(model, "2") == [500.0, 60.0]
        # placed before it (in the op): the copy is where it was then
        then = [{"object_id": "2", "instance": 0, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 70, 60, 5]}]
        ops = [PlateOp(op="duplicate", plate=1, instances=then)]
        _, _, model, _ = plate_structure.apply(SETTINGS, CFG, ops, moved, MODEL)
        assert item(model, "5") == [70.0, -247.2]

    def test_a_duplicate_of_a_duplicate_copies_the_copies(self):
        out, _, model, _ = self.run({"op": "duplicate", "plate": 1}, {"op": "duplicate", "plate": 3})
        assert plates(out)[2:] == [[("5", 0)], [("6", 0)]]
        assert item(model, "6") == [357.2, -247.2]  # plate 4: second row, second column

    def test_an_instance_put_on_a_new_plate_is_listed_there_where_it_was_put(self):
        # plate 1's object put where a third plate will be (the second row), then the plate added and plate 1 removed
        on_file, _ = plate_structure.assign(SETTINGS, MODEL, [InstancePlate(object_id="2", instance=0, plate=0)])
        onto = [InstancePlate(object_id="2", instance=0, plate=2, new_plate=True)]
        ops = [PlateOp(op="add"), PlateOp(op="remove", plate=1)]
        out, _, model, _ = plate_structure.apply(on_file, CFG, ops, MODEL, MODEL, onto)
        assert plates(out) == [[("4", 0), ("4", 1)], [("2", 0)]]
        assert item(model, "2") == [50.0, 60.0]  # where it was put: it doesn't move with any plate
        assert item(model, "4") == [92.8, 60.0]  # plate 2 took plate 1's place
        assert b'<metadata key="identify_id" value="308"/>' in out[out.index(b'"plater_id" value="2"') :]

    @pytest.mark.parametrize(
        ("onto", "error"),
        [
            ({"object_id": "2", "instance": 0, "plate": 1, "new_plate": True}, "not a plate these changes add"),
            ({"object_id": "4", "instance": 0, "plate": 3, "new_plate": True}, "on another plate"),
            ({"object_id": "2", "instance": 5, "plate": 3, "new_plate": True}, "no instance 5"),
        ],
    )
    def test_putting_one_on_a_new_plate_is_refused(self, onto, error):
        on_file, _ = plate_structure.assign(SETTINGS, MODEL, [InstancePlate(object_id="2", instance=0, plate=0)])
        with pytest.raises(InvalidPlateStructure, match=error):
            plate_structure.apply(on_file, CFG, [PlateOp(op="add")], MODEL, MODEL, [InstancePlate(**onto)])

    def test_refused_without_the_model_or_past_36_plates(self):
        with pytest.raises(InvalidPlateStructure, match="without the model"):
            plate_structure.apply(SETTINGS, CFG, [PlateOp(op="duplicate", plate=1)])
        with pytest.raises(InvalidPlateStructure, match="at most 36 plates"):
            self.run(*[{"op": "add"}] * 34, {"op": "duplicate", "plate": 1})


@pytest.mark.integration
class TestSaveCopy:
    async def test_a_moved_object_and_a_duplicate_reopen_where_they_are(self, async_client, db_session):
        original = make_project("orca", project_settings(printable_area=CFG["printable_area"]))
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        # object 2 moved from plate 1 onto plate 2's bed (x 307.2 to 563.2), then plate 2 duplicated with both on it
        resp = await save(
            async_client,
            doc,
            {},
            transforms=[{"object_id": "2", "instance": 0, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 400, 50, 5]}],
            instance_plates=[{"object_id": "2", "instance": 0, "plate": 2}],
            plate_structure=[
                {
                    "op": "duplicate",
                    "plate": 2,
                    "objects": ["4", "2"],
                    "instances": [{"object_id": "2", "instance": 0, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 400, 50, 5]}],
                }
            ],
        )
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        assert sorted(n for n in before if before[n] != after[n]) == [project.MODEL_ENTRY, project.MODEL_SETTINGS_ENTRY]
        copy = await open_ws(async_client, row.id)
        assert [(p["index"], sorted(p["object_ids"])) for p in copy["plates"]] == [
            (1, []),
            (2, ["2", "4"]),
            (3, ["5", "6"]),
        ]
        assert sorted(o["name"] for o in copy["objects"] if o["id"] in ("5", "6")) == ["Cube A", "Cube B"]
        assert {"key": "plate_object", "object_id": "2", "instance": 0, "before": 1, "after": 2} in resp.json()[
            "changes"
        ]

    async def test_an_object_moved_onto_a_plate_added_before_saving(self, async_client, db_session):
        original = make_project("orca", project_settings(printable_area=CFG["printable_area"]))
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        # a third plate added (the second row, y -307.2 to -51.2) and object 2 moved onto it
        resp = await save(
            async_client,
            doc,
            {},
            transforms=[{"object_id": "2", "instance": 0, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 50, -200, 5]}],
            instance_plates=[{"object_id": "2", "instance": 0, "plate": 3, "new_plate": True}],
            plate_structure=[{"op": "add"}],
        )
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        copy = await open_ws(async_client, row.id)
        assert [(p["index"], p["object_ids"]) for p in copy["plates"]] == [(1, []), (2, ["4"]), (3, ["2"])]
        moved = [c for c in resp.json()["changes"] if c["key"] == "plate_object"]
        assert moved == [
            {"key": "plate_object", "object_id": "2", "instance": 0, "before": 1, "after": 3, "new_plate": True}
        ]
        model = entries(library.to_absolute_path(row.file_path).read_bytes())[project.MODEL_ENTRY]
        assert b'objectid="2" transform="1 0 0 0 1 0 0 0 1 50 -200 5"' in model
        # without a plate added there is none to put it on
        resp = await save(
            async_client, doc, {}, instance_plates=[{"object_id": "2", "instance": 0, "plate": 3, "new_plate": True}]
        )
        assert resp.status_code == 422 and "no plate is added" in resp.json()["detail"]

    async def test_an_unknown_instance_saves_nothing(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca"))
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, instance_plates=[{"object_id": "9", "instance": 0, "plate": 1}])
        assert resp.status_code == 422 and "no instance" in resp.json()["detail"]
