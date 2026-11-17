"""Printfarm (fork): objects and instances deleted, printable or not, dropping automatically or not and renamed in a
workspace copy (OrcaSlicer's Delete, Delete All, the plate's Delete All, Printable, Auto Drop and Rename).

A deletion drops the build items, the plates' and the assembly's entries and (the last instance gone) the object's
resource and settings; the object's later instances are numbered again, and so are the files that name objects by
their place in the model. Everything else stays.
"""

from __future__ import annotations

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import objects, placement, plate_objects, plate_structure, project
from backend.app.farm.workspace.objects import InvalidObjectOp, ObjectOp
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import (
    API,
    add_file,
    entries,
    make_project,
    open_ws,
    project_settings,
    save,
)
from backend.tests.integration.test_farm_workspace_layer_ranges import RANGES
from backend.tests.integration.test_farm_workspace_plate_objects import MODEL, SETTINGS

HEIGHTS = b"object_id=1|0 0.2 10 0.1\nobject_id=2|0 0.2 20 0.3\n"
ITEM_2 = (
    b'  <item objectid="2" p:UUID="00000002-b1ec-4553-aec9-835e5b724bb4" transform="1 0 0 0 1 0 0 0 1 50 60 5"'
    b' printable="1"/>\n'
)


def run(*ops: dict, model: bytes = MODEL, settings: bytes = SETTINGS, indexed=None):
    return objects.apply(model, settings, [ObjectOp(**o) for o in ops], indexed)


def plates(settings: bytes) -> list[list[tuple[str, int]]]:
    m = plate_structure._membership(settings)
    return [sorted(k for k, p in m.items() if p == i) for i in range(settings.count(b"<plate>"))]


def items(model: bytes) -> list[tuple[str, str]]:
    return [(i["objectid"], i["transform"]) for i in placement._build_items(model)]


class TestDelete:
    def test_an_instance_goes_and_the_later_ones_are_numbered_again(self):
        model, settings, changed, applied = run({"op": "delete", "object_id": "4", "instance": 0})
        assert items(model) == [("2", "1 0 0 0 1 0 0 0 1 50 60 5"), ("4", "1 0 0 0 1 0 0 0 1 450 60 5")]
        assert b'<object id="4"' in model  # one instance left: the object stays
        # its second instance is its first now, with its own identify_id
        assert plates(settings) == [[("2", 0)], [("4", 0)]]
        assert b'<metadata key="instance_id" value="0"/>\n      <metadata key="identify_id" value="307"/>' in settings
        assert b'value="297"' not in settings
        assert changed == {}
        assert applied == [{"key": "delete", "object_id": "4", "instances": [0], "object": False}]

    def test_an_object_goes_with_its_settings_plates_assembly_and_numbered_files(self):
        indexed = {project.LAYER_RANGES_ENTRY: RANGES, objects.HEIGHTS_ENTRY: HEIGHTS}
        model, settings, changed, applied = run({"op": "delete", "object_id": "2"}, indexed=indexed)
        assert [oid for oid, _ in items(model)] == ["4", "4"]
        assert b'<object id="2"' not in model and b'objectid="1"' not in model  # its resource, not its mesh's file
        assert b'<object id="2">' not in settings and b"<assemble_item" not in settings
        assert plates(settings) == [[], [("4", 0), ("4", 1)]]
        assert b'<metadata key="plater_name" value="Left"/>' in settings  # the plate stays, empty
        # object 4 was the model's second object, it is the first now; object 2's entries go
        assert changed[project.LAYER_RANGES_ENTRY] == RANGES.replace(b'<object id="2">', b'<object id="1">')
        assert changed[objects.HEIGHTS_ENTRY] == b"object_id=1|0 0.2 20 0.3\n"
        assert applied[0]["object"] is True

    def test_an_instance_deleted_can_change_the_objects_order(self):
        # the build names object 4 first, then 2, then 4 again: without 4's first instance, 2 comes first
        reordered = MODEL.replace(ITEM_2, b"").replace(
            b'  <item objectid="4" p:UUID="00000005', ITEM_2 + b'  <item objectid="4" p:UUID="00000005'
        )
        assert [oid for oid, _ in items(reordered)] == ["4", "2", "4"]
        indexed = {objects.HEIGHTS_ENTRY: HEIGHTS}
        _, _, changed, _ = run({"op": "delete", "object_id": "4", "instance": 0}, model=reordered, indexed=indexed)
        assert changed[objects.HEIGHTS_ENTRY] == b"object_id=2|0 0.2 10 0.1\nobject_id=1|0 0.2 20 0.3\n"

    def test_delete_all_leaves_the_plates_empty(self):
        model, settings, _, _ = run({"op": "delete", "object_id": "2"}, {"op": "delete", "object_id": "4"})
        assert items(model) == [] and b"<build" in model
        assert plates(settings) == [[], []]

    @pytest.mark.parametrize(
        ("op", "error"),
        [
            ({"op": "delete", "object_id": "4", "instance": 2}, "no instance 2"),
            ({"op": "delete", "object_id": "9"}, "no instance"),
            ({"op": "printable", "object_id": "9", "value": False}, "no object 9"),
        ],
    )
    def test_refuses(self, op, error):
        with pytest.raises(InvalidObjectOp, match=error):
            run(op)


