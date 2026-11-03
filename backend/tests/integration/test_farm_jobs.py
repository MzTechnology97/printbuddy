"""Printfarm (fork): Accoda jobs with alternative plates, placed on the first free printer that fits.

No printer is contacted: the live printer snapshot, slicing and file reads are stubbed. Data is made up.
"""

import json
import zipfile

import pytest
from sqlalchemy import select

from backend.app.farm.jobs import service
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.plan import service as plan
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem

API = "/api/v1/farm"


@pytest.fixture
async def farm(async_client, db_session, printer_factory, monkeypatch, tmp_path):
    """Black 'Sopra' with plates on A1 (sliced, 15 pcs) and P1S (not sliced, 20 pcs); printers stubbed."""
    shape = (await async_client.post(f"{API}/shapes", json={"name": "Sopra"})).json()
    black = (await async_client.post(f"{API}/components", json={"shape_id": shape["id"], "color": "Nero"})).json()
    plates = {}
    for model, pieces in (("A1", 15), ("P1S", 20)):
        plate = FarmPlate(library_file_id=len(plates) + 50, plate_index=2, printer_model=model, materials=["PLA"])
        plate.status = "confirmed"
        plate.yields = [FarmPlateYield(object_name="sopra", quantity=pieces, shape_id=shape["id"])]
        db_session.add(plate)
        plates[model] = plate
    a1_one = await printer_factory(name="A1 uno", model="A1")
    a1_two = await printer_factory(name="A1 due", model="A1")
    p1s = await printer_factory(name="P1S", model="P1S")
    sliced = LibraryFile(
        filename="sopra_plate2.gcode.3mf", file_path="/x/sopra.gcode.3mf", file_type="3mf", file_size=1
    )
    db_session.add(sliced)
    await db_session.commit()

    sliced_path = tmp_path / "sopra.gcode.3mf"
    with zipfile.ZipFile(sliced_path, "w") as zf:
        zf.writestr("Metadata/plate_2.gcode", "G28")
    snapshot = [
        {
            "id": a1_one.id,
            "name": "A1 uno",
            "model": "A1",
            "idle": True,
            "loaded": [{"type": "PLA", "color": "161616"}],
        },
        {
            "id": a1_two.id,
            "name": "A1 due",
            "model": "A1",
            "idle": True,
            "loaded": [{"type": "PLA", "color": "161616"}],
        },
        {"id": p1s.id, "name": "P1S", "model": "P1S", "idle": True, "loaded": [{"type": "PLA", "color": "ffffff"}]},
    ]
    slicing_asked = []

    async def fake_sliced(db, plate, color):
        return sliced if plate.printer_model == "A1" else None

    async def fake_ask(db, plate, color):
        slicing_asked.append((plate.printer_model, color))

    async def fake_local(db, lib_file):
        return sliced_path

    monkeypatch.setattr(service, "variant_file", fake_sliced)
    monkeypatch.setattr(service, "ask_variant", fake_ask)
    monkeypatch.setattr(service, "_local_path", fake_local)
    monkeypatch.setattr(service, "extract_filament_requirements", lambda path, plate: [{"slot_id": 1, "type": "PLA"}])
    monkeypatch.setattr(plan, "printer_snapshot", lambda printers: snapshot)
    # The live slots: the snapshot's loaded filaments, tray i on AMS slot i.
    monkeypatch.setattr(
        service,
        "_loaded_trays",
        lambda pid: [
            {"type": f["type"], "color": f"#{f['color']}", "global_tray_id": i}
            for i, f in enumerate(next(p for p in snapshot if p["id"] == pid)["loaded"])
        ],
    )
    return {
        "code": black["code"],
        "plates": [plates["A1"].id, plates["P1S"].id],
        "snapshot": snapshot,
        "slicing": slicing_asked,
        "printers": (a1_one.id, a1_two.id, p1s.id),
    }


