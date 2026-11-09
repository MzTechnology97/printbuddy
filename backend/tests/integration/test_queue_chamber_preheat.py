"""Chamber preheat before queued warm-chamber prints (port of upstream #1468): ``services/queue_preheat``.

No printer is contacted: the printer manager, its clients, uploads and notifications are stubbed. Data is made up.
"""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.app.models.library import LibraryFile
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.settings import Settings
from backend.app.services import print_scheduler as ps, queue_preheat as qp
from backend.app.services.print_scheduler import PrintScheduler


class FakeClient:
    """Records the commands, as the MQTT client's own methods name them (M140/M141 G-code, set_airduct)."""

    def __init__(self):
        self.sent: list[str] = []

    def set_bed_temperature(self, target):
        self.sent.append(f"M140 S{target}")
        return True

    def set_chamber_temperature(self, target):
        self.sent.append(f"M141 S{target}")
        return True

    def set_airduct_mode(self, mode):
        self.sent.append(f"airduct {mode}")
        return True


class Rig:
    """The printers as the scheduler sees them: state, chamber reading, airduct, idle (plate clear), material."""

    def __init__(self):
        self.status: dict[int, SimpleNamespace] = {}
        self.clients: dict[int, FakeClient] = {}
        self.not_idle: set[int] = set()
        self.material = "ABS"
        self.tried: list[int] = []

    def add(self, printer, chamber=25.0, airduct=0):
        self.status[printer.id] = SimpleNamespace(
            state="IDLE", subtask_id=None, gcode_file=None, temperatures={"chamber": chamber}, airduct_mode=airduct
        )
        self.clients[printer.id] = FakeClient()

    def sent(self, printer):
        return self.clients[printer.id].sent


async def tick(test_engine, scheduler, rig: Rig):
    """One real ``check_queue`` (and ``_start_print`` when it starts)."""

    async def upload(**kwargs):
        rig.tried.append(kwargs["printer_id"])
        return True

    async def requirements(db, item):
        return [{"slot_id": 1, "type": rig.material}]

    async def same_name(name, path):
        return name

    pm = MagicMock()
    pm.is_connected.return_value = True
    pm.get_status.side_effect = lambda pid: rig.status.get(pid)
    pm.get_client.side_effect = lambda pid: rig.clients.get(pid)
    pm.start_print.return_value = True
    sessions = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    with (
        patch.object(ps, "async_session", sessions),
        patch.object(ps, "printer_manager", pm),
        patch.object(qp, "printer_manager", pm),
        patch.object(ps, "notification_service") as notify,
        patch.object(ps, "get_ftp_retry_settings", AsyncMock(return_value=(False, 1, 1, 10))),
        patch.object(ps, "delete_file_async", AsyncMock()),
        patch.object(ps.farm_printer_files, "fingerprint", same_name),
        patch("backend.app.services.archive.ArchiveService.archive_print", AsyncMock(return_value=None)),
        patch.object(scheduler, "_is_printer_idle", MagicMock(side_effect=lambda pid, *a: pid not in rig.not_idle)),
        patch.object(scheduler, "_check_auto_drying", AsyncMock()),
        patch.object(scheduler, "_compute_ams_mapping_for_printer", AsyncMock(return_value=[3])),
        patch.object(scheduler, "_get_filament_requirements", requirements),
        patch.object(scheduler, "_block_on_filament_deficit", AsyncMock(return_value=False)),
        patch.object(scheduler, "_delete_remote_file_for_provider", AsyncMock()),
        patch.object(scheduler, "_upload_file_for_provider", upload),
        patch.object(scheduler, "_power_off_if_needed", AsyncMock()),
        patch.object(scheduler, "_mark_printer_dispatched", MagicMock()),
        patch.object(scheduler, "_watchdog_print_start", AsyncMock()),
    ):
        for name in ("on_queue_job_failed", "on_queue_job_started", "on_queue_job_waiting", "on_queue_job_assigned"):
            setattr(notify, name, AsyncMock())
        await scheduler.check_queue()
    return notify


