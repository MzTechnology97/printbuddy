"""Printfarm (fork): prints started outside Accoda become batches once their components are known.

No printer is contacted: archives are written directly and Moonraker's history is stubbed. Data is made up.
"""

from datetime import datetime, timezone

import pytest

from backend.app.api.routes.settings import set_setting
from backend.app.farm.jobs import outside
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.models import FarmLibraryFile  # noqa: F401 - its table, read by the plate lookups
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem

API = "/api/v1/farm"
QUEUE_HASH = "abcdef12" + "0" * 56
OUTPUT_HASH = "12345678" + "f" * 56


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 1, hour, minute)


def archive(printer_id, filename, content_hash=None, status="completed", ended=None) -> PrintArchive:
    ended = ended or at(10)
    return PrintArchive(
        printer_id=printer_id,
        filename=filename,
        file_path=f"/a/{filename}",
        file_size=1,
        content_hash=content_hash,
        status=status,
        started_at=ended.replace(minute=0),
        completed_at=ended,
    )


@pytest.fixture
async def farm(async_client, db_session, printer_factory):
    """Black 'Sopra', 15 a plate (plate 2 of 'Sopra.3mf'), once printed by Accoda; outputs and printers made up."""
    shape = (await async_client.post(f"{API}/shapes", json={"name": "Sopra"})).json()
    black = (await async_client.post(f"{API}/components", json={"shape_id": shape["id"], "color": "Nero"})).json()
    project = LibraryFile(filename="Sopra.3mf", file_path="/x/Sopra.3mf", file_type="3mf", file_size=1)
    db_session.add(project)
    await db_session.flush()
    plate = FarmPlate(library_file_id=project.id, plate_index=2, printer_model="A1", materials=["PLA"])
    plate.status = "confirmed"
    plate.yields = [FarmPlateYield(object_name="sopra", quantity=15, shape_id=shape["id"])]
    db_session.add(plate)
    db_session.add(
        LibraryFile(
            filename="A1-Sopra - plate 2.gcode.3mf",
            file_path="/x/out.gcode.3mf",
            file_type="3mf",
            file_size=1,
            source_type="sliced",
            file_hash=OUTPUT_HASH,
            file_metadata={"sliced_from_library_file_id": project.id, "sliced_plate": 2},
        )
    )
    a1 = await printer_factory(name="A1 uno", model="A1")
    k2 = await printer_factory(name="K2", model="K2", provider="klipper")

    # What Accoda printed: its archive has the hash the printer-side name's fingerprint comes from.
    queued = archive(a1.id, "Sopra__abcdef12.3mf", QUEUE_HASH, ended=at(9))
    db_session.add(queued)
    await db_session.flush()
    item = PrintQueueItem(printer_id=a1.id, archive_id=queued.id, status="completed", completed_at=at(9))
    db_session.add(item)
    await db_session.flush()
    job = FarmPrintJob(
        component_code=black["code"],
        color="Nero",
        plate_ids=[plate.id],
        expected_pieces=15,
        status="queued",
        queue_item_id=item.id,
        printer_id=a1.id,
        plate_id=plate.id,
        pieces=15,
    )
    db_session.add(job)
    await set_setting(db_session, outside.SINCE_KEY, at(8).isoformat())
    await db_session.commit()
    return {"code": black["code"], "plate": plate.id, "a1": a1.id, "k2": k2.id, "queued": job.id}


