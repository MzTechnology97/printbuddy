"""3MF Workspace (fork): reading a project, editing its settings, saving a copy, slicing the copy.

Every 3MF here is generated: two cubes on two plates, laid out the way OrcaSlicer and Bambu Studio
save a project (components in ``3D/Objects``, plates and per-object settings in
``model_settings.config``, a full ``project_settings.config``). No file comes from a real farm.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select

from backend.app.api.routes import library
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.models import FarmPlate
from backend.app.farm.workspace import project, settings as ws
from backend.app.models.library import LibraryFile

API = "/api/v1/farm/workspace/files"
HEADERS = {
    "orca": {"Application": "BambuStudio-02.06.00.51", "OrcaSlicer": "2.3.1"},
    "bambu": {"Application": "BambuStudio-02.03.01.51"},
}
NS = 'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"'
PROD_NS = 'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06"'


def _png() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (8, 8), "#808080").save(out, "PNG")
    return out.getvalue()


PNG = _png()  # a real plate picture: the Klipper G-code embeds it
CUBE_FACES = (
    (0, 6, 4),
    (1, 5, 7),
    (1, 7, 3),
    (0, 4, 5),
    (0, 5, 1),
    (2, 3, 7),
    (2, 7, 6),
    (0, 1, 3),
    (0, 3, 2),
    (4, 6, 7),
    (4, 7, 5),
)
CUBE = (
    f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" {NS}><resources><object id="1" type="model">'
    "<mesh><vertices>"
    + "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x in (0, 10) for y in (0, 10) for z in (0, 10))
    + '</vertices><triangles><triangle v1="0" v2="2" v3="6" paint_supports="4"/>'  # painted support: kept as is
    + "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in CUBE_FACES)
    + "</triangles></mesh></object></resources><build/></model>"
)


def project_settings(**extra) -> dict:
    cfg = {
        "printer_settings_id": "Test Printer 0.4 nozzle",
        "print_settings_id": "0.20mm Standard @Test",
        "filament_settings_id": ["Test PLA", "Test PETG"],
        "inherits_group": ["0.20mm Standard @Test", "", "", ""],
        "different_settings_to_system": ["wall_loops", "", "nozzle_temperature", "retraction_length"],
        "printer_model": "Bambu Lab P1S",
        "nozzle_diameter": ["0.4"],
        "layer_height": "0.2",
        "initial_layer_print_height": "0.2",  # the key real projects save (not "initial_layer_height")
        "wall_loops": "3",
        "sparse_infill_density": "15%",
        "sparse_infill_pattern": "grid",
        "enable_support": "0",
        "support_type": "normal(auto)",
        "outer_wall_speed": "200",
        "filament_type": ["PLA", "PETG"],
        "filament_colour": ["#FFFFFF", "#000000"],
        "nozzle_temperature": ["220", "240"],
        "filament_retraction_length": ["nil", "nil"],
        "retraction_length": ["0.8"],
        "machine_start_gcode": "G28 ; home",
        "vendor_private_key": "kept as it is",  # a key the slicer doesn't define
    }
    cfg.update(extra)
    return cfg


MODEL_SETTINGS = """<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2">
    <metadata key="name" value="Cube A"/>
    <metadata key="extruder" value="1"/>
    <part id="1" subtype="normal_part"><metadata key="name" value="Cube A"/></part>
  </object>
  <object id="4">
    <metadata key="name" value="Cube B"/>
    <metadata key="extruder" value="2"/>
    <metadata key="wall_loops" value="5"/>
    <part id="1" subtype="normal_part"><metadata key="name" value="Cube B"/><metadata key="sparse_infill_density" value="40%"/></part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/><metadata key="plater_name" value="First"/>
    <model_instance><metadata key="object_id" value="2"/><metadata key="instance_id" value="0"/></model_instance>
  </plate>
  <plate>
    <metadata key="plater_id" value="2"/><metadata key="plater_name" value=""/>
    <metadata key="print_sequence" value="by object"/>
    <model_instance><metadata key="object_id" value="4"/><metadata key="instance_id" value="0"/></model_instance>
  </plate>