async def enable(db_session, **values):
    values = {"preheat_enabled": "true", **values}
    for key, value in values.items():
        db_session.add(Settings(key=key, value=value))
    await db_session.commit()


async def queue_item(db_session, tmp_path, printer=None, position=1, **kwargs):
    path = tmp_path / f"part{position}.gcode.3mf"
    path.write_bytes(b"3mf")
    lib = LibraryFile(filename=path.name, file_path=str(path), file_type="3mf", file_size=3)
    db_session.add(lib)
    await db_session.flush()
    item = PrintQueueItem(
        printer_id=printer.id if printer else None,
        library_file_id=lib.id,
        position=position,
        status="pending",
        **kwargs,
    )
    db_session.add(item)
    await db_session.commit()
    return item


async def refreshed(db_session, item):
    await db_session.refresh(item)
    return item


@pytest.fixture
async def x1c(db_session, printer_factory, tmp_path):
    printer = await printer_factory(name="X1C", model="X1C")
    rig = Rig()
    rig.add(printer)
    return SimpleNamespace(printer=printer, rig=rig, item=await queue_item(db_session, tmp_path, printer))


# ---------------------------------------------------------------- materials and targets


def test_targets_and_material_lookup():
    targets = qp.parse_targets("ABS=45, asa: 45; PA-CF = 55 junk")
    assert targets == {"ABS": 45, "ASA": 45, "PA-CF": 55}
    assert qp.chamber_target(["PLA Basic"], targets) == 0
    assert qp.chamber_target(["PLA", "ABS"], targets) == 45  # the highest of the plate's filaments
    assert qp.chamber_target(["ABS-GF"], targets) == 45  # a variant takes its base material
    assert qp.chamber_target(["PA-CF"], targets) == 55
    assert qp.chamber_target(["", None], targets) == 0
    assert qp.parse_targets(qp.DEFAULT_TARGETS) == {"ABS": 45, "ASA": 45}


# ---------------------------------------------------------------- off by default


@pytest.mark.asyncio
async def test_off_by_default_an_abs_print_starts_at_once(db_session, test_engine, x1c):
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.tried == [x1c.printer.id]
    assert (await refreshed(db_session, x1c.item)).status == "printing"
    assert x1c.rig.sent(x1c.printer) == []
    assert not scheduler.preheat.active


# ---------------------------------------------------------------- passive chamber (X1C)


@pytest.mark.asyncio
async def test_x1c_abs_heats_the_bed_waits_for_the_chamber_then_starts(db_session, test_engine, x1c, tmp_path):
    await enable(db_session)
    later = await queue_item(db_session, tmp_path, x1c.printer, position=2)
    scheduler = PrintScheduler()

    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.tried == []  # not sent yet
    assert x1c.rig.sent(x1c.printer) == ["M140 S90"]  # X1C: no chamber heater, no airduct flap
    item = await refreshed(db_session, x1c.item)
    assert item.status == "pending"
    assert item.waiting_reason == "Preheating chamber: 25/45 °C, up to 15 min"
    assert (await refreshed(db_session, later)).status == "pending"  # the printer is busy for its other jobs

    x1c.rig.status[x1c.printer.id].temperatures["chamber"] = 38.4
    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.tried == []
    assert (await refreshed(db_session, x1c.item)).waiting_reason == "Preheating chamber: 38/45 °C, up to 15 min"
    assert x1c.rig.sent(x1c.printer) == ["M140 S90"]  # heated once, not on every tick

    x1c.rig.status[x1c.printer.id].temperatures["chamber"] = 43.5  # within 2 °C
    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.tried == [x1c.printer.id]
    item = await refreshed(db_session, x1c.item)
    assert (item.status, item.waiting_reason) == ("printing", None)
    assert x1c.rig.sent(x1c.printer) == ["M140 S90"]  # the print keeps the heat
    assert not scheduler.preheat.active
    assert (await refreshed(db_session, later)).status == "pending"