class TestFlags:
    def test_printable_per_instance_or_every_instance(self):
        model, _, _, applied = run(
            {"op": "printable", "object_id": "4", "instance": 1, "value": False},
            {"op": "printable", "object_id": "2", "value": False},
        )
        assert [i.get("printable") for i in placement._build_items(model)] == ["0", "1", "0"]
        assert model.replace(b'printable="0"', b'printable="1"') == MODEL
        assert applied[0] == {"key": "printable", "object_id": "4", "instance": 1, "after": False}

    def test_auto_drop_is_the_objects_attribute(self):
        _, settings, _, _ = run({"op": "auto_drop", "object_id": "2", "value": False})
        assert b'<object id="2" auto_drop="0">' in settings
        _, back, _, _ = run({"op": "auto_drop", "object_id": "2", "value": True}, settings=settings)
        assert b'<object id="2" auto_drop="1">' in back

    def test_rename_an_object_of_one_part_renames_the_part_too(self):
        _, settings, _, applied = run({"op": "rename", "object_id": "2", "value": "Bracket & co"})
        assert settings.count(b'<metadata key="name" value="Bracket &amp; co"/>') == 2
        _, part, _, _ = run({"op": "rename", "object_id": "4", "part_id": "3", "value": "Lid"})
        assert (
            b'<metadata key="name" value="Cube B"/>\n    <part id="3" subtype="normal_part">\n'
            b'      <metadata key="name" value="Lid"/>'
        ) in part
        assert applied == [{"key": "rename", "object_id": "2", "part_id": None, "after": "Bracket & co"}]

    @pytest.mark.parametrize("name", ["", "  ", "a/b", 'say "hi"', "x" * 256])
    def test_a_name_orca_refuses(self, name):
        with pytest.raises(InvalidObjectOp):
            run({"op": "rename", "object_id": "2", "value": name})


