"""Printfarm (fork) phase 2: plates proposed from the 3MF and the printer-model map.

All projects are generated here; none come from a real farm.
"""

import json
import zipfile
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.api.routes import library
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plates.models import FarmPlate
from backend.app.farm.plates.printer_map import PrinterModelResolver, models_in_path
from backend.app.farm.plates.service import parse_plates, plates_by_component
from backend.app.models.library import LibraryFile
from backend.tests.integration.test_farm_library import BAMBU, add_external, age, scan

# plate number -> [(object name, extruder), ...]; repeated names are identical copies.
TWO_PLATES = {1: [("Hook", 1)] * 4 + [("Base", 2)], 2: [("Lid", 1), ("Lid", 1)]}


def make_project(
    path: Path,
    plates: dict[int, list[tuple[str, int]]],
    printer_model: str = "Bambu Lab P1S",
    filament_types: tuple[str, ...] = ("PETG", "PLA"),
) -> Path:
    objects, plate_xml, oid = [], [], 0
    for index, items in plates.items():
        instances = []
        for name, extruder in items:
            oid += 1
            objects.append(
                f'<object id="{oid}"><metadata key="name" value="{name}"/>'
                f'<metadata key="extruder" value="{extruder}"/><part id="1"/></object>'
            )
            instances.append(f'<model_instance><metadata key="object_id" value="{oid}"/></model_instance>')
        plate_xml.append(
            f'<plate><metadata key="plater_id" value="{index}"/><metadata key="plater_name" value=""/>'
            f"{''.join(instances)}</plate>"
        )
    meta = "".join(f'<metadata name="{k}">{v}</metadata>' for k, v in BAMBU.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", f"<model>{meta}<resources/></model>")
        zf.writestr("Metadata/model_settings.config", f"<config>{''.join(objects)}{''.join(plate_xml)}</config>")
        zf.writestr(
            "Metadata/project_settings.config",
            json.dumps(
                {"printer_model": printer_model, "filament_type": list(filament_types), "nozzle_diameter": ["0.4"]}
            ),
        )
    return age(path)


def test_parse_plates_counts_copies_and_materials(tmp_path):
    plates, nozzle = parse_plates(make_project(tmp_path / "p.3mf", TWO_PLATES))
    assert nozzle == 0.4
    assert [(p.index, p.objects, p.materials) for p in plates] == [
        (1, {"Hook": 4, "Base": 1}, ["PETG", "PLA"]),
        (2, {"Lid": 2}, ["PETG"]),
    ]


def test_parse_plates_orca_emboss_prefix_and_empty_plate(tmp_path):
    # Orca writes <slic3rpe:shape> without declaring the prefix; plate 2 has no objects.
    settings = (
        '<config><object id="1"><metadata key="name" value="Tag"/><metadata key="extruder" value="7"/>'
        '<part id="1"><slic3rpe:shape depth="0.3"/><slic3rpe:text text="X"/></part></object>'
        '<plate><metadata key="plater_id" value="1"/>'
        '<model_instance><metadata key="object_id" value="1"/></model_instance></plate>'
        '<plate><metadata key="plater_id" value="2"/></plate></config>'
    )
    path = tmp_path / "emboss.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("Metadata/model_settings.config", settings)
        zf.writestr("Metadata/project_settings.config", json.dumps({"filament_type": ["PLA"]}))
    plates, _ = parse_plates(path)
    # Extruder 7 is out of range: falls back to the first filament.
    assert [(p.index, p.objects, p.materials) for p in plates] == [(1, {"Tag": 1}, ["PLA"])]


def make_copies_project(
    path: Path, objects: dict[int, str], plates: dict[int, list[int]], items: list[str], slice_info: str | None = None
) -> Path:
    """A project whose build holds ``items`` and whose plates list ``{plate: [object id, ...]}``;
    the instance listed first for object N has identify_id 900+N."""
    objs = "".join(
        f'<object id="{o}"><metadata key="name" value="{n}"/><part id="1"/></object>' for o, n in objects.items()
    )
    plate_xml = "".join(
        f'<plate><metadata key="plater_id" value="{index}"/>'
        + "".join(
            f'<model_instance><metadata key="object_id" value="{o}"/>'
            f'<metadata key="identify_id" value="{900 + o}"/></model_instance>'
            for o in oids
        )
        + "</plate>"
        for index, oids in plates.items()
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "3D/3dmodel.model",
            '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"><resources/>'
            f"<build>{''.join(items)}</build></model>",
        )
        zf.writestr("Metadata/model_settings.config", f"<config>{objs}{plate_xml}</config>")
        if slice_info is not None:
            zf.writestr("Metadata/slice_info.config", slice_info)
    return path


def test_parse_plates_counts_copies_left_out_of_model_settings(tmp_path):
    """Orca lists one instance of an object on its plate but prints every build item of it there."""
    items = (
        ['<item objectid="1"/>'] * 15  # a handle copied 15 times, listed once on plate 1
        + ['<item objectid="2"/>', '<item objectid="2" printable="0"/>']  # one copy not printable
        + ['<item objectid="3"/>'] * 3  # listed on plates 2 and 3: its third copy can't be placed
    )
    path = make_copies_project(
        tmp_path / "copies.3mf", {1: "Handle", 2: "Pin", 3: "Clip"}, {1: [1], 2: [2, 3], 3: [3]}, items
    )
    plates, _ = parse_plates(path)
    assert [(p.index, p.objects) for p in plates] == [
        (1, {"Handle": 15}),
        (2, {"Pin": 1, "Clip": 1}),
        (3, {"Clip": 1}),
    ]


def test_parse_plates_counts_what_the_slice_printed(tmp_path):
    """slice_info lists every copy the slice printed: a copy off the plate isn't there, a skipped one is
    marked. Orca names them with labels, Bambu with the object's name; the first copy by identify_id."""
    orca = "".join(
        f'<object identify_id="{900 if n == 0 else 700 + n}" name="Maniglia_Portiera_grande_id_0_copy_{n}" '
        f'skipped="{"true" if n == 14 else "false"}"/>'
        for n in range(15)
    )
    bambu = '<object identify_id="5" name="Pin"/><object identify_id="6" name="Pin"/>'
    slice_info = (
        f'<config><plate><metadata key="index" value="1"/><metadata key="prediction" value="100"/>{orca}</plate>'
        f'<plate><metadata key="index" value="2"/>{bambu}</plate></config>'
    )
    # 16 copies of the handle in the build: one sits off every plate and is never printed.
    items = ['<item objectid="0"/>'] * 16 + ['<item objectid="2"/>'] * 2
    path = make_copies_project(
        tmp_path / "sliced.3mf", {0: "Maniglia Portiera grande", 2: "Pin"}, {1: [0], 2: [2]}, items, slice_info
    )
    plates, _ = parse_plates(path)
    assert [(p.index, p.objects, p.print_time_seconds) for p in plates] == [
        (1, {"Maniglia Portiera grande": 14}, 100),  # 15 printed, one skipped
        (2, {"Pin": 2}, None),
    ]


def test_parse_plates_keeps_its_count_when_the_slice_names_an_unknown_object(tmp_path):
    slice_info = (
        '<config><plate><metadata key="index" value="1"/><object identify_id="1" name="Other"/></plate></config>'
    )
    items = ['<item objectid="1"/>'] * 3
    path = make_copies_project(tmp_path / "odd.3mf", {1: "Hook"}, {1: [1]}, items, slice_info)
    plates, _ = parse_plates(path)
    assert [p.objects for p in plates] == [{"Hook": 3}]


def test_parse_plates_without_model_settings(tmp_path):
    path = tmp_path / "cad.3mf"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", "<model/>")
    assert parse_plates(path) == ([], None)


def test_klipper_models_resolve_with_or_without_the_brand():
    """Slicers write "Creality K1" / "Sovol SV08"; the farm's printers are "K1" / "SV08"."""
    resolver = PrinterModelResolver({}, ["K1", "SV08", "Creality K2 Pro", "Sovol Zero", "P2S"])
    models = ("Creality K1", "Sovol SV08", "Creality K2 Pro", "SOVOL ZERO", "Bambu Lab P2S", "Creality K1 Max")
    got = {m: resolver.resolve(m).target_model for m in (*models, "A1 Mini")}
    assert got == {
        "Creality K1": "K1",
        "Sovol SV08": "SV08",
        "Creality K2 Pro": "Creality K2 Pro",
        "SOVOL ZERO": "Sovol Zero",
        "Bambu Lab P2S": "P2S",
        "Creality K1 Max": None,  # another printer
        "A1 Mini": None,  # none in the farm
    }


def test_models_in_path_prefers_longest_name():
    names = PrinterModelResolver({}, ["A1", "A1 Mini", "P1S", "Sovol Zero"]).known_names()
    assert models_in_path("Box/A1 mini/A1 Mini-PLA-box.3mf", names) == {"a1 mini"}
    assert models_in_path("Box/P1S-PLA-box.3mf", names) == {"p1s"}
    assert models_in_path("Box/SOVOL ZERO/box.3mf", names) == {"sovol zero"}
    assert models_in_path("Box/box-final.3mf", names) == set()


@pytest.fixture(autouse=True)
def allow_test_external_root(monkeypatch, tmp_path):
    monkeypatch.setattr(library.app_settings, "external_roots", str(tmp_path))


@pytest.fixture
async def farm(async_client, printer_factory, tmp_path):
    """NAS with a P1S project in a folder named after the A1, plus the farm's printers."""
    nas = tmp_path / "nas"
    make_project(nas / "Hook" / "A1" / "hook.3mf", TWO_PLATES)
    make_project(nas / "Lid" / "P2S-PETG-lid.3mf", {1: [("Lid", 1)]}, printer_model="Bambu Lab P2S")
    make_project(nas / "Clip" / "K2-clip.3mf", {1: [("Clip", 1)]}, printer_model="Creality K2 Pro")
    make_project(nas / "Cap" / "cap.3mf", {1: [("Cap", 1)]}, printer_model="SOVOL ZERO")
    for model in ("P1S", "A1", "N7", "K2", "Sovol Zero"):  # N7 = how Printbuddy may store a P2S
        await printer_factory(model=model)
    folder = await add_external(async_client, nas)
    await async_client.put(f"/api/v1/farm/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)
    files = (await async_client.get("/api/v1/farm/library/files")).json()
    return {"folder": folder, **{Path(f["path"]).name: f["library_file_id"] for f in files}}


async def get_plates(client, file_id):
    resp = await client.get(f"/api/v1/farm/library/files/{file_id}/plates")
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.integration
class TestPlates:
    async def test_proposed_from_3mf(self, async_client, farm):
        plates = await get_plates(async_client, farm["hook.3mf"])
        assert [p["plate_index"] for p in plates] == [1, 2]
        first = plates[0]
        assert first["status"] == "proposed"
        assert first["yields"] == [
            {
                "object_name": "Hook",
                "quantity": 4,
                "shape_id": None,
                "suggested_shape_id": None,
                "component_code": None,
            },
            {
                "object_name": "Base",
                "quantity": 1,
                "shape_id": None,
                "suggested_shape_id": None,
                "component_code": None,
            },
        ]
        assert (first["printer_model"], first["target_model"], first["target_model_source"]) == ("P1S", "P1S", "auto")
        assert (first["materials"], first["nozzle_diameter"]) == (["PETG", "PLA"], 0.4)

    async def test_plates_read_by_an_older_parser_are_read_again(self, async_client, db_session, farm):
        """A confirmed plate whose count changes stays confirmed: every object keeps its link."""
        for plate in await get_plates(async_client, farm["hook.3mf"]):
            body = {"yields": [{**y, "component_code": f"c-{y['object_name'].lower()}"} for y in plate["yields"]]}
            assert (await async_client.put(f"/api/v1/farm/plates/{plate['id']}", json=body)).status_code == 200
        stored = (
            await db_session.execute(select(FarmPlate).where(FarmPlate.library_file_id == farm["hook.3mf"]))
        ).scalars()
        for plate in stored:
            plate.parse_version = None  # read before copies left out of model_settings counted
            for y in plate.yields:
                y.quantity = 1
        await db_session.commit()
        plates = await get_plates(async_client, farm["hook.3mf"])
        assert [(p["status"], {y["object_name"]: y["quantity"] for y in p["yields"]}) for p in plates] == [
            ("confirmed", {"Hook": 4, "Base": 1}),
            ("confirmed", {"Lid": 2}),
        ]
        assert plates[0]["yields"][0]["component_code"] == "C-HOOK"

    async def test_warns_when_folder_names_another_printer(self, async_client, farm):
        hook = await get_plates(async_client, farm["hook.3mf"])
        assert "a1" in hook[0]["warning"] and "P1S" in hook[0]["warning"]
        lid = await get_plates(async_client, farm["P2S-PETG-lid.3mf"])
        assert lid[0]["warning"] is None  # "P2S" in the name, P2S stored as N7

    async def test_confirm_then_file_change_reproposes_only_changed_plate(
        self, async_client, db_session, farm, tmp_path
    ):
        plates = await get_plates(async_client, farm["hook.3mf"])
        for plate in plates:
            body = {"yields": [{**y, "component_code": f"c-{y['object_name'].lower()}"} for y in plate["yields"]]}
            resp = await async_client.put(f"/api/v1/farm/plates/{plate['id']}", json=body)
            assert resp.json()["status"] == "confirmed"

        # The project now holds 6 hooks and a new strap on plate 1; plate 2 is unchanged.
        # (Quantities alone would keep it confirmed: every object still has its link.)
        changed = {1: [("Hook", 1)] * 6 + [("Base", 2), ("Strap", 1)], 2: TWO_PLATES[2]}
        make_project(tmp_path / "nas" / "Hook" / "A1" / "hook.3mf", changed)
        await scan(async_client, farm["folder"])
        first, second = await get_plates(async_client, farm["hook.3mf"])
        assert first["status"] == "proposed"
        assert first["yields"][0] == {
            "object_name": "Hook",
            "quantity": 6,
            "shape_id": None,
            "suggested_shape_id": None,
            "component_code": "C-HOOK",
        }
        assert second["status"] == "confirmed"

        by_code = (await async_client.get("/api/v1/farm/plates?component_code=c-lid")).json()
        assert [p["id"] for p in by_code] == [second["id"]]

        # Contract used by the products module (and Seller Hub): confirmed plates only.
        found = await plates_by_component(db_session, ["C-HOOK", "c-lid", "C-NONE"])
        assert found == {
            "C-HOOK": [],  # plate 1 is back to proposed
            "C-LID": [
                {
                    "library_file_id": farm["hook.3mf"],
                    "plate": 2,
                    "printer_model": "P1S",
                    "material": "PETG",
                    "yield": 2,
                }
            ],
            "C-NONE": [],
        }

    async def test_time_and_grams_from_slice_of_same_version(self, async_client, db_session, farm):
        file_id = farm["P2S-PETG-lid.3mf"]
        state = await db_session.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == file_id))
        source = await db_session.get(LibraryFile, file_id)
        for sha, seconds in ((state.sha256, 3600), ("older-version", 999)):
            db_session.add(
                LibraryFile(
                    folder_id=source.folder_id,
                    filename="lid.gcode.3mf",
                    file_path="x.gcode.3mf",
                    file_type="gcode",
                    file_size=1,
                    source_type="sliced",
                    file_metadata={
                        "sliced_from_library_file_id": file_id,
                        "sliced_from_sha256": sha,
                        "sliced_plate": 1,
                        "print_time_seconds": seconds,
                        "filament_used_g": 12.5,
                    },
                )
            )
        await db_session.commit()
        plate = (await get_plates(async_client, file_id))[0]
        assert (plate["print_time_seconds"], plate["filament_grams"]) == (3600, 12.5)
        assert plate["sliced_file_id"] is not None