async def completed(client, **params):
    r = await client.get(f"{API}/jobs", params={"state": "completed", **params})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_prints_outside_the_queue_are_recognised_or_left_to_assign(async_client, db_session, farm):
    a1 = farm["a1"]
    db_session.add_all(
        [
            archive(a1, "Sopra__abcdef12.3mf", ended=at(10)),  # reprinted from the display: the queue's fingerprint
            archive(a1, "copia.gcode.3mf", OUTPUT_HASH, ended=at(11)),  # sent from the slicer: a sliced output
            archive(a1, "Sopra plate 2.3mf", ended=at(12)),  # only the name says what it is
            archive(a1, "Gancio.3mf", ended=at(13)),  # nothing known
            archive(a1, "Sopra__abcdef12.3mf", status="failed", ended=at(14)),  # no batch
            archive(a1, "Sopra__abcdef12.3mf", ended=at(7)),  # before the first scan
        ]
    )
    await db_session.commit()
    assert await outside.scan_archives(db_session) == 4
    assert await outside.scan_archives(db_session) == 0  # each print once; Accoda's own print never

    batches = await completed(async_client)
    assert [(b["id"] == farm["queued"], b["outside_queue"], b["component_code"], b["pieces"]) for b in batches] == [
        (True, False, farm["code"], 15),
        (False, True, farm["code"], 15),
        (False, True, farm["code"], 15),
    ]
    assert [(b["color"], b["printer"], b["print_name"]) for b in batches[1:]] == [
        ("Nero", "A1 uno", "Sopra__abcdef12.3mf"),
        ("Nero", "A1 uno", "copia.gcode.3mf"),
    ]
    assert batches[1]["finished_at"] == "2026-01-01T10:00:00"

    listed = {j["print_name"]: j for j in (await async_client.get(f"{API}/jobs")).json() if j["outside_queue"]}
    by_name, unknown = listed["Sopra plate 2.3mf"], listed["Gancio.3mf"]
    assert (by_name["state"], unknown["state"], unknown["proposal"]) == ("to_assign", "to_assign", None)
    assert [(p["component_code"], p["pieces"], p["plate_id"]) for p in by_name["proposal"]] == [
        (farm["code"], 15, farm["plate"])
    ]

    # Assigned today, it is a batch of today: a reader already past the others still gets it.
    before = datetime.now(timezone.utc)
    r = await async_client.post(
        f"{API}/jobs/{by_name['id']}/assign", json={"parts": [{"component_code": farm["code"], "pieces": 12}]}
    )
    assert r.status_code == 200, r.text
    later = await completed(async_client, since=before.isoformat())
    assert [(b["id"], b["pieces"], b["finished_at"]) for b in later] == [(by_name["id"], 12, "2026-01-01T12:00:00")]

    r = await async_client.post(f"{API}/jobs/{unknown['id']}/assign", json={"not_production": True})
    assert r.json()[0]["state"] == "not_production"
    assert len(await completed(async_client)) == 4
    # Once a batch, it stays as it is: Seller Hub may have it already.
    again = {"parts": [{"component_code": farm["code"], "pieces": 1}]}
    assert (await async_client.post(f"{API}/jobs/{by_name['id']}/assign", json=again)).status_code == 409
    assert (await async_client.delete(f"{API}/jobs/{by_name['id']}")).status_code == 409


@pytest.mark.asyncio
async def test_assign_validates_the_components(async_client, db_session, farm):
    db_session.add(archive(farm["a1"], "Gancio.3mf"))
    await db_session.commit()
    await outside.scan_archives(db_session)
    [job] = [j for j in (await async_client.get(f"{API}/jobs")).json() if j["state"] == "to_assign"]
    url = f"{API}/jobs/{job['id']}/assign"
    assert (await async_client.post(url, json={})).status_code == 422
    unknown = {"parts": [{"component_code": "C-9999", "pieces": 1}]}
    assert (await async_client.post(url, json=unknown)).status_code == 422
    twice = {"parts": [{"component_code": farm["code"], "pieces": 1}] * 2}
    assert (await async_client.post(url, json=twice)).status_code == 422

    # Two components of one plate: one batch each, both from this print.
    other = (await async_client.post(f"{API}/components", json={"name": "Sotto", "color": "Nero"})).json()
    both = {"parts": [{"component_code": farm["code"], "pieces": 3}, {"component_code": other["code"], "pieces": 4}]}
    r = await async_client.post(url, json=both)
    assert r.status_code == 200, r.text
    assert [(j["component_code"], j["pieces"], j["print_name"]) for j in r.json()] == [
        (farm["code"], 3, "Gancio.3mf"),
        (other["code"], 4, "Gancio.3mf"),
    ]


@pytest.mark.asyncio
async def test_klipper_prints_done_while_printbuddy_was_off(async_client, db_session, farm, monkeypatch):
    from backend.app.services.printer_manager import printer_manager

    def epoch(d: datetime) -> float:
        return d.replace(tzinfo=timezone.utc).timestamp()

    db_session.add(archive(farm["k2"], "seen.gcode", ended=at(10, 30)))  # Printbuddy was on: the archive counts
    await db_session.commit()
    jobs = [
        {"job_id": "0001", "filename": "seen.gcode", "status": "completed", "start_time": epoch(at(10, 2))},
        {
            "job_id": "0002",
            "filename": "K2-Sopra-plate-2__abcdef12.gcode",
            "status": "completed",
            "start_time": epoch(at(11)),
        },
        {"job_id": "0003", "filename": "Gancio.gcode", "status": "cancelled", "start_time": epoch(at(12))},
    ]
    for j in jobs:
        j["end_time"] = j["start_time"] + 40 * 60
    asked = []

    class Client:
        def _get(self, path):
            asked.append(path)
            return {"jobs": jobs}

    monkeypatch.setattr(printer_manager, "get_client", lambda pid: Client() if pid == farm["k2"] else None)
    await outside.scan_archives(db_session)  # the K2's own archive: not production of a known plate
    assert await outside.scan_klipper_history(db_session) == 1
    assert await outside.scan_klipper_history(db_session) == 0
    assert f"since={epoch(at(8)):.0f}" in asked[0]
    [batch] = [b for b in await completed(async_client) if b["outside_queue"]]
    assert (batch["component_code"], batch["pieces"], batch["printer"], batch["finished_at"]) == (
        farm["code"],
        15,
        "K2",
        "2026-01-01T11:40:00",
    )