</config>
"""


def make_project(
    slicer: str = "orca",
    settings: dict | None = None,
    *,
    with_settings: bool = True,
    model_settings: str = MODEL_SETTINGS,
    extra: dict[str, bytes] | None = None,
) -> bytes:
    meta = "".join(f'<metadata name="{k}">{v}</metadata>' for k, v in HEADERS.get(slicer, {}).items())
    main = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<model unit="millimeter" {NS} {PROD_NS}>{meta}<resources>'
        '<object id="2" type="model"><components><component p:path="/3D/Objects/object_1.model" objectid="1"/>'
        "</components></object>"
        '<object id="4" type="model"><components><component p:path="/3D/Objects/object_2.model" objectid="1"/>'
        "</components></object></resources>"
        '<build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 50 50 5" printable="1"/>'
        '<item objectid="4" transform="1 0 0 0 1 0 0 0 1 300 50 5" printable="1"/></build></model>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships/>')
        zf.writestr("3D/3dmodel.model", main)
        zf.writestr("3D/Objects/object_1.model", CUBE)
        zf.writestr("3D/Objects/object_2.model", CUBE.replace('"10"', '"20"'))
        if with_settings:
            zf.writestr(project.PROJECT_SETTINGS_ENTRY, json.dumps(settings or project_settings(), indent=4))
        if model_settings:
            zf.writestr(project.MODEL_SETTINGS_ENTRY, model_settings)
        zf.writestr("Metadata/slice_info.config", '<?xml version="1.0"?><config><header/></config>')
        zf.writestr("Metadata/plate_1.png", PNG)
        zf.writestr("Metadata/plate_2.png", PNG)
        zf.writestr("Metadata/custom_vendor_data.xml", "<unknown>kept</unknown>")
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()


async def add_file(db, data: bytes, filename: str = "cubes.3mf") -> LibraryFile:
    path = library.get_library_files_dir() / f"ws-{hashlib.sha256(data).hexdigest()[:12]}-{filename}"
    path.write_bytes(data)
    row = LibraryFile(
        filename=filename,
        file_path=library.to_relative_path(path),
        file_type="3mf",
        file_size=len(data),
        file_hash=hashlib.sha256(data).hexdigest(),
    )
    db.add(row)
    await db.commit()
    return row


def has(entry: dict, **expected) -> bool:
    return {k: entry.get(k) for k in expected} == expected


def settings_by_key(doc: dict) -> dict[str, dict]:
    return {s["key"]: s for s in doc["settings"]}


async def open_ws(client, file_id: int) -> dict:
    resp = await client.get(f"{API}/{file_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def save(client, doc: dict, changes: dict, **body):
    return await client.post(
        f"{API}/{doc['file']['id']}/copy", json={"base_sha256": doc["sha256"], "changes": changes, **body}
    )


def entries(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


async def library_count(db) -> int:
    return await db.scalar(select(func.count()).select_from(LibraryFile))


# ---------------------------------------------------------------- reading


@pytest.mark.integration
class TestOpen:
    @pytest.mark.parametrize(("slicer", "expected"), [("orca", "orcaslicer"), ("bambu", "bambu_studio")])
    async def test_opens_projects_of_both_slicers(self, async_client, db_session, slicer, expected):
        data = make_project(slicer)
        f = await add_file(db_session, data)
        doc = await open_ws(async_client, f.id)
        assert (doc["kind"], doc["slicer"], doc["editable"]) == ("project", expected, True)
        assert doc["sha256"] == hashlib.sha256(data).hexdigest()
        assert [(p["index"], p["name"], p["object_ids"]) for p in doc["plates"]] == [
            (1, "First", ["2"]),
            (2, None, ["4"]),
        ]
        assert [(o["name"], o["extruder"]) for o in doc["objects"]] == [("Cube A", "1"), ("Cube B", "2")]
        assert doc["plates"][1]["instances"] == [{"object_id": "4", "instance_id": "0"}]
        assert doc["plates"][1]["metadata"]["print_sequence"] == "by object"  # the plate's own setting
        assert "print_sequence" not in doc["plates"][0]["metadata"]

    async def test_settings_carry_the_slicers_types_and_state(self, async_client, db_session):
        f = await add_file(db_session, make_project())
        s = settings_by_key(await open_ws(async_client, f.id))
        # Types, sections and ranges are OrcaSlicer's own (PrintConfig.cpp, Preset.cpp)
        assert has(s["layer_height"], section="process", type="float", value="0.2", editable=True)
        assert has(s["initial_layer_print_height"], section="process", type="float", min=0, editable=True)
        assert has(s["wall_loops"], type="int", differs_from_system=True)
        assert s["layer_height"]["differs_from_system"] is False  # same as the system preset
        assert has(s["sparse_infill_density"], type="float", percent="always", max=100)
        assert has(s["support_type"], type="enum") and "tree(auto)" in [
            o["value"] for o in s["support_type"]["options"]
        ]
        assert has(s["nozzle_temperature"], section="filament", value=["220", "240"], editable=True)
        assert s["nozzle_temperature"]["differs_from_system"] is True
        assert has(s["retraction_length"], section="printer", editable=True)
        # one value where OrcaSlicer 2.5 keeps one per extruder variant: saved before, still editable
        assert has(s["outer_wall_speed"], value="200", editable=True)
        # text, G-code, material and printer settings change too, as in the client
        assert has(s["machine_start_gcode"], type="string", multiline=True, editable=True)
        assert has(s["filament_type"], editable=True)
        # the printer's hardware: changed with the printer profile, never in a copy queued for that printer
        assert has(s["nozzle_diameter"], editable=False, readonly_reason="hardware")
        # The object that slices with its own value says so, by object and part.
        assert [(o["object"], o["part"], o["value"]) for o in s["wall_loops"]["overrides"]] == [("Cube B", None, "5")]
        assert [(o["object"], o["part"]) for o in s["sparse_infill_density"]["overrides"]] == [("Cube B", "Cube B")]
        # Read only: identity, inherit-only values, keys the slicer doesn't define.
        assert s["printer_settings_id"]["readonly_reason"] == "identity"
        assert s["printer_model"]["readonly_reason"] == "identity"
        assert s["inherits_group"]["readonly_reason"] == "identity"
        assert s["different_settings_to_system"]["readonly_reason"] == "identity"
        assert s["filament_retraction_length"]["readonly_reason"] == "format"
        assert has(s["vendor_private_key"], section=None, editable=False, readonly_reason="unknown")

    async def test_sliced_file_is_read_only(self, async_client, db_session):
        f = await add_file(db_session, make_project(extra={"Metadata/plate_1.gcode": b"G28\n"}), "cubes.gcode.3mf")
        doc = await open_ws(async_client, f.id)
        assert (doc["kind"], doc["editable"]) == ("sliced", False)
        assert (await save(async_client, doc, {"layer_height": "0.1"})).status_code == 400
        assert (await async_client.get(f"/api/v1/farm/library/files/{f.id}/project")).status_code == 400

    async def test_project_without_settings_is_read_only(self, async_client, db_session):
        f = await add_file(db_session, make_project("cad", with_settings=False, model_settings=""))
        doc = await open_ws(async_client, f.id)
        assert (doc["kind"], doc["editable"], doc["settings"]) == ("model", False, [])
        assert (await save(async_client, doc, {"layer_height": "0.1"})).status_code == 400

    async def test_corrupt_files(self, async_client, db_session):
        f = await add_file(db_session, b"PK\x03\x04 truncated", "broken.3mf")
        resp = await async_client.get(f"{API}/{f.id}")
        assert resp.status_code == 422 and "not a valid 3MF" in resp.json()["detail"]

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
            zf.writestr(project.PROJECT_SETTINGS_ENTRY, "{not json")
        f = await add_file(db_session, buf.getvalue(), "badjson.3mf")
        resp = await async_client.get(f"{API}/{f.id}")
        assert resp.status_code == 422 and "not valid JSON" in resp.json()["detail"]

        f = await add_file(db_session, make_project(model_settings="<config><object"), "badxml.3mf")
        doc = await open_ws(async_client, f.id)
        assert doc["editable"] is False and "not valid XML" in doc["warnings"][0]
        assert (await save(async_client, doc, {"layer_height": "0.1"})).status_code == 400

    async def test_not_a_3mf_and_missing(self, async_client, db_session):
        assert (await async_client.get(f"{API}/999999")).status_code == 404
        f = await add_file(db_session, b"G28", "part.gcode")
        assert (await async_client.get(f"{API}/{f.id}")).status_code == 400


class TestArchiveSafety:
    def _zip(self, name: str, data: bytes = b"x", compress=zipfile.ZIP_STORED) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compress) as zf:
            zf.writestr("3D/3dmodel.model", "<model/>")
            zf.writestr(name, data)
        return buf.getvalue()

    @pytest.mark.parametrize("name", ["../evil.txt", "/etc/passwd", "C:/x.txt", "a\\..\\b", "Metadata/../../x"])
    def test_zip_slip_names_are_refused(self, name):
        with pytest.raises(project.ArchiveError, match="unsafe path"):
            project.open_archive(self._zip(name))

    def test_zip_bomb_is_refused(self):
        with pytest.raises(project.ArchiveError, match="inflates"):
            project.open_archive(self._zip("Metadata/bomb.bin", b"\0" * (64 * 1024 * 1024), zipfile.ZIP_DEFLATED))

    def test_too_large_and_missing_model(self, monkeypatch):
        with monkeypatch.context() as m:
            m.setattr(project, "MAX_ARCHIVE_BYTES", 100)
            with pytest.raises(project.ArchiveError, match="larger than"):
                project.open_archive(make_project())
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("Metadata/x", "1")
        with pytest.raises(project.ArchiveError, match="missing 3D/3dmodel.model"):
            project.open_archive(buf.getvalue())


# ---------------------------------------------------------------- changes


class TestChanges:
    def apply(self, changes: dict, cfg: dict | None = None):
        return ws.apply_changes(cfg or project_settings(), changes)

    def test_number_bool_enum_and_percent_keep_the_project_format(self):
        changes = {
            "layer_height": 0.16,
            "wall_loops": "4",
            "enable_support": True,
            "support_type": "tree(auto)",
            "sparse_infill_density": "25",
            "nozzle_temperature": ["225", 240],
        }
        cfg, applied = self.apply(changes)
        assert (cfg["layer_height"], cfg["wall_loops"], cfg["enable_support"]) == ("0.16", "4", "1")
        assert (cfg["support_type"], cfg["sparse_infill_density"]) == ("tree(auto)", "25%")
        assert cfg["nozzle_temperature"] == ["225", "240"]
        assert [a["key"] for a in applied] == list(changes)
        # Each change is listed against the system preset of its section: process, filament 1, printer.
        assert cfg["different_settings_to_system"] == [
            "wall_loops;layer_height;enable_support;support_type;sparse_infill_density",
            "nozzle_temperature",
            "nozzle_temperature",
            "retraction_length",
        ]
        untouched = set(project_settings()) - set(changes) - {"different_settings_to_system"}
        assert {k: cfg[k] for k in untouched} == {k: project_settings()[k] for k in untouched}

    def test_true_false_booleans_stay_so(self):
        cfg, _ = self.apply({"enable_support": "1"}, project_settings(enable_support="false"))
        assert cfg["enable_support"] == "true"

    def test_the_original_value_is_no_change(self):
        cfg, applied = self.apply({"layer_height": "0.2", "wall_loops": 3, "nozzle_temperature": ["220", "240"]})
        assert applied == [] and cfg == project_settings()

    @pytest.mark.parametrize(
        ("changes", "error"),
        [
            ({"sparse_infill_density": "120"}, "above the maximum"),
            ({"layer_height": "abc"}, "not a number"),
            ({"layer_height": "nan"}, "not a number"),
            ({"initial_layer_print_height": "-0.1"}, "below the minimum"),
            ({"wall_loops": "2.5"}, "whole number"),
            ({"wall_loops": "-1"}, "below the minimum"),
            ({"enable_support": "maybe"}, "yes/no"),
            ({"support_type": "rocket"}, "options"),
            ({"outer_wall_speed": "50%"}, "not a percentage"),
            ({"nozzle_temperature": ["220"]}, "expected 2 value"),
            ({"nozzle_temperature": "220"}, "expected 2 value"),
            ({"printer_settings_id": "Other printer"}, "read only (identity)"),
            ({"inherits_group": ["", "", "", ""]}, "read only (identity)"),
            ({"vendor_private_key": "x"}, "read only (unknown)"),
            ({"filament_type": ["PLA\x00", "PETG"]}, "control characters"),
            ({"filament_type": ["PLA\nX", "PETG"]}, "control characters"),  # one line, not G-code
            ({"machine_start_gcode": "G28\n" * 200_000}, "longer than"),
            ({"not_in_project": "1"}, "not in this project"),
            ({"layer_height": "nil"}, "inherited value"),
        ],
    )
    def test_invalid_values_are_refused(self, changes, error):
        with pytest.raises(ws.InvalidChange) as info:
            self.apply(changes)
        assert error in next(iter(info.value.errors.values()))

    def test_initial_layer_print_height_round_trip(self):
        """The key real projects save: editable with OrcaSlicer's range, and saved."""
        (described,) = [d for d in ws.describe(project_settings(), {}) if d["key"] == "initial_layer_print_height"]
        assert (described["editable"], described["min"], described["type"]) == (True, 0, "float")
        cfg, applied = self.apply({"initial_layer_print_height": "0.24"})
        assert cfg["initial_layer_print_height"] == "0.24"
        assert applied == [{"key": "initial_layer_print_height", "before": "0.2", "after": "0.24"}]
        assert "initial_layer_print_height" in cfg["different_settings_to_system"][0].split(";")

    def test_per_extruder_and_per_filament_lists(self):
        cfg, _ = self.apply({"retraction_length": [1.2], "nozzle_temperature": ["220", "250"]})
        assert cfg["retraction_length"] == ["1.2"]
        assert cfg["nozzle_temperature"] == ["220", "250"]
        # only the second filament changed: only its section of different_settings_to_system lists it
        assert cfg["different_settings_to_system"][1:3] == ["", "nozzle_temperature"]

    def test_gcode_and_text_are_saved_as_typed(self):
        gcode = "G28 ; home\nG1 Z5 F600\n  M117 {layer_num}\t; keep spacing\n"
        cfg, _ = self.apply({"machine_start_gcode": gcode, "filament_type": ["PLA-CF", "PETG"]})
        assert cfg["machine_start_gcode"] == gcode
        assert cfg["filament_type"] == ["PLA-CF", "PETG"]  # an open list: any material name

    def test_printer_hardware_stays_the_printer_profiles(self):
        cfg = project_settings(
            printable_area=["0x0", "256x0", "256x256", "0x256"], gcode_flavor="klipper", printer_notes=""
        )
        for key, value in [
            ("nozzle_diameter", ["0.6"]),
            ("printable_area", ["0x0", "300x0", "300x300", "0x300"]),
            ("gcode_flavor", "marlin2"),
            ("printer_notes", "BBL"),
        ]:
            with pytest.raises(ws.InvalidChange) as info:
                self.apply({key: value}, cfg)
            assert info.value.errors[key] == "read only (hardware)"
        # what the printer does stays editable, as in the client
        changed, _ = self.apply({"machine_start_gcode": "G28 ; home\nG29", "retraction_length": ["1"]}, cfg)
        assert changed["machine_start_gcode"] == "G28 ; home\nG29"

    def test_text_keeps_the_slicers_line_breaks_and_a_save_has_a_size_limit(self):
        cfg, _ = self.apply({"machine_start_gcode": "G28\r\nG1 Z5\r\n"})
        assert cfg["machine_start_gcode"] == "G28\nG1 Z5\n"
        with pytest.raises(ws.InvalidChange) as info:
            self.apply(
                {"machine_start_gcode": "G28 ;" + "x" * (200 * 1024), "filament_type": ["x" * (1024 * 1024)] * 2}
            )
        assert "MB in total" in info.value.errors["changes"]

    def test_numbers_are_written_without_exponent(self):
        cfg, _ = self.apply({"layer_height": "1e-05", "retraction_length": ["0.00002"]})
        assert (cfg["layer_height"], cfg["retraction_length"]) == ("0.00001", ["0.00002"])

    def test_bambu_projects_use_bambu_studios_options(self):
        bambu = ws.options("bambu_studio")
        assert ws.options("orcaslicer") is not bambu and "counter_coef_1" in bambu  # a Bambu Studio only key
        cfg, _ = ws.apply_changes(project_settings(), {"layer_height": "0.12"}, "bambu_studio")
        assert cfg["layer_height"] == "0.12"


