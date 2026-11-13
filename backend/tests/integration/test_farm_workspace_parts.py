"""Printfarm (fork): a part deleted or its type changed in a workspace copy (OrcaSlicer's Delete of a part, Change
Type and Precise Seam Type).

The part's component and its block in model_settings.config change and nothing else; the last solid part stays;
a type change sorts the object's parts as Orca's sort_volumes does; one part left takes the object's place.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from backend.app.api.routes import library
from backend.app.farm.workspace import parts, placement, project
from backend.app.farm.workspace.parts import InvalidPartOp, PartOp
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

ORCA, BAMBU = "orcaslicer", "bambu_studio"
MODEL = (
    b'<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
    b'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06">\n <resources>\n'
    b'  <object id="5" type="model">\n   <components>\n'
    b'    <component p:path="/3D/Objects/object_1.model" objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>\n'
    b'    <component p:path="/3D/Objects/object_1.model" objectid="3" transform="0 1 0 -1 0 0 0 0 1 10 0 0"/>\n'
    b'    <component p:path="/3D/Objects/object_1.model" objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 2"/>\n'
    b"   </components>\n  </object>\n </resources>\n"
    b' <build>\n  <item objectid="5" transform="1 0 0 0 1 0 0 0 1 100 50 5" printable="1"/>\n'
    b'  <item objectid="5" transform="1 0 0 0 1 0 0 0 1 150 50 5" printable="1"/>\n </build>\n</model>\n'
)
SETTINGS = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n<config>\n  <object id="5">\n'
    b'    <metadata key="name" value="Bracket"/>\n    <metadata key="wall_loops" value="4"/>\n'
    b'    <part id="1" subtype="normal_part">\n      <metadata key="name" value="Body"/>\n'
    b'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n    </part>\n'
    b'    <part id="3" subtype="normal_part">\n      <metadata key="name" value="Peg"/>\n'
    b'      <metadata key="matrix" value="0 -1 0 10 1 0 0 0 0 0 1 0 0 0 0 1"/>\n'
    b'      <metadata key="wall_loops" value="5"/>\n    </part>\n'
    b'    <part id="2" subtype="modifier_part">\n      <metadata key="name" value="Box"/>\n'
    b'      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 2 0 0 0 1"/>\n'
    b'      <metadata key="sparse_infill_density" value="80%"/>\n    </part>\n'
    b'  </object>\n  <assemble>\n   <assemble_item object_id="5" instance_id="0" transform="1 0 0 0 1 0 0 0 1 9 9 9"/>\n'
    b"  </assemble>\n</config>\n"
)
CFG = {"wall_loops": "3", "sparse_infill_density": "15%", "filament_colour": ["#FFFFFF", "#000000"]}


def run(*ops: dict, slicer: str = ORCA, model: bytes = MODEL, settings: bytes = SETTINGS, cut=None):
    return parts.apply(model, settings, [PartOp(**o) for o in ops], CFG, slicer, cut)


def components(model: bytes) -> list[str]:
    return [c.split(b'objectid="')[1].split(b'"')[0].decode() for c in model.split(b"<component ")[1:]]


def part_ids(settings: bytes) -> list[str]:
    return [p.split(b'"')[1].decode() for p in settings.split(b"<part id=")[1:]]


def with_entries(data: bytes, replaced: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as zin, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            zout.writestr(info, replaced.get(info.filename, zin.read(info)))
    return out.getvalue()


class TestChangeType:
    def test_a_part_becomes_negative_and_the_parts_are_sorted_as_orca_does(self):
        model, settings, applied = run({"op": "type", "object_id": "5", "part_id": "1", "type": "negative_part"})
        # sort_volumes(true): the model part, then the negative part, then the modifier; each line moved whole
        assert components(model) == ["3", "1", "2"] and part_ids(settings) == ["3", "1", "2"]
        assert sorted(model.splitlines()) == sorted(MODEL.splitlines())
        assert b'<part id="1" subtype="negative_part">' in settings
        retyped = settings.replace(b'"negative_part"', b'"normal_part"', 1)
        assert sorted(retyped.splitlines()) == sorted(SETTINGS.splitlines())
        assert applied == [
            {
                "key": "part",
                "op": "type",
                "object_id": "5",
                "part_id": "1",
                "before": "normal_part",
                "after": "negative_part",
            }
        ]

    def test_a_precise_seam_is_a_modifier_with_its_type_and_its_settings_dormant(self):
        _, seam, _ = run({"op": "type", "object_id": "5", "part_id": "2", "type": "precise_seam_left"})
        assert (
            b'    <part id="2" subtype="modifier_part">\n'
            b'      <metadata key="precise_seam_type" value="precise_seam_left"/>\n'
            b'      <metadata key="name" value="Box"/>'
        ) in seam
        assert b'<metadata key="precise_seam_config:sparse_infill_density" value="80%"/>' in seam
        # its subtype picked again (Precise Seam Type), then a modifier again: the settings are its own once more
        _, other, _ = run(
            {"op": "type", "object_id": "5", "part_id": "2", "type": "precise_seam_blocked"}, settings=seam
        )
        assert b'value="precise_seam_blocked"' in other and b"precise_seam_config:sparse_infill_density" in other
        _, back, applied = run(
            {"op": "type", "object_id": "5", "part_id": "2", "type": "modifier_part"}, settings=other
        )
        assert back == SETTINGS
        assert applied[0]["before"] == "precise_seam_blocked"

    def test_the_document_reads_a_seam_as_its_type(self):
        _, seam, _ = run({"op": "type", "object_id": "5", "part_id": "2", "type": "precise_seam_center"})
        zf = zipfile.ZipFile(io.BytesIO(with_entries(make_project("orca"), {project.MODEL_SETTINGS_ENTRY: seam})))
        kinds = [p["type"] for p in project.read_structure(zf, project_settings())["objects"][0]["parts"]]
        assert kinds == ["normal_part", "normal_part", "precise_seam_center"]

    @pytest.mark.parametrize(
        ("ops", "slicer", "error"),
        [
            ([("1", "modifier_part"), ("3", "support_blocker")], ORCA, "last solid object part cannot be changed"),
            ([("2", "precise_seam_center")], BAMBU, "OrcaSlicer's"),
            ([("9", "normal_part")], ORCA, "no part 9"),
        ],
    )
    def test_refuses(self, ops, slicer, error):
        with pytest.raises(InvalidPartOp, match=error):
            run(*[{"op": "type", "object_id": "5", "part_id": p, "type": t} for p, t in ops], slicer=slicer)

    def test_a_text_part_is_no_support(self):
        text = SETTINGS.replace(
            b'<metadata key="name" value="Box"/>', b'<metadata key="name" value="Box"/><slic3rpe:text text="A"/>'
        )
        with pytest.raises(InvalidPartOp, match="text or SVG"):
            run({"op": "type", "object_id": "5", "part_id": "2", "type": "support_enforcer"}, settings=text)

    def test_the_same_type_changes_nothing(self):
        assert run({"op": "type", "object_id": "5", "part_id": "2", "type": "modifier_part"}) == (MODEL, SETTINGS, [])


class TestDelete:
    def test_a_part_goes_and_everything_else_stays(self):
        model, settings, applied = run({"op": "delete", "object_id": "5", "part_id": "2"})
        assert model == MODEL.replace(
            b'    <component p:path="/3D/Objects/object_1.model" objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 2"/>\n',
            b"",
        )
        assert part_ids(settings) == ["1", "3"] and b'value="Box"' not in settings
        assert applied == [
            {"key": "part", "op": "delete", "object_id": "5", "part_id": "2", "before": "modifier_part", "after": None}
        ]

    def test_the_last_part_takes_the_objects_place(self):
        model, settings, _ = run(
            {"op": "delete", "object_id": "5", "part_id": "2"}, {"op": "delete", "object_id": "5", "part_id": "1"}
        )
        # delete_volume: the part's transform (90 deg about Z, 10 mm along X) goes into every instance
        assert components(model) == ["3"]
        assert b'objectid="3" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>' in model
        items = [i["transform"] for i in placement._build_items(model)]
        assert items == ["0 1 0 -1 0 0 0 0 1 110 50 5", "0 1 0 -1 0 0 0 0 1 160 50 5"]
        # its matrix without that transform, its settings the object's (wall_loops 5 over the object's 4)
        assert b'<metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>' in settings
        obj = settings[: settings.index(b"<part ")]
        assert b'<metadata key="wall_loops" value="5"/>' in obj and b'value="4"' not in obj
        assert settings.count(b"wall_loops") == 1
        assert b'<assemble_item object_id="5" instance_id="0" transform="1 0 0 0 1 0 0 0 1 9 9 9"/>' in settings

    @pytest.mark.parametrize(
        ("kwargs", "error"),
        [
            ({}, "deleting the last solid part is not allowed"),
            ({"cut": {"5"}}, "is cut"),
            ({"model": MODEL.replace(b"<component ", b"<componentx ")}, "not components"),
        ],
    )
    def test_refuses(self, kwargs, error):
        ops = [{"op": "delete", "object_id": "5", "part_id": "1"}, {"op": "delete", "object_id": "5", "part_id": "3"}]
        with pytest.raises(InvalidPartOp, match=error):
            run(*(ops if not kwargs else ops[:1]), **kwargs)

    def test_cut_objects_are_named_by_their_place(self):
        xml = (
            b'<?xml version="1.0"?><objects><object id="1"><cut_id id="0" check_sum="1" connectors_cnt="0"/></object>'
            b'<object id="2"><cut_id id="9" check_sum="1" connectors_cnt="0"/></object></objects>'
        )
        # Bambu Studio writes id 0 for an object never cut
        assert parts.cut_objects(xml, ["7", "5"]) == {"5"}


@pytest.mark.integration
class TestSaveCopy:
    @staticmethod
    def project() -> bytes:
        # make_project's object 2 made of three parts (one mesh named three times: the slicer isn't asked here)
        data = make_project("orca", project_settings())
        model = entries(data)[project.MODEL_ENTRY].replace(
            b'<component p:path="/3D/Objects/object_1.model" objectid="1"/></components></object><object id="4"',
            b'<component p:path="/3D/Objects/object_1.model" objectid="1"/>'
            b'<component p:path="/3D/Objects/object_1.model" objectid="6" transform="1 0 0 0 1 0 0 0 1 0 0 2"/>'
            b'<component p:path="/3D/Objects/object_1.model" objectid="7"/></components></object><object id="4"',
        )
        settings = entries(data)[project.MODEL_SETTINGS_ENTRY].replace(
            b'<part id="1" subtype="normal_part"><metadata key="name" value="Cube A"/></part>',
            b'<part id="1" subtype="normal_part"><metadata key="name" value="Cube A"/></part>'
            b'<part id="6" subtype="modifier_part"><metadata key="name" value="Box"/></part>'
            b'<part id="7" subtype="normal_part"><metadata key="name" value="Peg"/></part>',
        )
        return with_entries(data, {project.MODEL_ENTRY: model, project.MODEL_SETTINGS_ENTRY: settings})

    async def test_a_copy_with_a_part_deleted_and_one_retyped(self, async_client, db_session):
        original = self.project()
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        ops = [
            {"op": "delete", "object_id": "2", "part_id": "6"},
            {"op": "type", "object_id": "2", "part_id": "1", "type": "negative_part"},
        ]
        resp = await save(async_client, doc, {}, part_ops=ops)
        assert resp.status_code == 200, resp.text
        assert [(c["op"], c["part_id"]) for c in resp.json()["changes"]] == [("delete", "6"), ("type", "1")]
        row = await db_session.get(LibraryFile, resp.json()["library_file_id"])
        before, after = entries(original), entries(library.to_absolute_path(row.file_path).read_bytes())
        assert sorted(n for n in before if before[n] != after[n]) == [project.MODEL_ENTRY, project.MODEL_SETTINGS_ENTRY]
        copy = await open_ws(async_client, row.id)
        cube = next(o for o in copy["objects"] if o["id"] == "2")
        assert [(p["id"], p["type"]) for p in cube["parts"]] == [("7", "normal_part"), ("1", "negative_part")]

    async def test_refused_with_a_duplicate_or_an_arrange_or_the_last_part(self, async_client, db_session):
        f = await add_file(db_session, self.project())
        doc = await open_ws(async_client, f.id)
        ops = [{"op": "delete", "object_id": "2", "part_id": "6"}]
        resp = await save(async_client, doc, {}, part_ops=ops, plate_structure=[{"op": "duplicate", "plate": 1}])
        assert resp.status_code == 422 and "then duplicate" in resp.json()["detail"]
        resp = await async_client.post(f"{API}/{f.id}/arrange", json={"base_sha256": doc["sha256"], "part_ops": ops})
        assert resp.status_code == 422 and "then arrange" in resp.json()["detail"]
        resp = await save(async_client, doc, {}, part_ops=[{"op": "delete", "object_id": "4", "part_id": "1"}])
        assert resp.status_code == 422 and "last solid part" in resp.json()["detail"]