async def accoda(client, code, plate_ids):
    r = await client.post(f"{API}/jobs", json={"jobs": [{"component_code": code, "plate_ids": plate_ids}]})
    assert r.status_code == 200, r.text
    return r.json()[0]


@pytest.mark.asyncio
async def test_job_waits_then_goes_to_the_free_printer_with_the_colour(async_client, db_session, farm):
    job = await accoda(async_client, farm["code"], farm["plates"])
    assert (job["state"], job["pieces"]) == ("waiting", 15)
    assert farm["slicing"] == [("P1S", "Nero")]  # sliced right away, in the component's colour
    assert await service.queued_pieces(db_session) == {farm["code"]: 15}

    assert await service.dispatch_once(db_session) == 1
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    a1_one = farm["printers"][0]
    assert (item.printer_id, item.plate_id, item.status) == (a1_one, 2, "pending")
    assert (json.loads(item.ams_mapping), item.filament_overrides, item.manual_start) == ([0], None, False)
    assert json.loads(item.required_filament_types) == ["PLA"]

    jobs = (await async_client.get(f"{API}/jobs")).json()
    assert [(j["state"], j["printer"], j["pieces"]) for j in jobs] == [("pending", "A1 uno", 15)]
    # Nothing is placed twice, and a second job takes the other free A1.
    assert await service.dispatch_once(db_session) == 0
    await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 1
    printers = sorted((await db_session.execute(select(PrintQueueItem.printer_id))).scalars())
    assert printers == sorted(farm["printers"][:2])
    assert await service.queued_pieces(db_session) == {farm["code"]: 30}


@pytest.mark.asyncio
async def test_job_explains_why_it_waits(async_client, db_session, farm):
    for printer in farm["snapshot"][:2]:
        printer["loaded"] = [{"type": "PETG", "color": "161616"}]  # black, but the wrong material
    job = await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 0
    note = (await db_session.get(FarmPrintJob, job["id"])).note
    assert "A1 uno: no Nero PLA loaded" in note
    assert "being sliced" in note  # the P1S plate


@pytest.mark.asyncio
async def test_cancel_and_validation(async_client, db_session, farm):
    job = await accoda(async_client, farm["code"], farm["plates"])
    await service.dispatch_once(db_session)
    assert (await async_client.delete(f"{API}/jobs/{job['id']}")).json()["state"] == "cancelled"
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    await db_session.refresh(item)
    assert item.status == "cancelled"
    assert await service.queued_pieces(db_session) == {}

    bad = await async_client.post(f"{API}/jobs", json={"jobs": [{"component_code": farm["code"], "plate_ids": [999]}]})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_klipper_gets_the_plate_gcode_and_a_manual_start(async_client, db_session, farm):
    from backend.app.models.printer import Printer

    for printer_id in farm["printers"][:2]:
        (await db_session.get(Printer, printer_id)).provider = "klipper"
    await db_session.commit()
    farm["snapshot"][0]["loaded"] = []  # single spool: what is loaded can't be read
    farm["snapshot"][1]["idle"] = False
    job = await accoda(async_client, farm["code"], farm["plates"])

    assert await service.dispatch_once(db_session) == 1
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    gcode = await db_session.get(LibraryFile, item.library_file_id)
    assert (gcode.filename, gcode.file_type) == ("sopra-plate2.gcode", "gcode")  # readable, as on the printer
    # Colour not readable: queued, but it starts only when the user starts it after checking the spool.
    assert (item.printer_id, item.manual_start, item.filament_overrides) == (farm["printers"][0], True, None)
    assert "Load the Nero spool on A1 uno" in (await db_session.get(FarmPrintJob, job["id"])).note

    # The extracted G-code is reused for the next job on the same plate.
    await db_session.get(PrintQueueItem, item.id)
    item.status = "completed"
    await db_session.commit()
    await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 1
    files = (await db_session.execute(select(PrintQueueItem.library_file_id))).scalars().all()
    assert files == [gcode.id, gcode.id]


