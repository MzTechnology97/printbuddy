"""Printfarm (fork): prints started outside Accoda become batches once their components are known.

No printer is contacted: archives are written directly and Moonraker's history is stubbed. Data is made up.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from backend.app.api.routes.settings import set_setting
from backend.app.farm.jobs import outside, service
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.models import FarmLibraryFile  # noqa: F401 - its table, read by the plate lookups
from backend.app.farm.plan.models import FarmPrinterSpool
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.products.models import FarmComponent
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
    return {
        "code": black["code"],
        "shape": shape["id"],
        "plate": plate.id,
        "a1": a1.id,
        "k2": k2.id,
        "queued": job.id,
    }


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


def test_colour_name_from_the_colour_table():
    from backend.app.farm.plan.service import DEFAULT_COLORS

    assert service.colour_name("#161616", DEFAULT_COLORS, 80) == "Nero"
    assert service.colour_name("FFFFFF", DEFAULT_COLORS, 80) == "Bianco"
    assert service.colour_name("#00ffff", DEFAULT_COLORS, 80) == "#00ffff"  # nothing near: the colour itself
    assert service.colour_name("fff", DEFAULT_COLORS, 80) == "Bianco"  # short form
    assert service.colour_name("F5F5F5FF", DEFAULT_COLORS, 80) == "Bianco"  # RGBA
    for bad in ("", "#12", "xyz", "zzzzzz", None):
        assert service.colour_name(bad, DEFAULT_COLORS, 80) is None


def test_a_sure_colour_needs_many_samples_nearly_all_alike():
    def sure(samples):
        return service.used_color({service.USED_COLORS_KEY: samples}, sure=True)

    assert sure({"Bianco": 9, "Nero": 1}) == "Bianco"
    assert sure({"Bianco": 3}) is None  # too few
    assert sure({"Bianco": 8, "Nero": 4}) is None  # not clear enough
    assert service.used_color({service.USED_COLORS_KEY: {"Bianco": 8, "Nero": 4}}) == "Bianco"  # the batch's colour


@pytest.fixture
def printing(farm, monkeypatch):
    """The A1 feeds AMS slot 1 (white); the K2 reports no slot (no CFS). Both print 'Sopra__abcdef12'.

    Add printers to ``running`` to run them; ``trays`` is the A1's AMS.
    """
    from backend.app.services.printer_manager import printer_manager

    state = SimpleNamespace(
        running=set(), trays=[{"global_tray_id": 0, "color": "#161616"}, {"global_tray_id": 1, "color": "#f5f5f5"}]
    )
    monkeypatch.setattr(
        printer_manager,
        "get_status",
        lambda pid: SimpleNamespace(
            state="RUNNING" if pid in state.running else "IDLE", tray_now=1, subtask_name="Sopra__abcdef12"
        ),
    )
    monkeypatch.setattr(service, "_loaded_trays", lambda pid: state.trays if pid == farm["a1"] else [])
    return state


@pytest.mark.asyncio
async def test_the_batch_colour_is_the_filament_the_printer_fed(async_client, db_session, farm, printing):
    a1, k2 = farm["a1"], farm["k2"]
    # Accoda's black job printing on the A1 from the white slot; on the K2, a print from its display.
    running = archive(a1, "Sopra__abcdef12.3mf", QUEUE_HASH, status="printing", ended=at(15))
    display = archive(k2, "Sopra__abcdef12.gcode", status="printing", ended=at(15))
    leftover = archive(a1, "Vecchio.3mf", status="printing", ended=at(14))  # never closed: not this print
    db_session.add_all(
        [
            leftover,
            running,
            display,
            FarmPrinterSpool(printer_id=k2, material="PLA", color_name="Rosso", color="d32f2f"),
        ]
    )
    await db_session.flush()
    item = PrintQueueItem(printer_id=a1, archive_id=running.id, status="printing")
    db_session.add(item)
    await db_session.flush()
    job = FarmPrintJob(
        component_code=farm["code"],
        color="Nero",
        plate_ids=[farm["plate"]],
        expected_pieces=15,
        status="queued",
        queue_item_id=item.id,
        printer_id=a1,
        plate_id=farm["plate"],
        pieces=15,
    )
    db_session.add(job)
    await db_session.commit()

    printing.running.update({a1, k2})
    assert await service.record_used_colors(db_session) == 2
    assert await service.record_used_colors(db_session) == 2
    printing.trays[1]["color"] = "zz"  # a slot reporting garbage: no sample, no error
    assert await service.record_used_colors(db_session) == 1
    printing.trays[1]["color"] = "#f5f5f5"
    assert (await db_session.get(PrintArchive, leftover.id)).extra_data is None
    item.status, item.completed_at = "completed", at(16)
    for a in (running, display):
        a.status = "completed"
    await db_session.commit()
    printing.running.clear()
    assert await service.record_used_colors(db_session) == 0
    await outside.scan_archives(db_session)

    batches = {b["printer"]: b for b in await completed(async_client, since=at(15).isoformat())}
    assert [(p, b["color"], b["component_color"], b["color_from_printer"]) for p, b in sorted(batches.items())] == [
        ("A1 uno", "Bianco", "Nero", True),  # the slot in use, not the component's colour
        ("K2", "Rosso", "Nero", True),  # no CFS: the spool set by hand
    ]
    # Printed before this: no sample, the component's colour.
    [old] = [b for b in await completed(async_client) if b["id"] == farm["queued"]]
    assert (old["color"], old["color_from_printer"]) == ("Nero", False)


@pytest.mark.asyncio
async def test_the_colour_fed_picks_between_components_of_one_piece(async_client, db_session, farm):
    body = {"shape_id": farm["shape"], "color": "Bianco"}
    white = (await async_client.post(f"{API}/components", json=body)).json()
    fed_white = archive(farm["a1"], "copia.gcode.3mf", OUTPUT_HASH, ended=at(10))
    fed_white.extra_data = {service.USED_COLORS_KEY: {"Bianco": 9, "Nero": 1}}
    barely = archive(farm["a1"], "copia 3.gcode.3mf", OUTPUT_HASH, ended=at(12))
    barely.extra_data = {service.USED_COLORS_KEY: {"Bianco": 3}}  # too few samples to decide
    db_session.add_all([fed_white, archive(farm["a1"], "copia 2.gcode.3mf", OUTPUT_HASH, ended=at(11)), barely])
    await db_session.commit()
    await outside.scan_archives(db_session)

    [batch] = [b for b in await completed(async_client) if b["outside_queue"]]
    assert (batch["component_code"], batch["color"]) == (white["code"], "Bianco")
    left = [j for j in (await async_client.get(f"{API}/jobs")).json() if j["state"] == "to_assign"]
    assert len(left) == 2
    for job in left:  # one of the two colours, never both: nothing decided for the user
        assert sorted((p["component_code"], p["alternative"]) for p in job["proposal"]) == sorted(
            [(farm["code"], True), (white["code"], True)]
        )


@pytest.mark.asyncio
async def test_one_file_queued_for_two_components_is_a_proposal(async_client, db_session, farm):
    other = (await async_client.post(f"{API}/components", json={"name": "Sotto", "color": "Nero"})).json()
    again = archive(farm["a1"], "Sopra__abcdef12.3mf", QUEUE_HASH, ended=at(9, 30))
    db_session.add(again)
    await db_session.flush()
    item = PrintQueueItem(printer_id=farm["a1"], archive_id=again.id, status="completed", completed_at=at(9, 30))
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        FarmPrintJob(
            component_code=other["code"],
            color="Nero",
            plate_ids=[],
            expected_pieces=4,
            status="queued",
            queue_item_id=item.id,
            printer_id=farm["a1"],
            pieces=4,
        )
    )
    db_session.add(archive(farm["a1"], "Sopra__abcdef12.3mf", ended=at(10)))  # reprinted from the display
    await db_session.commit()
    await outside.scan_archives(db_session)

    assert not [b for b in await completed(async_client) if b["outside_queue"]]
    [job] = [j for j in (await async_client.get(f"{API}/jobs")).json() if j["state"] == "to_assign"]
    assert sorted((p["component_code"], p["pieces"], p["alternative"]) for p in job["proposal"]) == sorted(
        [(farm["code"], 15, True), (other["code"], 4, True)]
    )
    assert "Sotto" in {p["name"] for p in job["proposal"]}


@pytest.mark.asyncio
async def test_a_print_to_assign_is_notified(async_client, db_session, farm, farm_notifications):
    from backend.app.farm import notify

    db_session.add(archive(farm["a1"], "Sopra plate 2.3mf", ended=at(10)))
    await db_session.commit()
    await outside.scan_archives(db_session)
    for task in list(notify._tasks):
        await task
    [(event, values)] = farm_notifications
    assert (event, values["batch"]) == ("farm_batch_to_assign", "Sopra plate 2.3mf")
    assert values["detail"].startswith(f"A1 uno: Sopra plate 2.3mf stampato fuori coda. Sembra: {farm['code']} ")
    assert values["detail"].endswith(" ×15")


@pytest.mark.asyncio
async def test_klipper_history_is_read_page_by_page_from_a_cursor(async_client, db_session, farm, monkeypatch):
    from backend.app.api.routes.settings import get_setting
    from backend.app.services.printer_manager import printer_manager

    base = at(9).replace(tzinfo=timezone.utc).timestamp()
    jobs = [
        {
            "job_id": f"{i:04d}",
            "filename": f"Regalo-{i}.gcode",
            "status": "completed",
            "start_time": base + i * 60,
            "end_time": base + i * 60 + 30,
        }
        for i in range(150)
    ]
    jobs.append({"job_id": "0150", "filename": "Lungo.gcode", "status": "in_progress", "start_time": base + 150 * 60})
    asked = []

    class Client:
        def _get(self, path):
            asked.append(path)
            start = int(path.split("&start=")[1].split("&")[0])
            since = float(path.split("&since=")[1])
            later = [j for j in jobs if j["start_time"] >= since]
            return {"jobs": later[start : start + outside.HISTORY_PAGE]}

    monkeypatch.setattr(printer_manager, "get_client", lambda pid: Client() if pid == farm["k2"] else None)
    assert await outside.scan_klipper_history(db_session) == 150  # past the first 100
    assert ["&start=0&" in asked[0], "&start=100&" in asked[1]] == [True, True]
    # The cursor stops at the print still running, so it is read again until it ends.
    cursor = float(await get_setting(db_session, outside.HISTORY_KEY.format(farm["k2"])))
    assert cursor == base + 150 * 60
    asked.clear()
    jobs[-1].update(status="completed", end_time=base + 160 * 60)
    assert await outside.scan_klipper_history(db_session) == 1
    assert f"since={cursor:.0f}" in asked[0]


@pytest.mark.asyncio
async def test_a_klipper_print_archived_across_a_restart_is_counted_once(async_client, db_session, farm, monkeypatch):
    from backend.app.services.printer_manager import printer_manager

    def epoch(d: datetime) -> float:
        return d.replace(tzinfo=timezone.utc).timestamp()

    # Printbuddy restarted mid-print: the archive starts at the restart, then ends with the print.
    restarted = archive(farm["k2"], "K2-Sopra-plate-2.gcode", ended=at(11, 59))
    restarted.started_at = at(11, 30)
    other = archive(farm["k2"], "Altro.gcode", ended=at(13, 50))  # another file in the window: not the same print
    db_session.add_all([restarted, other])
    await db_session.commit()
    jobs = [
        {
            "job_id": "0001",
            "filename": "K2-Sopra-plate-2__abcdef12.gcode",
            "status": "completed",
            "start_time": epoch(at(11)),
            "end_time": epoch(at(12)),
        },
        {
            "job_id": "0002",
            "filename": "Gancio.gcode",
            "status": "completed",
            "start_time": epoch(at(13)),
            "end_time": epoch(at(14)),
        },
    ]

    class Client:
        def _get(self, path):
            return {"jobs": jobs if "&start=0&" in path else []}

    monkeypatch.setattr(printer_manager, "get_client", lambda pid: Client() if pid == farm["k2"] else None)
    assert await outside.scan_klipper_history(db_session) == 1
    [job] = [j for j in (await async_client.get(f"{API}/jobs")).json() if j["outside_queue"]]
    assert job["print_name"] == "Gancio.gcode"


@pytest.mark.asyncio
async def test_one_failing_step_never_stops_the_tick(db_session):
    async def broken(db):
        raise ValueError("bad colour")

    await service._guarded(db_session, "colour sampling", broken)  # logged, not raised


@pytest.mark.asyncio
async def test_the_next_print_of_the_same_file_does_not_hide_one_done_while_off(
    async_client, db_session, farm, monkeypatch
):
    from backend.app.services.printer_manager import printer_manager

    def epoch(d: datetime) -> float:
        return d.replace(tzinfo=timezone.utc).timestamp()

    # Print A (10:00-11:00) while Printbuddy was off; print B of the same file archived at 11:05.
    after = archive(farm["k2"], "Regalo.gcode", ended=at(12))
    after.started_at = after.created_at = at(11, 5)
    db_session.add(after)
    await db_session.commit()
    jobs = [
        {
            "job_id": "000A",
            "filename": "Regalo.gcode",
            "status": "completed",
            "start_time": epoch(at(10)),
            "end_time": epoch(at(11)),
        },
        {
            "job_id": "000B",
            "filename": "Regalo.gcode",
            "status": "completed",
            "start_time": epoch(at(11, 5)),
            "end_time": epoch(at(12)),
        },
    ]

    class Client:
        def _get(self, path):
            return {"jobs": jobs if "&start=0&" in path else []}

    monkeypatch.setattr(printer_manager, "get_client", lambda pid: Client() if pid == farm["k2"] else None)
    assert await outside.scan_klipper_history(db_session) == 1  # A: recorded; B: Printbuddy has it
    [job] = [j for j in (await async_client.get(f"{API}/jobs")).json() if j["outside_queue"]]
    assert job["finished_at"] == "2026-01-01T11:00:00"


@pytest.mark.asyncio
async def test_one_print_that_always_fails_never_holds_back_the_others(async_client, db_session, farm, monkeypatch):
    from backend.app.services.printer_manager import printer_manager

    record = outside._record

    async def fragile(db, source, printer_id, name, *args):
        if "Rotto" in name:
            raise ValueError("cannot read this one")
        return await record(db, source, printer_id, name, *args)

    monkeypatch.setattr(outside, "_record", fragile)
    db_session.add_all(
        [archive(farm["a1"], "Rotto.3mf", ended=at(10)), archive(farm["a1"], "Gancio.3mf", ended=at(11))]
    )
    await db_session.commit()
    assert await outside.scan_archives(db_session) == 1
    assert await outside.scan_archives(db_session) == 0  # the broken one is tried again, the other kept once
    assert [j["print_name"] for j in (await async_client.get(f"{API}/jobs")).json() if j["outside_queue"]] == [
        "Gancio.3mf"
    ]

    # Klipper: the same, and the cursor stays on the job that failed so it is read again.
    def epoch(d: datetime) -> float:
        return d.replace(tzinfo=timezone.utc).timestamp()

    jobs = [
        {
            "job_id": "0001",
            "filename": "Rotto.gcode",
            "status": "completed",
            "start_time": epoch(at(12)),
            "end_time": epoch(at(12, 30)),
        },
        {
            "job_id": "0002",
            "filename": "Coperchio.gcode",
            "status": "completed",
            "start_time": epoch(at(13)),
            "end_time": epoch(at(13, 30)),
        },
    ]
    open_while_asking = []

    class Client:
        def _get(self, path):
            open_while_asking.append(db_session.in_transaction())
            return {"jobs": jobs if "&start=0&" in path else []}

    monkeypatch.setattr(printer_manager, "get_client", lambda pid: Client() if pid == farm["k2"] else None)
    assert await outside.scan_klipper_history(db_session) == 1
    assert open_while_asking == [False]  # no write transaction held while the printer answers
    from backend.app.api.routes.settings import get_setting

    assert float(await get_setting(db_session, outside.HISTORY_KEY.format(farm["k2"]))) == epoch(at(12))


@pytest.mark.asyncio
async def test_one_printer_failing_never_stops_the_colour_of_the_others(db_session, farm, printing, monkeypatch):
    sample = service._sample_colour

    async def fragile(db, printer_id, *args):
        if printer_id == farm["k2"]:
            raise ValueError("bad status")
        return await sample(db, printer_id, *args)

    monkeypatch.setattr(service, "_sample_colour", fragile)
    running = archive(farm["a1"], "Sopra__abcdef12.3mf", status="printing", ended=at(15))
    db_session.add(running)
    await db_session.commit()
    printing.running.update({farm["a1"], farm["k2"]})
    assert await service.record_used_colors(db_session) == 1
    assert (await db_session.get(PrintArchive, running.id)).extra_data == {service.USED_COLORS_KEY: {"Bianco": 1}}


async def printing_job(db_session, farm, printer: str, filename: str) -> tuple[PrintQueueItem, FarmPrintJob]:
    """Accoda's black 'Sopra' (15 a plate) printing now on ``printer`` from ``filename``."""
    running = archive(farm[printer], filename, status="printing", ended=at(15))
    db_session.add(running)
    await db_session.flush()
    item = PrintQueueItem(printer_id=farm[printer], archive_id=running.id, status="printing")
    db_session.add(item)
    await db_session.flush()
    job = FarmPrintJob(
        component_code=farm["code"],
        color="Nero",
        plate_ids=[farm["plate"]],
        expected_pieces=15,
        status="queued",
        queue_item_id=item.id,
        printer_id=farm[printer],
        plate_id=farm["plate"],
        pieces=15,
    )
    db_session.add(job)
    await db_session.commit()
    return item, job