@pytest.mark.asyncio
async def test_timeout_starts_the_print_anyway(db_session, test_engine, x1c):
    await enable(db_session, preheat_max_wait_minutes="5")
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, x1c.rig)
    assert (await refreshed(db_session, x1c.item)).waiting_reason.endswith("up to 5 min")

    scheduler.preheat.active[x1c.printer.id].deadline = time.monotonic() - 1
    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.tried == [x1c.printer.id]
    assert (await refreshed(db_session, x1c.item)).status == "printing"


@pytest.mark.asyncio
async def test_chamber_already_warm_starts_at_once(db_session, test_engine, x1c):
    await enable(db_session)
    x1c.rig.status[x1c.printer.id].temperatures["chamber"] = 46.0
    await tick(test_engine, PrintScheduler(), x1c.rig)
    assert x1c.rig.tried == [x1c.printer.id]
    assert x1c.rig.sent(x1c.printer) == []


@pytest.mark.asyncio
async def test_pla_has_no_preheat(db_session, test_engine, x1c):
    await enable(db_session)
    x1c.rig.material = "PLA"
    await tick(test_engine, PrintScheduler(), x1c.rig)
    assert x1c.rig.tried == [x1c.printer.id]
    assert x1c.rig.sent(x1c.printer) == []


@pytest.mark.asyncio
async def test_open_and_klipper_printers_are_never_preheated(db_session, test_engine, printer_factory, tmp_path):
    await enable(db_session)
    rig = Rig()
    a1 = await printer_factory(name="A1", model="A1")
    klipper = await printer_factory(name="Voron", model="X1C", provider="klipper")
    for n, printer in enumerate((a1, klipper), start=1):
        rig.add(printer)
        await queue_item(db_session, tmp_path, printer, position=n)
    scheduler = PrintScheduler()
    with patch.object(scheduler, "_start_print", AsyncMock()) as start:
        await tick(test_engine, scheduler, rig)
    assert start.await_count == 2
    assert rig.sent(a1) == [] and rig.sent(klipper) == []


# ---------------------------------------------------------------- active chamber heater (H2D) and P1S


@pytest.mark.asyncio
async def test_h2d_turns_on_its_chamber_heater_and_airduct(db_session, test_engine, printer_factory, tmp_path):
    await enable(db_session, preheat_targets="ABS=50")
    h2d = await printer_factory(name="H2D", model="H2D")
    rig = Rig()
    rig.add(h2d, airduct=0)
    item = await queue_item(db_session, tmp_path, h2d)
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, rig)
    assert rig.sent(h2d) == ["M140 S90", "airduct heating", "M141 S50"]
    assert (await refreshed(db_session, item)).waiting_reason == "Preheating chamber: 25/50 °C, up to 15 min"

    rig.status[h2d.id].temperatures["chamber"] = 49
    await tick(test_engine, scheduler, rig)
    assert rig.tried == [h2d.id]

    # The next print, PLA, gets the flap back as it was before the preheat.
    item.status = "completed"
    await db_session.commit()
    rig.material = "PLA"
    await queue_item(db_session, tmp_path, h2d, position=2)
    with patch.object(scheduler, "_start_print", AsyncMock()):
        await tick(test_engine, scheduler, rig)
    assert rig.sent(h2d)[-1] == "airduct cooling"


@pytest.mark.asyncio
async def test_p1s_without_sensor_holds_for_the_whole_wait(db_session, test_engine, printer_factory, tmp_path):
    await enable(db_session, preheat_max_wait_minutes="10")
    p1s = await printer_factory(name="P1S", model="P1S")
    rig = Rig()
    rig.add(p1s, chamber=60.0)  # a P1S reports a chamber value with no sensor behind it: not read
    item = await queue_item(db_session, tmp_path, p1s)
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, rig)
    assert rig.tried == [] and rig.sent(p1s) == ["M140 S90"]
    assert (await refreshed(db_session, item)).waiting_reason == (
        "Preheating chamber (no sensor): bed at 90 °C for 10 min more"
    )
    scheduler.preheat.active[p1s.id].deadline = time.monotonic() - 1
    await tick(test_engine, scheduler, rig)
    assert rig.tried == [p1s.id]