@pytest.mark.integration
class TestSaveCopy:
    async def test_a_deleted_object_and_its_ranges(self, async_client, db_session):
        original = make_project("orca", project_settings(), extra={project.LAYER_RANGES_ENTRY: RANGES.decode()})
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        ops = [{"op": "delete", "object_id": "2"}, {"op": "rename", "object_id": "4", "value": "Lid"}]
        resp = await save(async_client, doc, {}, object_ops=ops)
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        assert sorted(n for n in before if before[n] != after[n]) == sorted(
            [project.MODEL_ENTRY, project.LAYER_RANGES_ENTRY, project.MODEL_SETTINGS_ENTRY]
        )
        copy = await open_ws(async_client, row.id)
        assert [(o["id"], o["name"]) for o in copy["objects"]] == [("4", "Lid")]
        # object 4's ranges follow it: the model's first object now
        assert copy["objects"][0]["ranges"][0]["options"]["layer_height"] == "0.1"
        assert [p["object_ids"] for p in copy["plates"]] == [[], ["4"]]

    async def test_delete_all_reopens_empty(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca", project_settings()))
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, object_ops=[{"op": "delete", "object_id": i} for i in ("2", "4")])
        assert resp.status_code == 200, resp.text
        copy = await open_ws(async_client, resp.json()["library_file_id"])
        assert copy["objects"] == [] and len(copy["plates"]) == 2

    async def test_refused_with_a_duplicate_or_an_arrange(self, async_client, db_session):
        f = await add_file(db_session, make_project("orca", project_settings()))
        doc = await open_ws(async_client, f.id)
        ops = [{"op": "delete", "object_id": "2"}]
        resp = await save(async_client, doc, {}, object_ops=ops, plate_structure=[{"op": "duplicate", "plate": 2}])
        assert resp.status_code == 422 and "then duplicate" in resp.json()["detail"]
        resp = await async_client.post(f"{API}/{f.id}/arrange", json={"base_sha256": doc["sha256"], "object_ops": ops})
        assert resp.status_code == 422 and "then arrange" in resp.json()["detail"]
        resp = await save(async_client, doc, {}, object_ops=[{"op": "rename", "object_id": "2", "value": "a|b"}])
        assert resp.status_code == 422 and "not allowed" in resp.json()["detail"]


MOVED = [1, 0, 0, 0, 1, 0, 0, 0, 1, 80, 60, 5]


def grow(*ops: dict, indexed=None):
    return objects.add(MODEL, SETTINGS, [ObjectOp(**o) for o in ops], indexed)


class TestAdd:
    def test_a_new_instance_copies_the_objects_last_one_at_its_place(self):
        model, settings, changed, applied = grow({"op": "add_instance", "object_id": "2", "matrix": MOVED})
        # after the object's own items, as Orca saves them: the objects keep their places
        assert [oid for oid, _ in items(model)] == ["2", "2", "4", "4"]
        assert items(model)[1] == ("2", "1 0 0 0 1 0 0 0 1 80 60 5")
        new = [i for i in placement._build_items(model) if i["objectid"] == "2"][1]
        assert new["printable"] == "1" and new["{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}UUID"]
        assert settings == SETTINGS and changed == {}  # on no plate until the copy puts it on one
        assert applied == [{"key": "instance", "object_id": "2", "instance": 1}]

    def test_a_copy_takes_the_id_asked_for_and_its_objects_numbered_lines(self):
        first = plate_objects.next_object_id(MODEL, SETTINGS)
        model, settings, changed, applied = grow(
            {"op": "copy", "object_id": "4", "new_id": str(first + 3), "matrices": [MOVED, MOVED]},
            indexed={objects.HEIGHTS_ENTRY: HEIGHTS},
        )
        assert [oid for oid, _ in items(model)] == ["2", "4", "4", str(first + 3), str(first + 3)]
        assert f'<object id="{first + 3}">'.encode() in settings
        # object 4 is the model's 2nd object, its copy the 3rd: the copy's line repeats 4's
        assert changed[objects.HEIGHTS_ENTRY] == HEIGHTS + b"object_id=3|0 0.2 20 0.3\n"
        assert applied[0]["copies"] == [["4", str(first + 3)]]

    @pytest.mark.parametrize(
        ("op", "error"),
        [
            ({"op": "copy", "object_id": "4", "new_id": "3", "matrices": [MOVED]}, "from 5 on"),
            ({"op": "copy", "object_id": "4", "new_id": "9"}, "transforms"),
            ({"op": "add_instance", "object_id": "9", "matrix": MOVED}, "no object 9"),
            ({"op": "add_instance", "object_id": "2", "matrix": [1, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 0]}, "mirrors"),
        ],
    )
    def test_refuses(self, op, error):
        with pytest.raises(InvalidObjectOp, match=error):
            grow(op)