def excluding(monkeypatch, objects: dict, skipped: list, **file) -> SimpleNamespace:
    """Every printer prints the file (``subtask_name`` for Bambu, ``gcode_file`` for Klipper) with these objects."""
    from backend.app.services.printer_manager import printer_manager

    status = SimpleNamespace(state="RUNNING", printable_objects=objects, skipped_objects=skipped, **file)
    monkeypatch.setattr(printer_manager, "get_status", lambda pid: status)
    return status


async def batch(client, db_session, item, job_id, ended=None) -> dict:
    item.status, item.completed_at = "completed", ended or datetime.now(timezone.utc).replace(tzinfo=None)
    await db_session.commit()
    return next(b for b in await completed(client) if b["id"] == job_id)


def summary(b: dict) -> tuple:
    return b["pieces"], b["pieces_planned"], b["excluded_objects"], b["exclusions"]


@pytest.mark.asyncio
async def test_bambu_skipped_objects_come_off_the_batch(async_client, db_session, farm, monkeypatch):
    item, job = await printing_job(db_session, farm, "a1", "Sopra__abcdef12.3mf")
    copies = {oid: {"name": "sopra", "x": 0, "y": 0} for oid in (437, 448, 470)}
    status = excluding(monkeypatch, copies, [448], subtask_name="Sopra__abcdef12")
    assert await service.record_excluded_objects(db_session) == 1
    assert await service.record_excluded_objects(db_session) == 1  # read twice, subtracted once
    [running] = [j for j in (await async_client.get(f"{API}/jobs")).json() if j["id"] == job.id]
    assert summary(running) == (14, 15, ["sopra"], "read")

    # Printbuddy restarted: the printer still reports its skipped ids, the names come from before.
    status.printable_objects, status.skipped_objects = {}, [448, 470]
    assert await service.record_excluded_objects(db_session) == 1
    assert summary(await batch(async_client, db_session, item, job.id)) == (15, 15, ["sopra", "#470"], "unmatched")

    # The archive 3MF names the one skipped after the restart.
    monkeypatch.setattr(service, "_archive_objects", lambda a: {"470": "sopra"})
    item.status = "printing"
    await db_session.commit()
    assert await service.record_excluded_objects(db_session) == 1
    assert summary(await batch(async_client, db_session, item, job.id)) == (13, 15, ["sopra", "sopra"], "read")


