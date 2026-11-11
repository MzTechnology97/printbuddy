"""Printfarm (fork): projects whose model_settings.config uses a prefix it never declares (OrcaSlicer's
``<slic3rpe:text>`` of an embossed-text part) read by every reader of that file, as the slicers read them, and
written back with the prefix OrcaSlicer reads. Made-up data."""

from __future__ import annotations

import io
import json
import zipfile
from xml.etree import ElementTree as ET

import pytest

from backend.app.utils.xml_prefixes import declared

# As OrcaSlicer writes a part with embossed text (bbs_3mf.cpp TEXT_TAG): no xmlns:slic3rpe anywhere.
SETTINGS = b"""<?xml version="1.0" encoding="UTF-8"?>
<config>
  <object id="2">
    <metadata key="name" value="Badge"/>
    <metadata key="extruder" value="2"/>
    <part id="1" subtype="normal_part">
      <metadata key="name" value="Badge"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
    </part>
    <part id="3" subtype="normal_part">
      <metadata key="name" value="Text"/>
      <metadata key="extruder" value="3"/>
      <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>
      <slic3rpe:text text="HELLO" font_descriptor="Arial" style_name="Bold" line_height="1"/>
    </part>
  </object>
  <plate>
    <metadata key="plater_id" value="1"/>
    <metadata key="filament_map_mode" value="Auto For Flush"/>
    <model_instance>
      <metadata key="object_id" value="2"/>
      <metadata key="instance_id" value="0"/>
    </model_instance>
  </plate>
</config>
"""
CFG = {
    "printer_model": "Bambu Lab H2D",
    "physical_extruder_map": ["1", "0"],
    "filament_map_mode": "Auto For Flush",
    "filament_map": ["1", "1", "1"],
    "filament_type": ["PLA", "PLA", "PETG"],
    "filament_colour": ["#FFFFFF", "#000000", "#FF0000"],
}


def project() -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("3D/3dmodel.model", '<model><resources><object id="2" type="model"/></resources><build/></model>')
        zf.writestr("Metadata/project_settings.config", json.dumps(CFG))
        zf.writestr("Metadata/model_settings.config", SETTINGS)
    return out.getvalue()


def test_the_raw_file_is_refused_and_declared_reads():
    with pytest.raises(ET.ParseError, match="unbound prefix"):
        ET.fromstring(SETTINGS)
    root = ET.fromstring(declared(SETTINGS))
    assert [p.get("id") for p in root.iter("part")] == ["1", "3"]
    # nothing to declare: the same bytes; declared already: the same bytes
    plain = SETTINGS.replace(b"slic3rpe:text", b"text")
    assert declared(plain) == plain
    assert declared(declared(SETTINGS)) == declared(SETTINGS)


def test_the_farm_reads_the_plate_and_its_filaments():
    from backend.app.farm import variants
    from backend.app.farm.library_problems import read_project

    data = project()
    assert variants.plate_filaments(data, 1) == {2, 3}
    assert [s["slot_id"] for s in variants.plate_slots(data, 1, 0)] == [2, 3]
    assert variants.free_nozzle(data, 1, 0)
    assert read_project(io.BytesIO(data)) == {1: 1}


def test_a_copy_written_back_keeps_the_prefix_orca_reads():
    from backend.app.farm import variants

    for copy in (variants.force_nozzle(project(), 1, "2=1,3=0"), variants.move_plate_to(project(), 1, 1)):
        with zipfile.ZipFile(io.BytesIO(copy)) as zf:
            written = zf.read("Metadata/model_settings.config")
        assert b"<slic3rpe:text " in written  # OrcaSlicer compares the tag name as written
        assert [e.get("text") for e in ET.fromstring(written).iter() if e.tag.endswith("}text")] == ["HELLO"]


def test_the_workspace_and_the_print_dialog_read_it():
    from backend.app.farm.workspace import placement, project as ws_project
    from backend.app.utils.threemf_tools import extract_plate_extruder_set_from_3mf

    with zipfile.ZipFile(io.BytesIO(project())) as zf:
        structure = ws_project.read_structure(zf, CFG)
        assert structure["warnings"] == []
        assert [(o["id"], [p["name"] for p in o["parts"]]) for o in structure["objects"]] == [("2", ["Badge", "Text"])]
        assert extract_plate_extruder_set_from_3mf(zf, 1) == {2, 3}
    assert len(placement._part_settings(SETTINGS)) > 10  # every element, the text one included