@pytest.mark.integration
class TestPrinterModelMap:
    async def test_auto_manual_and_unmapped(self, async_client, farm):
        data = (await async_client.get("/api/v1/farm/printer-models")).json()
        seen = {s["printer_model"]: (s["target_model"], s["target_model_source"]) for s in data["seen"]}
        assert seen["P1S"] == ("P1S", "auto")
        assert seen["P2S"] == ("N7", "auto")
        assert seen["SOVOL ZERO"] == ("Sovol Zero", "auto")
        assert seen["Creality K2 Pro"] == (None, "unmapped")

        resp = await async_client.put(
            "/api/v1/farm/printer-models", json={"source_model": "Creality K2 Pro", "target_model": "k2"}
        )
        assert resp.status_code == 200
        assert resp.json()["target_model"] == "K2"  # stored as Printbuddy names it
        plate = (await get_plates(async_client, farm["K2-clip.3mf"]))[0]
        assert (plate["target_model"], plate["target_model_source"], plate["warning"]) == ("K2", "manual", None)

        await async_client.delete(f"/api/v1/farm/printer-models/{resp.json()['id']}")
        plate = (await get_plates(async_client, farm["K2-clip.3mf"]))[0]
        assert plate["target_model_source"] == "unmapped"

    async def test_unknown_target_rejected(self, async_client, farm):
        resp = await async_client.put(
            "/api/v1/farm/printer-models", json={"source_model": "Voron 2.4", "target_model": "Ender 3"}
        )
        assert resp.status_code == 400