@pytest.mark.asyncio
async def test_klipper_excluded_objects_come_off_the_batch(async_client, db_session, farm, monkeypatch):
    item, job = await printing_job(db_session, farm, "k2", "Sopra__abcdef12.gcode")
    labels = {i: {"name": f"SOPRA_ID_0_COPY_{i}"} for i in range(15)}  # exclude_object upper-cases Orca labels
    status = excluding(monkeypatch, labels, [3, 7, 9], subtask_name=None, gcode_file="Sopra__abcdef12.gcode")
    assert await service.record_excluded_objects(db_session) == 1
    assert await service.queued_pieces(db_session) == {farm["code"]: 12}  # the plan counts what will come out
    status.skipped_objects = [3, 7]  # one restored: excluded no more
    assert await service.record_excluded_objects(db_session) == 1
    excluded = ["SOPRA_ID_0_COPY_3", "SOPRA_ID_0_COPY_7"]
    assert summary(await batch(async_client, db_session, item, job.id)) == (13, 15, excluded, "read")
    # Its end not watched: those read come off all the same, flagged as maybe more.
    late = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
    assert summary(await batch(async_client, db_session, item, job.id, late)) == (13, 15, excluded, "unread")


@pytest.mark.asyncio
async def test_an_excluded_object_not_on_the_plate_leaves_the_plate_pieces(async_client, db_session, farm, monkeypatch):
    item, job = await printing_job(db_session, farm, "k2", "Sopra__abcdef12.gcode")
    labels = {0: {"name": "SOPRA_ID_0_COPY_0"}, 1: {"name": "GANCIO_ID_1_COPY_0"}}
    excluding(monkeypatch, labels, [1], subtask_name=None, gcode_file="Sopra__abcdef12.gcode")
    assert await service.record_excluded_objects(db_session) == 1
    expected = (15, 15, ["GANCIO_ID_1_COPY_0"], "unmatched")
    assert summary(await batch(async_client, db_session, item, job.id)) == expected