# ---------------------------------------------------------------- stopping


@pytest.mark.asyncio
async def test_cancel_during_preheat_stops_and_turns_the_heaters_off(
    db_session, test_engine, printer_factory, tmp_path
):
    await enable(db_session)
    h2d = await printer_factory(name="H2D", model="H2D")
    rig = Rig()
    rig.add(h2d)
    item = await queue_item(db_session, tmp_path, h2d)
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, rig)
    assert h2d.id in scheduler.preheat.active

    item.status = "cancelled"
    await db_session.commit()
    await tick(test_engine, scheduler, rig)
    assert rig.sent(h2d)[3:] == ["M140 S0", "M141 S0", "airduct cooling"]
    assert not scheduler.preheat.active and rig.tried == []


@pytest.mark.asyncio
async def test_plate_check_reset_or_offline_stops_the_wait(db_session, test_engine, x1c):
    await enable(db_session)
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.printer.id in scheduler.preheat.active

    x1c.rig.not_idle.add(x1c.printer.id)  # awaiting plate-clear again (or offline): not idle
    await tick(test_engine, scheduler, x1c.rig)
    item = await refreshed(db_session, x1c.item)
    assert (item.status, item.waiting_reason) == ("pending", None)
    assert x1c.rig.sent(x1c.printer) == ["M140 S90", "M140 S0"]
    assert not scheduler.preheat.active

    # Cleared again: it preheats again from the start.
    x1c.rig.not_idle.clear()
    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.sent(x1c.printer)[-1] == "M140 S90"


@pytest.mark.asyncio
async def test_no_heater_off_to_a_printer_that_started_printing(db_session, test_engine, x1c):
    await enable(db_session)
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, x1c.rig)
    x1c.rig.status[x1c.printer.id].state = "RUNNING"  # started from its screen meanwhile
    x1c.rig.not_idle.add(x1c.printer.id)
    await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.sent(x1c.printer) == ["M140 S90"]
    assert not scheduler.preheat.active


@pytest.mark.asyncio
async def test_refused_upload_after_preheat_turns_the_heat_off(db_session, test_engine, x1c):
    await enable(db_session)
    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, x1c.rig)
    x1c.rig.status[x1c.printer.id].temperatures["chamber"] = 45

    async def refused(db, item):  # as _hold_after_refused_upload leaves it
        item.waiting_reason = f"did not take the file. {ps.UPLOAD_REFUSED_MARK}"

    with patch.object(scheduler, "_start_print", refused):
        await tick(test_engine, scheduler, x1c.rig)
    assert x1c.rig.sent(x1c.printer) == ["M140 S90", "M140 S0"]
    assert not scheduler.preheat.active


# ---------------------------------------------------------------- "any model" jobs and farm jobs


@pytest.mark.asyncio
async def test_model_job_keeps_its_printer_while_preheating(db_session, test_engine, printer_factory, tmp_path):
    await enable(db_session)
    one = await printer_factory(name="X1C one", model="X1C")
    two = await printer_factory(name="X1C two", model="X1C")
    rig = Rig()
    rig.add(one)
    rig.add(two)
    item = await queue_item(db_session, tmp_path, target_model="X1C")
    scheduler = PrintScheduler()

    notify = await tick(test_engine, scheduler, rig)
    assert rig.sent(one) == ["M140 S90"] and rig.sent(two) == []
    notify.on_queue_job_assigned.assert_awaited_once()
    item = await refreshed(db_session, item)
    assert (item.status, item.printer_id) == ("pending", None)
    assert item.waiting_reason.startswith("Preheating chamber: 25/45")
    assert scheduler.preheat.printers() == {one.id}

    notify = await tick(test_engine, scheduler, rig)
    notify.on_queue_job_assigned.assert_not_awaited()
    assert rig.sent(two) == []  # not moved to the other printer

    rig.status[one.id].temperatures["chamber"] = 45
    await tick(test_engine, scheduler, rig)
    assert rig.tried == [one.id]
    item = await refreshed(db_session, item)
    assert (item.status, item.printer_id) == ("printing", one.id)


