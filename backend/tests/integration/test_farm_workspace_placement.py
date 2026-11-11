"""Printfarm (fork): instances placed anew in a workspace copy (OrcaSlicer's Move / Rotate / Scale).

Only the placed build items' ``transform`` changes in ``3D/3dmodel.model``; every other byte of it, and
every other entry, stays as it was; the original is untouched.
"""

from __future__ import annotations

import io
import math
import zipfile

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import placement, project
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import MODEL_SETTINGS, add_file, entries, make_project, open_ws, save

MOVED = [1, 0, 0, 0, 1, 0, 0, 0, 1, 80, 60, 5]
TURNED = [0, 1, 0, -1, 0, 0, 0, 0, 1, 50, 50, 5]  # 90° about Z


def model_of(data: bytes) -> bytes:
    return entries(data)[project.MODEL_ENTRY]


class TestPlace:
    def test_only_the_transform_changes(self):
        model = model_of(make_project())
        out, placed = placement.place(model, [placement.InstanceTransform(object_id="4", instance=0, matrix=MOVED)])
        assert out == model.replace(b"1 0 0 0 1 0 0 0 1 300 50 5", b"1 0 0 0 1 0 0 0 1 80 60 5")
        assert placed == [
            {
                "key": "transform",
                "object_id": "4",
                "instance": 0,
                "before": "1 0 0 0 1 0 0 0 1 300 50 5",
                "after": "1 0 0 0 1 0 0 0 1 80 60 5",
            }
        ]

    def test_the_nth_item_of_an_object_is_its_instance_n_and_a_missing_transform_is_added(self):
        model = (
            b'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources/><build>'
            b'<item objectid="2" transform="1 0 0 0 1 0 0 0 1 10 10 0"/><item objectid="3"/>'
            b"<item objectid='2' printable='1'/></build></model>"
        )
        out, _ = placement.place(
            model,
            [
                placement.InstanceTransform(object_id="2", instance=1, matrix=TURNED),
                placement.InstanceTransform(object_id="3", instance=0, matrix=MOVED),
            ],
        )
        assert b'<item objectid="2" transform="1 0 0 0 1 0 0 0 1 10 10 0"/>' in out  # instance 0 as it was
        assert b'<item transform="1 0 0 0 1 0 0 0 1 80 60 5" objectid="3"/>' in out
        assert b"<item transform=\"0 1 0 -1 0 0 0 0 1 50 50 5\" objectid='2' printable='1'/>" in out

    def test_numbers_are_written_plainly(self):
        m = [math.cos(0.5), math.sin(0.5), 0, -math.sin(0.5), math.cos(0.5), 0, 0, 0, 1, 1e-12, -0.0, 123.4567891234]
        assert (
            placement.format_matrix(m) == "0.877582562 0.479425539 0 -0.479425539 0.877582562 0 0 0 1 0 0 123.456789123"
        )

    @pytest.mark.parametrize(
        ("matrix", "error"),
        [
            ([1, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 0], "mirrors or flattens"),
            ([1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0], "mirrors or flattens"),
            ([1e-4, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], "a scale is outside"),
            ([2000, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0], "a scale is outside"),
            ([1, 0, 0, 0, 1, 0, 0, 0, 1, 1e6, 0, 0], "more than"),
            ([1, 0, 0, 0, 1, 0, 0, 0, 1, float("nan"), 0, 0], "not a number"),
        ],
    )
    def test_refuses_what_the_slicer_would_not_take(self, matrix, error):
        with pytest.raises(placement.InvalidPlacement, match=error):
            placement.check_matrix(matrix)

    def test_refuses_an_instance_the_model_does_not_have_or_one_placed_twice(self):
        model = model_of(make_project())
        with pytest.raises(placement.InvalidPlacement, match="has no instance 1"):
            placement.place(model, [placement.InstanceTransform(object_id="2", instance=1, matrix=MOVED)])
        twice = [placement.InstanceTransform(object_id="2", instance=0, matrix=MOVED)] * 2
        with pytest.raises(placement.InvalidPlacement, match="placed twice"):
            placement.place(model, twice)

    def test_only_the_build_is_parsed_inside_the_models_root_tag(self, monkeypatch):
        # an Orca model with its meshes inline: they are not parsed (a mesh here is not even valid XML), and
        # Bambu Studio's p:UUID resolves through the root tag's namespace
        model = (
            b'<?xml version="1.0"?><model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
            b'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"><resources>'
            b'<object id="2"><mesh><vertices><vertex x="0" y="0" z="0"><build>not this</build></mesh></object>'
            b'</resources><build><item objectid="2" p:UUID="00000002" transform="1 0 0 0 1 0 0 0 1 10 10 0"/></build></model>'
        )
        parsed = []
        real = placement.SafeET.fromstring
        monkeypatch.setattr(placement.SafeET, "fromstring", lambda text: parsed.append(text) or real(text))
        out, _ = placement.place(model, [placement.InstanceTransform(object_id="2", instance=0, matrix=MOVED)])
        assert out == model.replace(b"1 0 0 0 1 0 0 0 1 10 10 0", b"1 0 0 0 1 0 0 0 1 80 60 5")
        assert len(parsed) == 2 and not any(b"<mesh" in text for text in parsed)  # before and after: the build alone


