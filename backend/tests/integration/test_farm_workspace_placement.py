"""Printfarm (fork): instances placed anew in a workspace copy (OrcaSlicer's Move / Rotate / Scale).

Only the placed build items' ``transform`` changes in ``3D/3dmodel.model``; every other byte of it, and
every other entry, stays as it was; the original is untouched.
"""

from __future__ import annotations

import math

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import placement, project
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_workspace import add_file, entries, make_project, open_ws, save

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


class TestSaveCopy:
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