@pytest.mark.asyncio
async def test_farm_job_preheats_and_its_printer_takes_no_other_job(async_client, db_session, test_engine, farm):
    from backend.app.farm.jobs import service
    from backend.app.farm.jobs.models import FarmPrintJob
    from backend.app.models.printer import Printer
    from backend.tests.integration.test_farm_jobs import accoda

    await enable(db_session)
    sliced = await db_session.get(LibraryFile, (await db_session.execute(LibraryFile.active())).scalars().first().id)
    sliced.file_path = str(farm["sliced_path"])
    await db_session.commit()
    job = await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 1
    row = await db_session.get(FarmPrintJob, job["id"])
    item = await db_session.get(PrintQueueItem, row.queue_item_id)
    printer = await db_session.get(Printer, row.printer_id)
    printer.model = "P2S"  # made-up: the farm fixture's printers are A1s; an enclosed one here
    await db_session.commit()
    rig = Rig()
    for pid in farm["printers"]:
        rig.add(SimpleNamespace(id=pid))

    scheduler = PrintScheduler()
    await tick(test_engine, scheduler, rig)
    assert rig.tried == []
    assert rig.sent(printer) == ["M140 S90", "airduct heating"]  # P2S: flap, no heater
    item = await refreshed(db_session, item)
    assert item.status == "pending" and item.waiting_reason.startswith("Preheating chamber")
    board = (await async_client.get("/api/v1/farm/board")).json()
    assert any((r.get("status_text") or "").startswith("Preheating chamber") for r in _rows(board))

    # A second job does not go to the preheating printer.
    second = await accoda(async_client, farm["code"], farm["plates"])
    with patch.object(service, "scheduler", scheduler):
        assert await service.dispatch_once(db_session) == 1
    assert (await db_session.get(FarmPrintJob, second["id"])).printer_id != printer.id

    rig.status[printer.id].temperatures["chamber"] = 44
    await tick(test_engine, scheduler, rig)
    assert printer.id in rig.tried
    assert (await refreshed(db_session, item)).status == "printing"


def _rows(board):
    """Every row of the board answer, wherever it lists them."""
    found = []

    def walk(value):
        if isinstance(value, dict):
            if "status_text" in value:
                found.append(value)
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(board)
    return found


@pytest.mark.asyncio
async def test_farm_does_not_place_a_job_on_a_printer_preheating_for_a_model_job(
    async_client, db_session, farm, monkeypatch
):
    from backend.app.farm.jobs import service
    from backend.app.farm.jobs.models import FarmPrintJob
    from backend.tests.integration.test_farm_jobs import accoda

    a1_one, a1_two, _ = farm["printers"]
    monkeypatch.setitem(
        service.scheduler.preheat.active, a1_one, qp.Preheat(999, 45, time.monotonic() + 600, True, False, None)
    )
    job = await accoda(async_client, farm["code"], farm["plates"])
    assert await service.dispatch_once(db_session) == 1
    assert (await db_session.get(FarmPrintJob, job["id"])).printer_id == a1_two


# ---------------------------------------------------------------- settings


@pytest.mark.asyncio
async def test_settings_default_off_and_round_trip(async_client):
    got = (await async_client.get("/api/v1/settings/")).json()
    assert (got["preheat_enabled"], got["preheat_targets"], got["preheat_max_wait_minutes"]) == (
        False,
        "ABS=45, ASA=45",
        15,
    )
    r = await async_client.put(
        "/api/v1/settings/",
        json={"preheat_enabled": True, "preheat_targets": "ABS=50", "preheat_max_wait_minutes": 20},
    )
    assert r.status_code == 200, r.text
    got = (await async_client.get("/api/v1/settings/")).json()
    assert (got["preheat_enabled"], got["preheat_targets"], got["preheat_max_wait_minutes"]) == (True, "ABS=50", 20)
    assert (await async_client.put("/api/v1/settings/", json={"preheat_max_wait_minutes": 0})).status_code == 422


from backend.tests.integration.test_farm_jobs import farm  # noqa: E402, F401  the farm fixture