@pytest.mark.integration
class TestSaveAdditions:
    async def test_new_instances_and_copies_land_on_their_plates_with_their_ranges(self, async_client, db_session):
        original = make_project("orca", project_settings(), extra={project.LAYER_RANGES_ENTRY: RANGES.decode()})
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        new_id = str(doc["next_object_id"])
        ops = [
            {"op": "add_instance", "object_id": "2", "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 90, 50, 5]},
            {"op": "copy", "object_id": "4", "new_id": new_id, "matrices": [[1, 0, 0, 0, 1, 0, 0, 0, 1, 320, 90, 5]]},
            {"op": "rename", "object_id": new_id, "value": "Copy of B"},
        ]
        plates = [
            {"object_id": "2", "instance": 1, "plate": 1},
            {"object_id": new_id, "instance": 0, "plate": 2},
        ]
        # a move of the new instance after it was added: placed on it
        moved = [{"object_id": "2", "instance": 1, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 95, 55, 5]}]
        resp = await save(async_client, doc, {}, object_ops=ops, instance_plates=plates, transforms=moved)
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        copy = await open_ws(async_client, row.id)
        assert [(p["index"], sorted(set(p["object_ids"]))) for p in copy["plates"]] == [
            (1, ["2"]),
            (2, sorted(["4", new_id])),
        ]
        assert copy["plates"][0]["instances"] == [
            {"object_id": "2", "instance_id": "0"},
            {"object_id": "2", "instance_id": "1"},
        ]
        clone = next(o for o in copy["objects"] if o["id"] == new_id)
        assert clone["name"] == "Copy of B"
        # object 4's ranges are its copy's too (Model::add_object)
        assert clone["ranges"] == next(o["ranges"] for o in copy["objects"] if o["id"] == "4")
        model = entries(library.to_absolute_path(row.file_path).read_bytes())[project.MODEL_ENTRY]
        built = [(i["objectid"], i["transform"]) for i in placement._build_items(model)]
        assert list(dict.fromkeys(oid for oid, _ in built)) == ["2", "4", new_id]
        assert [t for oid, t in built if oid == "2"][-1] == "1 0 0 0 1 0 0 0 1 95 55 5"
        assert built[-1] == (new_id, "1 0 0 0 1 0 0 0 1 320 90 5")

    async def test_cut_and_paste_keeps_the_ranges(self, async_client, db_session):
        # Cut then Paste: the copy made and its source deleted in one save; the copy has the source's ranges
        original = make_project("orca", project_settings(), extra={project.LAYER_RANGES_ENTRY: RANGES.decode()})
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        new_id = str(doc["next_object_id"])
        ops = [
            {"op": "copy", "object_id": "4", "new_id": new_id, "matrices": [[1, 0, 0, 0, 1, 0, 0, 0, 1, 320, 90, 5]]},
            {"op": "delete", "object_id": "4"},
        ]
        resp = await save(async_client, doc, {}, object_ops=ops)
        assert resp.status_code == 200, resp.text
        copy = await open_ws(async_client, resp.json()["library_file_id"])
        assert [o["id"] for o in copy["objects"]] == ["2", new_id]
        source = next(o for o in doc["objects"] if o["id"] == "4")
        assert source["ranges"] and copy["objects"][1]["ranges"] == source["ranges"]

    async def test_an_instance_added_then_the_first_deleted_keeps_the_numbered_files(self, async_client, db_session):
        original = make_project("orca", project_settings(), extra={project.LAYER_RANGES_ENTRY: RANGES.decode()})
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        ops = [
            {"op": "add_instance", "object_id": "2", "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 90, 50, 5]},
            {"op": "delete", "object_id": "2", "instance": 0},
        ]
        resp = await save(async_client, doc, {}, object_ops=ops)
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        # object 2 stays the model's first object: its ranges' place is unchanged
        assert after[project.LAYER_RANGES_ENTRY] == before[project.LAYER_RANGES_ENTRY]
        copy = await open_ws(async_client, row.id)
        assert [o["ranges"] for o in copy["objects"]] == [o["ranges"] for o in doc["objects"]]
        built = [(i["objectid"], i["transform"]) for i in placement._build_items(after[project.MODEL_ENTRY])]
        assert built[0] == ("2", "1 0 0 0 1 0 0 0 1 90 50 5")