class TestAutoDrop:
    async def test_each_objects_auto_drop_is_read_on_unless_off(self, async_client, db_session):
        # bbs_3mf writes <object ... auto_drop="0"> for an object Orca must not put back on the bed after a move
        settings = MODEL_SETTINGS.replace('<object id="4">', '<object id="4" auto_drop="0">')
        assert settings != MODEL_SETTINGS
        f = await add_file(db_session, make_project(model_settings=settings))
        doc = await open_ws(async_client, f.id)
        assert {o["id"]: o["auto_drop"] for o in doc["objects"]} == {"2": True, "4": False}


class TestSaveCopy:
    async def test_a_model_too_large_to_rewrite_is_said_before_any_move(self, async_client, db_session, monkeypatch):
        monkeypatch.setattr(project, "MAX_MODEL_BYTES", 100)
        f = await add_file(db_session, make_project())
        doc = await open_ws(async_client, f.id)
        assert "larger than" in doc["cannot_place"]
        resp = await save(async_client, doc, {}, transforms=[{"object_id": "2", "instance": 0, "matrix": MOVED}])
        assert resp.status_code == 422, resp.text
        monkeypatch.setattr(project, "MAX_MODEL_BYTES", 256 * 1024 * 1024)
        assert (await open_ws(async_client, f.id))["cannot_place"] is None

    async def test_a_moved_instance_is_saved_in_the_copy_and_the_original_stays(self, async_client, db_session):
        original = make_project("bambu")
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        moved = {"object_id": "4", "instance": 0, "matrix": MOVED}
        resp = await save(async_client, doc, {}, transforms=[moved])  # a placement alone is a change
        assert resp.status_code == 200, resp.text
        assert [(c["key"], c.get("object_id")) for c in resp.json()["changes"]] == [("transform", "4")]
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        copy = library.to_absolute_path(row.file_path).read_bytes()
        before, after = entries(original), entries(copy)
        assert list(before) == list(after)
        for name in before:
            if name != project.MODEL_ENTRY:
                assert before[name] == after[name], name
        assert after[project.MODEL_ENTRY] == before[project.MODEL_ENTRY].replace(b"300 50 5", b"80 60 5")
        assert library.to_absolute_path(f.file_path).read_bytes() == original

    async def test_a_move_alone_keeps_the_settings_bytes_as_bambu_studio_wrote_them(self, async_client, db_session):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries(make_project("bambu")).items():
                crlf_settings = name == project.PROJECT_SETTINGS_ENTRY  # Bambu Studio's line breaks
                zf.writestr(name, data.replace(b"\n", b"\r\n") if crlf_settings else data)
        crlf = buf.getvalue()
        f = await add_file(db_session, crlf)
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, transforms=[{"object_id": "4", "instance": 0, "matrix": MOVED}])
        assert resp.status_code == 200, resp.text
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        after = entries(library.to_absolute_path(row.file_path).read_bytes())
        assert after[project.PROJECT_SETTINGS_ENTRY] == entries(crlf)[project.PROJECT_SETTINGS_ENTRY]

    async def test_with_settings_changes_both_are_saved(self, async_client, db_session):
        f = await add_file(db_session, make_project())
        doc = await open_ws(async_client, f.id)
        turned = {"object_id": "2", "instance": 0, "matrix": TURNED}
        resp = await save(async_client, doc, {"layer_height": "0.12"}, transforms=[turned])
        assert resp.status_code == 200, resp.text
        assert [c["key"] for c in resp.json()["changes"]] == ["layer_height", "transform"]

    @pytest.mark.parametrize(
        "transform",
        [
            {"object_id": "9", "instance": 0, "matrix": MOVED},  # no such object
            {"object_id": "2", "instance": 0, "matrix": [1, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 0]},  # mirrored
            {"object_id": "2", "instance": 0, "matrix": [1, 0, 0]},  # not 12 numbers
            {"object_id": "../2", "instance": 0, "matrix": MOVED},  # not an id
        ],
    )
    async def test_a_placement_that_cannot_be_saved_is_refused(self, async_client, db_session, transform):
        f = await add_file(db_session, make_project())
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {}, transforms=[transform])
        assert resp.status_code == 422, resp.text