# ---------------------------------------------------------------- the slicers' own option data

FIXTURES = Path(__file__).resolve().parents[1] / "_fixtures" / "slicer_projects"
# keys real projects hold that today's slicer no longer defines (renamed or removed since): shown read only
LEGACY_KEYS = {
    "bambu_studio_02.00_auto_pa_line_single.json": {
        "deretract_speed_extruder_change",
        "layer_time_smoothing",
        "layer_time_smoothing_threshold",
        "nozzle_type",
        "reduce_infill_retraction",
        "from",
        "name",
        "version",
    },
    "orcaslicer_repo_pa_pattern.json": {
        "bed_temperature_difference",
        "chamber_temp_control",
        "chamber_temperatures",
        "end_print_exhaust_fan_speed",
        "end_print_exhaust_fan_time",
        "extruder_clearance_max_radius",
        "initial_layer_flow_ratio",
        "internal_bridge_support_thickness",
        "silent_mode",
        "solid_infill_filament",
        "sparse_infill_anchor",
        "sparse_infill_anchor_max",
        "sparse_infill_filament",
        "top_one_wall_type",
        "wall_filament",
        "wall_infill_order",
        "from",
        "name",
        "version",
    },
}


class TestSlicerOptions:
    """Every key of a real project is either an option of its slicer (editable, or read only for a stated
    reason) or a known legacy key shown read only under "Other values"."""

    @pytest.mark.parametrize(
        ("fixture", "slicer"),
        [
            ("bambu_studio_02.00_auto_pa_line_single.json", "bambu_studio"),  # BambuStudio repo sample
            ("orcaslicer_repo_pa_pattern.json", "orcaslicer"),  # OrcaSlicer repo sample (older project)
        ],
    )
    def test_every_key_of_a_real_project_has_a_home(self, fixture, slicer):
        cfg = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
        described = ws.describe(cfg, {}, slicer)
        assert {d["key"] for d in described if d["readonly_reason"] == "unknown"} == LEGACY_KEYS[fixture]
        reasons = {d["readonly_reason"] for d in described}
        assert reasons <= {None, "identity", "hardware", "slicer", "project", "format", "unknown"}
        editable = [d for d in described if d["editable"]]
        assert len(editable) > 0.8 * len(described) - len(LEGACY_KEYS[fixture])
        # an editable value can be saved back as it is (the check accepts what the slicer wrote)
        for d in editable:
            assert ws.apply_changes(cfg, {d["key"]: d["value"]}, slicer)[1] == [], d["key"]

    def test_the_data_is_the_slicers(self):
        for slicer in ws.SLICERS:
            data = json.loads((ws.DATA / f"{slicer}.json").read_text(encoding="utf-8"))
            assert data["slicer"] == slicer and data["license"] == "AGPL-3.0" and len(data["commit"]) == 40
            assert len(data["options"]) > 700
        orca = ws.options("orcaslicer")
        assert orca["machine_max_speed_x"]["section"] == "printer"  # made in a loop in PrintConfig.cpp
        assert orca["bottom_surface_pattern"]["enum"] == orca["top_surface_pattern"]["enum"]  # shared list
        assert orca["filament_retraction_length"]["nullable"] is True
        assert orca["enable_prime_tower"]["type"] == "coBool"  # not the old schema's "prime_tower_enable"
        assert orca["fuzzy_skin_point_distance"]["section"] == "process"