@pytest.mark.asyncio
async def test_no_exclusion_keeps_the_plate_pieces(async_client, db_session, farm, monkeypatch):
    item, job = await printing_job(db_session, farm, "a1", "Sopra__abcdef12.3mf")
    excluding(monkeypatch, {437: {"name": "sopra"}}, [], subtask_name="Sopra__abcdef12")
    assert await service.record_excluded_objects(db_session) == 1
    assert summary(await batch(async_client, db_session, item, job.id)) == (15, 15, [], "read")
    # Its end not watched (the last read long before): the plate pieces, flagged.
    late = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
    assert summary(await batch(async_client, db_session, item, job.id, late)) == (15, 15, [], "unread")
    # Printed before exclusions were read: the plate pieces as before, flagged.
    old = {b["id"]: b for b in await completed(async_client)}[farm["queued"]]
    assert summary(old) == (15, 15, None, "unread")


@pytest.mark.asyncio
async def test_an_excluded_part_of_a_set_counts_complete_sets(async_client, db_session, farm, monkeypatch):
    base = (await async_client.post(f"{API}/shapes", json={"name": "Base"})).json()
    plate = await db_session.get(FarmPlate, farm["plate"])
    plate.yields.append(FarmPlateYield(object_name="base", quantity=15, shape_id=base["id"]))
    comp = await db_session.scalar(select(FarmComponent).where(FarmComponent.code == farm["code"]))
    comp.recipe = [{"shape_id": farm["shape"], "quantity": 1}, {"shape_id": base["id"], "quantity": 1}]
    await db_session.commit()
    item, job = await printing_job(db_session, farm, "a1", "Sopra__abcdef12.3mf")
    objects = {1: {"name": "sopra"}, 2: {"name": "sopra"}, 3: {"name": "base"}}
    status = excluding(monkeypatch, objects, [1], subtask_name="Sopra__abcdef12")
    assert await service.record_excluded_objects(db_session) == 1
    assert (await db_session.get(FarmPrintJob, job.id)).printed_pieces == 14  # one base left over
    status.skipped_objects = [1, 3]
    assert await service.record_excluded_objects(db_session) == 1
    assert summary(await batch(async_client, db_session, item, job.id))[:2] == (14, 15)  # still 14 complete sets
    status.skipped_objects = [1, 2, 3]
    item.status = "printing"
    await db_session.commit()
    assert await service.record_excluded_objects(db_session) == 1
    assert summary(await batch(async_client, db_session, item, job.id))[:2] == (13, 15)