@pytest.mark.asyncio
async def test_chosen_printer_gets_the_job_now_even_if_busy(async_client, db_session, farm):
    a1_two = farm["printers"][1]
    farm["snapshot"][1].update(idle=False, loaded=[{"type": "PLA", "color": "ffffff"}])  # busy, white loaded
    r = await async_client.post(
        f"{API}/jobs",
        json={"jobs": [{"component_code": farm["code"], "plate_ids": farm["plates"], "printer_id": a1_two}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["state"] == "pending"  # in the printer's queue right away, no tick needed
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    job = await db_session.get(FarmPrintJob, r.json()[0]["id"])
    assert job.plate_ids == [farm["plates"][0]]  # only the A1 plate
    # No black spool loaded: queued anyway, started by hand after loading it.
    assert (item.printer_id, item.manual_start, item.filament_overrides) == (a1_two, True, None)
    assert "Load the Nero spool on A1 due" in job.note

    # A printer of a model without a plate is refused.
    bad = await async_client.post(
        f"{API}/jobs",
        json={
            "jobs": [
                {"component_code": farm["code"], "plate_ids": farm["plates"][:1], "printer_id": farm["printers"][2]}
            ]
        },
    )
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_chosen_printer_takes_the_3mf_of_the_loaded_material(async_client, db_session, farm, monkeypatch):
    """Two 3MF for the same printer, PLA and PETG: black PETG is loaded, so the PETG one goes."""
    reads = iter([[{"slot_id": 1, "type": "PLA"}], [{"slot_id": 1, "type": "PETG"}]])  # each 3MF's slice_info
    monkeypatch.setattr(service, "extract_filament_requirements", lambda path, plate: next(reads))
    a1_two = farm["printers"][1]
    pla = await db_session.get(FarmPlate, farm["plates"][0])
    petg = FarmPlate(library_file_id=60, plate_index=1, printer_model="A1", materials=["PETG"])
    petg.status = "confirmed"
    petg.yields = [FarmPlateYield(object_name="sopra", quantity=12, shape_id=pla.yields[0].shape_id)]
    db_session.add(petg)
    await db_session.commit()
    farm["snapshot"][1].update(idle=False, loaded=[{"type": "PETG", "color": "141414"}])
    r = await async_client.post(
        f"{API}/jobs",
        json={"jobs": [{"component_code": farm["code"], "plate_ids": [pla.id, petg.id], "printer_id": a1_two}]},
    )
    assert r.status_code == 200, r.text
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    job = await db_session.get(FarmPrintJob, r.json()[0]["id"])
    assert (job.plate_id, job.pieces, item.manual_start) == (petg.id, 12, False)
    assert (json.loads(item.ams_mapping), item.filament_overrides) == ([0], None)


@pytest.mark.asyncio
async def test_klipper_without_cfs_uses_the_spool_set_by_hand(async_client, db_session, farm):
    from backend.app.models.printer import Printer

    a1_one = farm["printers"][0]
    (await db_session.get(Printer, a1_one)).provider = "klipper"
    await db_session.commit()
    farm["snapshot"][0]["loaded"] = []  # single spool: nothing reported
    farm["snapshot"][1]["idle"] = False

    spools = (await async_client.get(f"{API}/printer-spools")).json()
    assert [(s["name"], s["material"]) for s in spools] == [("A1 uno", None)]  # Bambu printers aren't listed
    r = await async_client.put(f"{API}/printer-spools/{a1_one}", json={"material": "pla", "color_name": "Nero"})
    assert r.json() == {"printer_id": a1_one, "material": "PLA", "color_name": "Nero", "color": "111111"}
    assert (
        await async_client.put(f"{API}/printer-spools/{a1_one}", json={"material": "PLA", "color_name": "Viola?"})
    ).status_code == 422
    assert (
        await async_client.put(
            f"{API}/printer-spools/{farm['printers'][2]}", json={"material": "PLA", "color_name": "Nero"}
        )
    ).status_code == 404

    await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 1
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    # Black PLA said to be loaded: it starts by itself after the plate-clear OK, nothing forced on the status.
    assert (item.printer_id, item.manual_start, item.filament_overrides) == (a1_one, False, None)

    assert (await async_client.delete(f"{API}/printer-spools/{a1_one}")).status_code == 200
    assert (await async_client.get(f"{API}/printer-spools")).json()[0]["material"] is None


@pytest.mark.asyncio
async def test_chosen_printer_waits_for_the_slice(async_client, db_session, farm):
    p1s = farm["printers"][2]
    r = await async_client.post(
        f"{API}/jobs", json={"jobs": [{"component_code": farm["code"], "plate_ids": farm["plates"], "printer_id": p1s}]}
    )
    job = r.json()[0]
    assert (job["state"], job["printer_id"]) == ("waiting", p1s)
    assert "being sliced" in (await db_session.get(FarmPrintJob, job["id"])).note
    assert (await db_session.execute(select(PrintQueueItem))).first() is None


async def test_klipper_gcode_follows_a_new_slice_of_the_output(db_session, tmp_path, monkeypatch):
    """An output sliced again keeps its row: the extracted G-code is refreshed in its own row too."""
    import hashlib
    from types import SimpleNamespace

    from backend.app.api.routes import library as lib
    from backend.app.farm.plates.service import _latest_slices

    monkeypatch.setattr(lib, "get_library_files_dir", lambda: tmp_path)
    src = tmp_path / "part.gcode.3mf"

    def slice_to(text: str) -> str:
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("Metadata/plate_1.gcode", text)
        return hashlib.sha256(src.read_bytes()).hexdigest()

    meta = {"sliced_from_library_file_id": 99, "sliced_from_sha256": "v1", "sliced_plate": None}
    sliced = LibraryFile(
        filename="part.gcode.3mf",
        file_path=str(src),
        file_type="gcode",
        file_size=1,
        source_type="sliced",
        file_hash=slice_to("; first"),
        file_metadata={**meta, "used_embedded_settings": True},
    )
    db_session.add(sliced)
    await db_session.commit()

    first = await service.gcode_for_klipper(db_session, sliced, src, 1)
    assert lib.to_absolute_path(first.file_path).read_text() == "; first"
    assert await service.gcode_for_klipper(db_session, sliced, src, 1) is first  # same bytes: reused

    sliced.file_hash = slice_to("; second")  # the output was sliced again in place
    second = await service.gcode_for_klipper(db_session, sliced, src, 1)
    assert second.id == first.id  # queued Klipper prints keep pointing at it
    assert lib.to_absolute_path(second.file_path).read_text() == "; second"

    # The extracted G-code carries the output's metadata but is not an output of the project.
    assert (await _latest_slices(db_session, SimpleNamespace(id=99), "v1")) == {1: sliced}


@pytest.mark.asyncio
async def test_print_dialog_options_reach_the_queue_item(async_client, db_session, farm):
    """Slots chosen by hand, start time and queue flags go to the queue item; no spool check then."""
    a1_two = farm["printers"][1]
    farm["snapshot"][1]["loaded"] = [{"type": "PLA", "color": "ffffff"}]  # white: no match, but slots are chosen
    r = await async_client.post(
        f"{API}/jobs",
        json={
            "jobs": [
                {
                    "component_code": farm["code"],
                    "plate_ids": farm["plates"][:1],
                    "printer_id": a1_two,
                    "ams_mapping": [2],
                    "scheduled_time": "2030-01-02T10:30:00+01:00",
                    "require_previous_success": True,
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()[0]["scheduled_time"] == "2030-01-02T09:30:00"
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    assert (item.printer_id, json.loads(item.ams_mapping), item.filament_overrides) == (a1_two, [2], None)
    assert (item.manual_start, item.require_previous_success, str(item.scheduled_time)) == (
        False,
        True,
        "2030-01-02 09:30:00",
    )
    assert (await db_session.get(FarmPrintJob, r.json()[0]["id"])).note is None

    # Slots belong to a chosen printer.
    job = {"component_code": farm["code"], "plate_ids": farm["plates"][:1], "ams_mapping": [0]}
    assert (await async_client.post(f"{API}/jobs", json={"jobs": [job]})).status_code == 422


@pytest.mark.asyncio
async def test_first_free_printer_job_waits_for_its_start_time(async_client, db_session, farm):
    r = await async_client.post(
        f"{API}/jobs",
        json={
            "jobs": [
                {"component_code": farm["code"], "plate_ids": farm["plates"], "scheduled_time": "2999-01-01T08:00:00Z"},
                {"component_code": farm["code"], "plate_ids": farm["plates"], "manual_start": True},
            ]
        },
    )
    later, manual = r.json()
    assert await service.dispatch_once(db_session) == 1  # only the one without a start time
    assert (await db_session.get(FarmPrintJob, later["id"])).note == "Starts at 2999-01-01 08:00 UTC"
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    assert item.id == (await db_session.get(FarmPrintJob, manual["id"])).queue_item_id
    assert item.manual_start is True  # black is loaded, but the user asked to start it by hand


@pytest.mark.asyncio
async def test_plate_filaments_for_the_slot_mapping(async_client, farm):
    a1, p1s = farm["plates"]
    assert (await async_client.get(f"{API}/plates/{a1}/filaments")).json() == {
        "plate_id": a1,
        "sliced": True,
        "filaments": [{"slot_id": 1, "type": "PLA"}],
    }
    assert (await async_client.get(f"{API}/plates/{p1s}/filaments")).json()["sliced"] is False
    assert (await async_client.get(f"{API}/plates/99999/filaments")).status_code == 404


def test_slot_mapping_follows_the_component_colour_not_the_3mf():
    """3 filaments in the 3MF, the plate uses the 3rd: the black variant goes on the black tray, the rest -1."""
    used = [{"slot_id": 3, "type": "PETG", "color": "#FFC800"}]  # the 3MF says yellow: it doesn't count
    trays = [
        {"type": "PETG", "color": "#FFC800", "global_tray_id": 0},
        {"type": "PLA", "color": "#111111", "global_tray_id": 1},  # black, wrong material
        {"type": "PETG", "color": "#121212", "global_tray_id": 2},
        {"type": "PETG", "color": "#FF7A00", "global_tray_id": 3},
    ]
    assert service._slot_mapping(used, trays, "111111", 80) == [-1, -1, 2]
    assert service._slot_mapping(used, trays[:2], "111111", 80) is None  # no black PETG loaded


@pytest.mark.asyncio
async def test_job_waits_without_a_tray_of_the_component_colour(async_client, db_session, farm, monkeypatch):
    monkeypatch.setattr(service, "extract_filament_requirements", lambda path, plate: [{"slot_id": 3, "type": "PLA"}])
    farm["snapshot"][0]["loaded"] = [{"type": "PLA", "color": "ffc800"}, {"type": "PLA", "color": "ff7a00"}]
    farm["snapshot"][1]["idle"] = False
    job = await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 0  # no black PLA: not sent by itself
    assert "A1 uno: no Nero PLA loaded" in (await db_session.get(FarmPrintJob, job["id"])).note

    farm["snapshot"][0]["loaded"].append({"type": "PLA", "color": "161616"})
    assert await service.dispatch_once(db_session) == 1
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    assert (json.loads(item.ams_mapping), item.manual_start) == ([-1, -1, 2], False)


@pytest.mark.asyncio
async def test_completed_jobs_for_seller_hub_however_old(async_client, db_session, farm):
    from datetime import datetime

    done = await accoda(async_client, farm["code"], farm["plates"])
    still = await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 2
    item = await db_session.get(PrintQueueItem, (await db_session.get(FarmPrintJob, done["id"])).queue_item_id)
    item.status, item.completed_at = "completed", datetime(2026, 1, 1, 9, 30)  # long before the 2-day window
    await db_session.commit()

    completed = (await async_client.get(f"{API}/jobs", params={"state": "completed"})).json()
    assert [(j["id"], j["state"], j["finished_at"]) for j in completed] == [
        (done["id"], "completed", "2026-01-01T09:30:00")
    ]
    later = {"state": "completed", "since": "2026-01-01T10:31:00+01:00"}  # 09:31 UTC: after it
    assert (await async_client.get(f"{API}/jobs", params=later)).json() == []
    earlier = {"state": "completed", "since": "2026-01-01T09:30:00Z"}
    assert [j["id"] for j in (await async_client.get(f"{API}/jobs", params=earlier)).json()] == [done["id"]]

    # Without parameters it is the same list as before, now with finished_at.
    default = {j["id"]: j for j in (await async_client.get(f"{API}/jobs")).json()}
    assert default[still["id"]]["finished_at"] is None
    assert (await async_client.get(f"{API}/jobs", params={"state": "printing"})).status_code == 422


def test_slot_mapping_reads_colours_in_any_form():
    """The K2 case: 3MF #F75403, CFS slot F75403FF, same PETG; the farm's Arancio is that orange too."""
    used = [{"slot_id": 1, "type": "PETG", "color": "#F75403"}]
    trays = [{"type": "PETG", "color": "F75403FF", "global_tray_id": 3}]
    assert service._slot_mapping(used, trays, "f75403", 80) == [3]
    assert service._slot_mapping(used, trays, "f28c28", 80) == [3]  # within the farm's tolerance


@pytest.mark.asyncio
async def test_klipper_gcode_row_has_the_plate_time_grams_and_picture(db_session, tmp_path, monkeypatch):
    """The K2 queue row showed no picture, time or grams: the extracted G-code's row carries the plate's."""
    import io

    from PIL import Image

    from backend.app.api.routes import library as lib

    monkeypatch.setattr(lib, "get_library_files_dir", lambda: tmp_path)
    monkeypatch.setattr(lib, "get_library_thumbnails_dir", lambda: tmp_path)
    png = io.BytesIO()
    Image.new("RGBA", (64, 64), (247, 84, 3, 255)).save(png, "PNG")
    src = tmp_path / "K2-Citroen AMI - plate 2.gcode.3mf"
    with zipfile.ZipFile(src, "w") as zf:
        zf.writestr("Metadata/plate_2.gcode", "; HEADER_BLOCK_END\nG28\n;LAYER_CHANGE\nG1 X1\n")
        zf.writestr("Metadata/plate_2.png", png.getvalue())
        zf.writestr(
            "Metadata/slice_info.config",
            '<config><plate><metadata key="index" value="2"/><metadata key="prediction" value="28303"/>'
            '<filament id="1" type="PETG" color="#F75403" used_g="163.98"/></plate></config>',
        )
    sliced = LibraryFile(
        filename=src.name, file_path=str(src), file_type="gcode", file_size=1, source_type="sliced", file_hash="h1"
    )
    db_session.add(sliced)
    await db_session.commit()

    row = await service.gcode_for_klipper(db_session, sliced, src, 2)
    assert row.filename == "K2-Citroen-AMI-plate-2.gcode"
    meta = row.file_metadata
    assert (meta["print_time_seconds"], meta["filament_used_grams"], meta["extracted_version"]) == (28303, 163.98, 2)
    assert lib.to_absolute_path(row.thumbnail_path).read_bytes() == png.getvalue()
    text = lib.to_absolute_path(row.file_path).read_bytes()
    assert b"; thumbnail begin 96x96" in text and b"SET_PRINT_STATS_INFO CURRENT_LAYER=1" in text

    # A row extracted before the preview (version 1) is redone in place, once.
    row.file_metadata = {**meta, "extracted_version": 1}
    await db_session.commit()
    again = await service.gcode_for_klipper(db_session, sliced, src, 2)
    assert again.id == row.id and again.file_metadata["extracted_version"] == 2
    assert await service.gcode_for_klipper(db_session, sliced, src, 2) is again