# ---------------------------------------------------------------- saving a copy


@pytest.mark.integration
class TestSaveCopy:
    async def test_copy_has_the_changes_and_everything_else_unchanged(self, async_client, db_session):
        original = make_project("bambu")
        f = await add_file(db_session, original)
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {"layer_height": "0.12", "enable_support": "1"})
        assert resp.status_code == 200, resp.text
        out = resp.json()
        assert out["filename"].startswith("cubes (edited ") and out["filename"].endswith(").3mf")
        assert [c["key"] for c in out["changes"]] == ["layer_height", "enable_support"]

        copy_row = await db_session.get(LibraryFile, out["library_file_id"])
        assert copy_row.source_type == "3mf_workspace" and copy_row.folder_id is None
        assert copy_row.file_metadata["workspace"]["copy_of_library_file_id"] == f.id
        assert copy_row.file_metadata["workspace"]["source_sha256"] == doc["sha256"]
        copy = library.to_absolute_path(copy_row.file_path).read_bytes()
        before, after = entries(original), entries(copy)
        assert list(before) == list(after)  # same entries, same order
        for name in before:  # geometry, paint, plates, filament assignment, thumbnails, unknown metadata
            if name != project.PROJECT_SETTINGS_ENTRY:
                assert before[name] == after[name], name
        cfg = json.loads(after[project.PROJECT_SETTINGS_ENTRY])
        assert (cfg["layer_height"], cfg["enable_support"]) == ("0.12", "1")
        assert cfg["filament_colour"] == ["#FFFFFF", "#000000"] and cfg["vendor_private_key"] == "kept as it is"
        # The original is untouched, on disk and in the library.
        assert library.to_absolute_path(f.file_path).read_bytes() == original
        await db_session.refresh(f)
        assert f.file_hash == hashlib.sha256(original).hexdigest()

        # Reopened, the copy shows the changes as its own values, and where it came from.
        again = await open_ws(async_client, copy_row.id)
        s = settings_by_key(again)
        assert (s["layer_height"]["value"], s["enable_support"]["value"]) == ("0.12", "1")
        assert again["copy_of"]["copy_of_filename"] == "cubes.3mf"
        assert len(again["plates"]) == 2

    @pytest.mark.parametrize("damage", ["crc", "encrypted"])
    async def test_a_damaged_entry_is_refused_and_leaves_nothing(
        self, async_client, db_session, damage, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(library, "get_library_files_dir", lambda: tmp_path)  # its own: workers share the real one
        data = bytearray(make_project())
        name = "3D/Objects/object_1.model"  # read only when the copy is written
        with zipfile.ZipFile(io.BytesIO(bytes(data))) as zf:
            local = zf.getinfo(name).header_offset
        central = bytes(data).index(name.encode(), bytes(data).index(b"PK\x01\x02")) - 46
        if damage == "crc":
            data[local + 14] ^= 0xFF  # the data no longer matches its CRC (local and central header)
            data[central + 16] ^= 0xFF
        else:
            data[local + 6] |= 0x01  # "encrypted" flag (local and central header)
            data[central + 8] |= 0x01
        f = await add_file(db_session, bytes(data))
        files_dir = library.get_library_files_dir()
        before = sorted(p.name for p in files_dir.iterdir())
        doc = await open_ws(async_client, f.id)  # the settings read fine: the damage shows when copying
        resp = await save(async_client, doc, {"layer_height": "0.1"})
        assert resp.status_code == 422, resp.text
        assert "Cannot save a copy" in resp.json()["detail"]
        assert sorted(p.name for p in files_dir.iterdir()) == before  # no .part, no half copy
        assert await library_count(db_session) == 1

    async def test_copy_of_a_copy_keeps_the_original(self, async_client, db_session):
        f = await add_file(db_session, make_project())
        first = (await save(async_client, await open_ws(async_client, f.id), {"wall_loops": "4"})).json()
        second = await save(
            async_client, await open_ws(async_client, first["library_file_id"]), {"wall_loops": "5"}, filename="v3"
        )
        assert second.status_code == 200 and second.json()["filename"] == "v3.3mf"
        row = await db_session.get(LibraryFile, second.json()["library_file_id"])
        assert row.file_metadata["workspace"]["original_library_file_id"] == f.id

    async def test_stale_version_is_refused(self, async_client, db_session):
        f = await add_file(db_session, make_project())
        doc = await open_ws(async_client, f.id)
        library.to_absolute_path(f.file_path).write_bytes(make_project(settings=project_settings(layer_height="0.28")))
        resp = await save(async_client, doc, {"layer_height": "0.1"})
        assert resp.status_code == 409
        assert await library_count(db_session) == 1

    async def test_invalid_changes_and_names_are_refused(self, async_client, db_session):
        f = await add_file(db_session, make_project())
        doc = await open_ws(async_client, f.id)
        resp = await save(async_client, doc, {"sparse_infill_density": "150", "wall_loops": "x"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "sparse_infill_density: above the maximum" in detail and "wall_loops: not a whole number" in detail
        assert (await save(async_client, doc, {})).status_code == 400  # nothing changed
        assert (await save(async_client, doc, {"layer_height": "0.1"}, filename="../x")).status_code == 422
        assert (await save(async_client, doc, {"layer_height": "0.1"}, filename="x.gcode.3mf")).status_code == 422
        bad = await async_client.post(f"{API}/{f.id}/copy", json={"base_sha256": "short", "changes": {}})
        assert bad.status_code == 422
        assert await library_count(db_session) == 1

    async def test_permissions_follow_the_library(self, async_client, db_session):
        f = await add_file(db_session, make_project())
        await async_client.post(
            "/api/v1/auth/setup",
            json={"auth_enabled": True, "admin_username": "wsadmin", "admin_password": "AdminPass1!"},
        )

        async def login(username: str, password: str) -> dict:
            resp = await async_client.post("/api/v1/auth/login", json={"username": username, "password": password})
            return {"Authorization": f"Bearer {resp.json()['access_token']}"}

        admin = await login("wsadmin", "AdminPass1!")
        groups = (await async_client.get("/api/v1/groups/", headers=admin)).json()
        viewers = next(g for g in groups if g["name"] == "Viewers")  # library:read, not library:upload
        await async_client.post(
            "/api/v1/users/",
            headers=admin,
            json={"username": "wsviewer", "password": "Viewerpass1!", "group_ids": [viewers["id"]]},
        )
        viewer = await login("wsviewer", "Viewerpass1!")

        assert (await async_client.get(f"{API}/{f.id}")).status_code == 401
        doc = (await async_client.get(f"{API}/{f.id}", headers=viewer)).json()
        body = {"base_sha256": doc["sha256"], "changes": {"layer_height": "0.1"}}
        assert (await async_client.post(f"{API}/{f.id}/copy", json=body, headers=viewer)).status_code == 403
        assert (await async_client.post(f"{API}/{f.id}/copy", json=body, headers=admin)).status_code == 200


# ---------------------------------------------------------------- the farm leaves copies alone; slicing a copy


@pytest.mark.integration
class TestFarm:
    @pytest.fixture
    def sidecar(self, monkeypatch):
        from backend.app.services.slicer_api import SlicerApiService, SliceResult

        calls = []
        output = make_project(extra={"Metadata/plate_1.gcode": b"G28\n"})

        async def fake_slice(self, **kwargs):
            calls.append({"url": self.base_url, **kwargs})
            return SliceResult(content=output, print_time_seconds=1800, filament_used_g=7.5, filament_used_mm=2500.0)

        monkeypatch.setattr(SlicerApiService, "slice_without_profiles", fake_slice)
        return calls

    async def _slice_copy(self, async_client, db_session, plate: int) -> tuple[int, LibraryFile]:
        """Save a copy of a Bambu project with one change and slice it through the farm route."""
        from backend.app.services.slice_dispatch import slice_dispatch

        f = await add_file(db_session, make_project("bambu"))
        saved = await save(async_client, await open_ws(async_client, f.id), {"layer_height": "0.1"})
        copy_id = saved.json()["library_file_id"]
        job = await async_client.post(f"/api/v1/farm/library/files/{copy_id}/slice", json={"plate": plate})
        assert job.status_code == 202, job.text
        for _ in range(200):
            state = slice_dispatch.get(job.json()["job_id"])
            if state and state.status in ("completed", "failed"):
                break
            await asyncio.sleep(0.05)
        slice_dispatch._jobs.pop(job.json()["job_id"], None)  # global: later tests count jobs by file id
        assert state.status == "completed", state.error_detail
        return copy_id, await db_session.get(LibraryFile, state.result["library_file_id"])

    async def test_copy_is_sliced_with_its_own_settings(self, async_client, db_session, sidecar):
        copy_id, out = await self._slice_copy(async_client, db_session, plate=2)
        info = await async_client.get(f"/api/v1/farm/library/files/{copy_id}/project")
        assert info.status_code == 200, info.text
        assert has(info.json(), slicer="bambu_studio", sliceable=True, printer_preset="Test Printer 0.4 nozzle")
        assert sidecar[0]["url"] == library.app_settings.bambu_studio_api_url.rstrip("/")
        assert sidecar[0]["plate"] == 2
        # The G-code preview gets the plate's G-code, not the archive (outputs are file_type "gcode").
        gcode = await async_client.get(f"/api/v1/library/files/{out.id}/gcode")
        assert gcode.status_code == 200 and gcode.text == "G28\n"
        # Reopened, the workspace offers that slice to the Preview tab, for the version it was sliced from.
        outputs = (await open_ws(async_client, copy_id))["outputs"]
        assert [(o["plate"], o["library_file_id"], o["current"]) for o in outputs] == [(2, out.id, True)]
        sent = json.loads(entries(sidecar[0]["model_bytes"])[project.PROJECT_SETTINGS_ENTRY])
        assert sent["layer_height"] == "0.1"  # the copy's settings went to the slicer
        assert out.file_metadata["sliced_from_library_file_id"] == copy_id
        assert out.filename.endswith(" - plate 2.gcode.3mf")
        # No farm state was stored for the copy: it is not a farm project.
        assert (
            await db_session.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == copy_id)) is None
        )

    @pytest.mark.parametrize("klipper", [False, True])
    async def test_a_sliced_copy_prints_from_the_queue(
        self, async_client, db_session, test_engine, printer_factory, sidecar, klipper
    ):
        """The queue's slice check has no farm version for a copy: the item starts, it is never failed."""
        from backend.app.farm.jobs import service as jobs
        from backend.app.models.print_queue import PrintQueueItem
        from backend.tests.integration.test_farm_stale_slice import queue_check

        copy_id, out = await self._slice_copy(async_client, db_session, plate=1)
        sent = out
        if klipper:  # Moonraker gets the plate's G-code taken out of the output
            sent = await jobs.gcode_for_klipper(db_session, out, library.to_absolute_path(out.file_path), 1)
            await db_session.commit()
            assert sent.file_metadata["sliced_from_library_file_id"] == copy_id
        printer = await printer_factory(name="P", model="A1")
        item = PrintQueueItem(printer_id=printer.id, library_file_id=sent.id, position=1, status="pending")
        db_session.add(item)
        await db_session.commit()

        assert await jobs.stale_slice_reason(db_session, item) is None
        assert await queue_check(test_engine) == [(item.id, sent.id)]
        await db_session.refresh(item)
        assert item.status == "pending" and item.waiting_reason is None  # not failed (_start_print is stubbed)
        # The copy moved to the trash: the output still prints as sliced, it has no farm version to wait for.
        copy_row = await db_session.get(LibraryFile, copy_id)
        copy_row.deleted_at = datetime(2026, 11, 9)
        await db_session.commit()
        assert await jobs.stale_slice_reason(db_session, item) is None

    async def test_saving_a_copy_of_a_farm_project_changes_nothing_of_the_farm(
        self, async_client, db_session, tmp_path, monkeypatch, sidecar
    ):
        from backend.app.farm import library_problems, reslice
        from backend.app.services.slice_dispatch import slice_dispatch

        monkeypatch.setattr(library.app_settings, "external_roots", str(tmp_path))
        share = tmp_path / "share"
        share.mkdir()
        nas_file = share / "cubes.3mf"
        original = make_project()
        nas_file.write_bytes(original)
        os.utime(nas_file, (time.time() - 300, time.time() - 300))
        folder = (
            await async_client.post(
                "/api/v1/library/folders/external", json={"name": "NAS", "external_path": str(share)}
            )
        ).json()
        await async_client.put(f"/api/v1/farm/library/folders/{folder['id']}", json={"scan_interval_seconds": 300})
        assert (await async_client.post(f"/api/v1/library/folders/{folder['id']}/scan")).json()["added"] == 1
        state = await db_session.scalar(select(FarmLibraryFile))
        plates_before = (await db_session.execute(select(FarmPlate.id, FarmPlate.status))).all()
        scheduled = []

        async def schedule(db, ids):
            scheduled.append(ids)

        monkeypatch.setattr(reslice, "schedule", schedule)
        jobs_before = len(slice_dispatch._jobs)

        doc = await open_ws(async_client, state.library_file_id)
        assert doc["file"]["watched"] is True
        out = (await save(async_client, doc, {"wall_loops": "6"})).json()

        assert nas_file.read_bytes() == original  # the NAS original is untouched
        assert sorted(p.name for p in share.iterdir()) == ["cubes.3mf"]  # nothing written next to it
        copy_row = await db_session.get(LibraryFile, out["library_file_id"])
        assert copy_row.is_external is False and copy_row.folder_id is None  # internal library, not the NAS
        # A scan afterwards finds nothing new or changed: no re-slice, the same plates, no slice job.
        result = (await async_client.post(f"/api/v1/library/folders/{folder['id']}/scan")).json()
        assert (result["added"], result["updated"]) == (0, 0)
        assert scheduled == [] and len(slice_dispatch._jobs) == jobs_before
        assert (await db_session.execute(select(FarmPlate.id, FarmPlate.status))).all() == plates_before
        assert await db_session.scalar(select(func.count()).select_from(FarmLibraryFile)) == 1
        assert await db_session.scalar(select(FarmPlate).where(FarmPlate.library_file_id == copy_row.id)) is None
        # Library problems read only production 3MFs (plates of active products): a copy has no plates.
        assert copy_row.id not in await library_problems.production_files(db_session)
        assert sidecar == []