class TestPlaceParts:
    """A part is its object's component: its transform in 3dmodel.model and its matrix in model_settings.config."""

    MODEL = (
        b'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        b'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"><resources>'
        b'<object id="2" type="model"><components>'
        b'<component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 5 0 0"/>'
        b'<component p:path="/3D/Objects/object_1.model" objectid="3" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
        b"</components></object></resources>"
        b'<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 100 100 0"/></build></model>'
    )
    SETTINGS = (
        b'<?xml version="1.0" encoding="UTF-8"?>\r\n<config>\r\n  <object id="2">\r\n'
        b'    <part id="1" subtype="normal_part"><metadata key="name" value="A"/>'
        b'<metadata key="matrix" value="1 0 0 5 0 1 0 0 0 0 1 0 0 0 0 1"/></part>\r\n'
        b'    <part id="3" subtype="modifier_part"><metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/></part>\r\n'
        b'  </object>\r\n  <assemble><assemble_item object_id="2" instance_id="0" transform="1 0 0 0 1 0 0 0 1 9 9 9"/></assemble>\r\n</config>'
    )

    def test_the_component_and_its_matrix_move_together_and_nothing_else(self):
        moved = placement.PartTransform(object_id="2", part_id="1", matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 15, 2, 0])
        model, settings, placed = placement.place_parts(self.MODEL, self.SETTINGS, [moved])
        assert model == self.MODEL.replace(
            b'objectid="1" transform="1 0 0 0 1 0 0 0 1 5 0 0"', b'objectid="1" transform="1 0 0 0 1 0 0 0 1 15 2 0"'
        )
        # Orca writes matrix = the part's matrix x its source transform: the change (+10, +2) times the old one
        assert settings == self.SETTINGS.replace(
            b'value="1 0 0 5 0 1 0 0 0 0 1 0 0 0 0 1"', b'value="1 0 0 15 0 1 0 2 0 0 1 0 0 0 0 1"'
        )
        assert placed == [
            {
                "key": "part_transform",
                "object_id": "2",
                "part_id": "1",
                "before": "1 0 0 0 1 0 0 0 1 5 0 0",
                "after": "1 0 0 0 1 0 0 0 1 15 2 0",
                "matrix": "1 0 0 15 0 1 0 2 0 0 1 0 0 0 0 1",
            }
        ]

    def test_a_turned_part_turns_its_matrix(self):
        turned = placement.PartTransform(object_id="2", part_id="3", matrix=[0, 1, 0, -1, 0, 0, 0, 0, 1, 0, 0, 0])
        _, settings, _ = placement.place_parts(self.MODEL, self.SETTINGS, [turned])
        # 90° about Z on column vectors, written row by row
        assert b'value="0 -1 0 0 1 0 0 0 0 0 1 0 0 0 0 1"' in settings

    def test_a_part_without_a_matrix_or_settings_leaves_them(self):
        moved = placement.PartTransform(object_id="2", part_id="1", matrix=[1, 0, 0, 0, 1, 0, 0, 0, 1, 15, 2, 0])
        model, settings, placed = placement.place_parts(self.MODEL, None, [moved])
        assert settings is None and placed[0]["matrix"] is None
        no_matrix = self.SETTINGS.replace(b'<metadata key="matrix" value="1 0 0 5 0 1 0 0 0 0 1 0 0 0 0 1"/>', b"")
        assert placement.place_parts(self.MODEL, no_matrix, [moved])[1] == no_matrix

    @pytest.mark.parametrize(
        ("part", "error"),
        [
            ({"object_id": "2", "part_id": "9", "matrix": MOVED}, "no part 9"),
            ({"object_id": "7", "part_id": "1", "matrix": MOVED}, "not one element"),
            ({"object_id": "2", "part_id": "1", "matrix": [1, 0, 0, 0, 1, 0, 0, 0, -1, 0, 0, 0]}, "mirrors"),
        ],
    )
    def test_refuses_what_it_cannot_place(self, part, error):
        with pytest.raises(placement.InvalidPlacement, match=error):
            placement.place_parts(self.MODEL, self.SETTINGS, [placement.PartTransform(**part)])
        twice = [placement.PartTransform(object_id="2", part_id="1", matrix=MOVED)] * 2
        with pytest.raises(placement.InvalidPlacement, match="placed twice"):
            placement.place_parts(self.MODEL, self.SETTINGS, twice)

    async def test_a_copy_with_a_part_moved(self, async_client, db_session):
        original = make_project("bambu")
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        part = {"object_id": "2", "part_id": "1", "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1, 3, 0, 0]}
        resp = await save(async_client, doc, {}, part_transforms=[part])
        assert resp.status_code == 200, resp.text
        assert [c["key"] for c in resp.json()["changes"]] == ["part_transform"]
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        assert [n for n in before if before[n] != after[n]] == [project.MODEL_ENTRY]  # no matrix in this project
        assert (
            b'<component transform="1 0 0 0 1 0 0 0 1 3 0 0" p:path="/3D/Objects/object_1.model" objectid="1"/>'
            in after[project.MODEL_ENTRY]
        )
