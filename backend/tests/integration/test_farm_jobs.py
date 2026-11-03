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

    async def fake_sliced(db, plate):
        return sliced if plate.printer_model == "A1" else None

    async def fake_ask(db, plate):
        slicing_asked.append(plate.printer_model)

    async def fake_local(db, lib_file):
        return sliced_path

    monkeypatch.setattr(service, "sliced_file", fake_sliced)
    monkeypatch.setattr(service, "ask_slicing", fake_ask)
    monkeypatch.setattr(service, "_local_path", fake_local)
    monkeypatch.setattr(service, "extract_filament_requirements", lambda path, plate: [{"slot_id": 1, "type": "PLA"}])
    monkeypatch.setattr(plan, "printer_snapshot", lambda printers: snapshot)
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
    assert farm["slicing"] == ["P1S"]  # the plate without a sliced file is sliced right away
    assert await service.queued_pieces(db_session) == {farm["code"]: 15}

    assert await service.dispatch_once(db_session) == 1
    item = (await db_session.execute(select(PrintQueueItem))).scalar_one()
    a1_one = farm["printers"][0]
    assert (item.printer_id, item.plate_id, item.status) == (a1_one, 2, "pending")
    assert json.loads(item.filament_overrides) == [
        {"slot_id": 1, "type": "PLA", "color": "#161616", "force_color_match": True}
    ]
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
    assert (gcode.filename, gcode.file_type) == ("sopra_plate2_plate2.gcode", "gcode")
    # Colour not readable: queued, but it starts only when the user starts it after checking the spool.
    assert (item.printer_id, item.manual_start, item.filament_overrides) == (farm["printers"][0], True, None)
    assert "Check the Nero spool on A1 uno" in (await db_session.get(FarmPrintJob, job["id"])).note

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
    assert "Check the Nero spool on A1 due" in job.note

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
async def test_chosen_printer_takes_the_3mf_of_the_loaded_material(async_client, db_session, farm):
    """Two 3MF for the same printer, PLA and PETG: black PETG is loaded, so the PETG one goes."""
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
    assert json.loads(item.filament_overrides)[0]["color"] == "#141414"


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
