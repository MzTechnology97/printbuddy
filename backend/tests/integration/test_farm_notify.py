"""Printfarm (fork): farm events as notifications (backend/app/farm/notify.py, alerts.py).

Nothing is sent: the autouse ``farm_notifications`` fixture records the events, the provider send
is mocked, and the printers' live state is stubbed. Data is made up.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from backend.app.farm import alerts, notify
from backend.app.farm.jobs.models import FarmPrintJob
from backend.app.farm.library_sync import _Analysis, _DiskEntry, _notify_corrupt
from backend.app.farm.notify import _send as real_send
from backend.app.models.library import LibraryFile
from backend.app.models.notification import NotificationProvider
from backend.app.models.print_queue import PrintQueueItem
from backend.app.services.notification_service import notification_service


async def _settle():
    for task in list(notify._tasks):
        await task


async def test_same_event_is_sent_once_until_forgotten(farm_notifications):
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, printer="X1C", waiting="C0001") is not None
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, printer="X1C", waiting="C0001") is None
    assert notify.notify(notify.PLATE_TO_CLEAR, 2, printer="A1", waiting=None) is not None
    await _settle()
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, printer="X1C", waiting="C0001") is None  # delivered
    notify.forget(notify.PLATE_TO_CLEAR, 1)
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, printer="X1C", waiting="C0002") is not None
    await _settle()
    assert [v["waiting"] for _, v in farm_notifications] == ["C0001", "", "C0002"]


async def test_slicing_failure_once_per_plate_and_colour_and_cleared_by_a_success(farm_notifications):
    error = HTTPException(409, "il colore Nero non è nel 3MF")
    notify.slicing_done(5, "Box.3mf", 2, "Nero", notify.AUTO_RESLICE, error)
    notify.slicing_done(5, "Box.3mf", 2, "nero ", notify.AUTO_RESLICE, error)  # same variant
    notify.slicing_done(5, "Box.3mf", 2, None, notify.QUEUE_SLICE, RuntimeError("sidecar down"))
    await _settle()
    notify.slicing_done(5, "Box.3mf", 2, "Nero", notify.AUTO_RESLICE, error)  # delivered: not again
    notify.slicing_done(5, "Box.3mf", 2, "Nero", notify.AUTO_RESLICE)  # works now
    notify.slicing_done(5, "Box.3mf", 2, "Nero", notify.AUTO_RESLICE, error)
    await _settle()
    assert [(e, v["color"], v["reason"]) for e, v in farm_notifications] == [
        (notify.SLICING_FAILED, "Nero", "il colore Nero non è nel 3MF"),
        (notify.SLICING_FAILED, "", "RuntimeError: sidecar down"),
        (notify.SLICING_FAILED, "Nero", "il colore Nero non è nel 3MF"),
    ]
    assert farm_notifications[0][1]["source"] == "Re-slice automatico"


async def test_corrupt_file_names_its_path_inside_the_share(farm_notifications):
    entry = _DiskEntry("/mnt/share/Progetti/Box.3mf", "Progetti", "Box.3mf", "3mf", 1.0, 10)
    _notify_corrupt(entry, _Analysis(sha256="abc", corrupt="Not a valid 3MF archive"))
    _notify_corrupt(entry, _Analysis(sha256="abc", corrupt="Not a valid 3MF archive"))  # same version
    await _settle()
    assert farm_notifications == [
        (
            notify.FILE_CORRUPT,
            {"filename": "Box.3mf", "path": "Progetti/Box.3mf", "reason": "Not a valid 3MF archive"},
        )
    ]


async def test_send_goes_to_providers_with_the_event_on(db_session):
    db_session.add_all(
        [
            NotificationProvider(name="on", provider_type="telegram", config="{}", on_farm_file_corrupt=True),
            NotificationProvider(name="off", provider_type="telegram", config="{}"),
        ]
    )
    await db_session.commit()

    @asynccontextmanager
    async def session():
        yield db_session

    with (
        patch("backend.app.core.database.async_session", session),
        patch.object(notification_service, "_send_to_providers", AsyncMock()) as send,
    ):
        await real_send(notify.FILE_CORRUPT, {"filename": "Box.3mf", "path": "Box.3mf", "reason": "bad"}, None, None)

    providers, title = send.call_args.args[0], send.call_args.args[1]
    assert [p.name for p in providers] == ["on"]
    assert send.call_args.args[4] == "farm_file_corrupt"
    assert title  # rendered from the template, or the event name when none is seeded


async def test_plate_to_clear_when_farm_work_waits_for_the_printer(db_session, printer_factory, farm_notifications):
    x1c = await printer_factory(name="X1C uno", model="X1C")
    idle = await printer_factory(name="X1C due", model="X1C")
    out = LibraryFile(filename="box_plate1.gcode.3mf", file_path="/x/box.gcode.3mf", file_type="3mf", file_size=1)
    db_session.add(out)
    await db_session.flush()
    item = PrintQueueItem(printer_id=x1c.id, library_file_id=out.id, status="pending")
    db_session.add(item)
    await db_session.flush()
    db_session.add(
        FarmPrintJob(
            component_code="C0001",
            color="Nero",
            plate_ids=[],
            expected_pieces=4,
            status="queued",
            queue_item_id=item.id,
        )
    )
    await db_session.commit()

    awaiting = {x1c.id, idle.id}
    with (
        patch.object(alerts.printer_manager, "is_awaiting_plate_clear", side_effect=lambda pid: pid in awaiting),
        patch.object(alerts.scheduler, "_is_printer_idle", return_value=True),
    ):
        await alerts.plates_to_clear(db_session)
        await alerts.plates_to_clear(db_session)  # still waiting: not again
        awaiting.clear()
        await alerts.plates_to_clear(db_session)  # cleared
        awaiting.add(x1c.id)
        await alerts.plates_to_clear(db_session)  # next finish: again
    await _settle()

    # The second X1C has no farm work waiting: no notification for it.
    assert farm_notifications == [(notify.PLATE_TO_CLEAR, {"printer": "X1C uno", "waiting": "C0001 Nero"})] * 2


async def test_urgent_component_without_a_free_loaded_printer(db_session, monkeypatch, farm_notifications):
    def plate(idle, material_ok, color_ok):
        return {"material": "PETG", "printers": [{"idle": idle, "material_ok": material_ok, "color_ok": color_ok}]}

    rows = [
        {
            "code": "C0001",
            "name": "Box",
            "status": "urgente",
            "to_print": 12,
            "color": "Nero",
            "plates": [plate(1, 1, 0)],
        },
        {
            "code": "C0002",
            "name": "Lid",
            "status": "esaurito",
            "to_print": 3,
            "color": None,
            "plates": [plate(1, 1, 1)],
        },
        {
            "code": "C0003",
            "name": "Pin",
            "status": "da_stampare",
            "to_print": 9,
            "color": "Nero",
            "plates": [plate(0, 0, 0)],
        },
        {"code": "C0004", "name": "Cap", "status": "urgente", "to_print": 2, "color": "Nero", "plates": []},
    ]

    async def fake_plan(db, queued=None, codes=None):
        return {"fetched_at": "2026-01-01T00:00:00Z", "error": None, "components": rows}

    monkeypatch.setattr(alerts.plan, "build_plan", fake_plan)
    monkeypatch.setattr("backend.app.farm.jobs.service.queued_pieces", AsyncMock(return_value={}))
    await alerts.urgent_components(db_session)
    await _settle()

    assert farm_notifications == [
        (
            notify.COMPONENT_URGENT,
            {
                "component": "C0001",
                "name": "Box",
                "status": "urgente",
                "missing": "12",
                "material": "PETG",
                "color": "Nero",
            },
        )
    ]


async def test_provider_api_saves_the_farm_toggles(async_client):
    data = {
        "name": "Farm",
        "provider_type": "ntfy",
        "config": {"topic": "farm-test"},
        "on_farm_plate_to_clear": True,
    }
    created = (await async_client.post("/api/v1/notifications/", json=data)).json()
    assert created["on_farm_plate_to_clear"] is True
    assert created["on_farm_component_urgent"] is False

    updated = await async_client.patch(
        f"/api/v1/notifications/{created['id']}", json={"on_farm_component_urgent": True}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["on_farm_component_urgent"] is True
    assert updated.json()["on_farm_plate_to_clear"] is True


FARM_EVENTS = sorted(v for v in vars(notify).values() if isinstance(v, str) and v.startswith("farm_"))


@pytest.mark.parametrize("event", FARM_EVENTS)
def test_every_farm_event_has_a_toggle_and_a_template(event):
    from backend.app.api.routes.notification_templates import EVENT_NAMES
    from backend.app.models.notification_template import DEFAULT_TEMPLATES
    from backend.app.schemas.notification_template import EVENT_VARIABLES, SAMPLE_DATA

    assert hasattr(NotificationProvider, f"on_{event}")
    assert event in {t["event_type"] for t in DEFAULT_TEMPLATES}
    assert event in EVENT_NAMES and event in EVENT_VARIABLES and event in SAMPLE_DATA


async def test_an_undelivered_event_is_tried_again_and_one_in_flight_is_not_doubled(monkeypatch):
    delivered = []
    gate = asyncio.Event()

    async def send(event, variables, printer_id, printer_name):
        await gate.wait()
        delivered.append(variables["waiting"])
        return variables["waiting"] != "off"  # "off": no provider has the event on

    monkeypatch.setattr(notify, "_send", send)
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, waiting="off") is not None
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, waiting="off") is None  # still being sent
    gate.set()
    await _settle()
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, waiting="on") is not None  # nobody got the first
    await _settle()
    assert notify.notify(notify.PLATE_TO_CLEAR, 1, waiting="again") is None  # delivered: 12 h
    assert delivered == ["off", "on"]


def _plan_row(code, status="urgente", stuck=True):
    printer = {"idle": True, "material_ok": True, "color_ok": not stuck}
    return {
        "code": code,
        "name": code,
        "status": status,
        "to_print": 5,
        "color": "Nero",
        "plates": [{"material": "PLA", "printers": [printer]}],
    }


async def test_urgent_component_leaving_the_plan_is_forgotten(db_session, monkeypatch, farm_notifications):
    plan_answer = {"fetched_at": "2026-01-01T00:00:00Z", "error": None, "components": [_plan_row("C0001")]}

    async def fake_plan(db, queued=None, codes=None):
        return plan_answer

    monkeypatch.setattr(alerts.plan, "build_plan", fake_plan)
    monkeypatch.setattr("backend.app.farm.jobs.service.queued_pieces", AsyncMock(return_value={}))
    await alerts.urgent_components(db_session)
    await _settle()
    plan_answer["components"] = []  # printed enough: out of the plan
    await alerts.urgent_components(db_session)
    plan_answer["components"] = [_plan_row("C0001")]  # urgent again: notified at once
    await alerts.urgent_components(db_session)
    await _settle()
    assert [v["component"] for _, v in farm_notifications] == ["C0001", "C0001"]


async def test_an_old_copy_of_the_plan_is_not_judged(db_session, monkeypatch, farm_notifications):
    async def fake_plan(db, queued=None, codes=None):
        return {
            "fetched_at": "2026-01-01T00:00:00Z",
            "error": "Seller Hub did not answer",
            "components": [_plan_row("C1")],
        }

    monkeypatch.setattr(alerts.plan, "build_plan", fake_plan)
    monkeypatch.setattr("backend.app.farm.jobs.service.queued_pieces", AsyncMock(return_value={}))
    await alerts.urgent_components(db_session)
    await _settle()
    assert farm_notifications == []


async def test_the_check_runs_in_its_own_task_one_at_a_time(monkeypatch):
    gate, runs = asyncio.Event(), []

    async def slow_check(db):
        runs.append(1)
        await gate.wait()

    monkeypatch.setattr(alerts, "check", slow_check)
    monkeypatch.setattr(alerts, "_running", None)
    alerts.start_check()  # returns at once: the dispatcher doesn't wait for Seller Hub
    await asyncio.sleep(0.05)
    alerts.start_check()  # still running: not a second one
    await asyncio.sleep(0.05)
    assert runs == [1]
    alerts.stop_check()
    await asyncio.gather(alerts._running, return_exceptions=True)
    assert alerts._running.cancelled()


async def test_unexpected_auto_reslice_error_is_notified(db_session, monkeypatch, farm_notifications):
    from backend.app.farm import reslice
    from backend.app.farm.models import FarmLibraryFile

    project = LibraryFile(filename="Box.3mf", file_path="/x/Box.3mf", file_type="3mf", file_size=1)
    db_session.add(project)
    await db_session.flush()
    db_session.add(
        FarmLibraryFile(watch_folder_id=1, library_file_id=project.id, path="/x", mtime=1, size=1, sha256="v1")
    )
    await db_session.commit()
    monkeypatch.setattr("backend.app.farm.plates.service.refresh_plates", AsyncMock())
    monkeypatch.setattr("backend.app.farm.slicing.slice_embedded", AsyncMock(side_effect=RuntimeError("disk full")))

    with pytest.raises(RuntimeError):
        await reslice._reslice(db_session, project.id, [(1, None)], job_id=1)
    await _settle()
    assert [(e, v["reason"], v["source"]) for e, v in farm_notifications] == [
        (notify.SLICING_FAILED, "RuntimeError: disk full", notify.AUTO_RESLICE)
    ]
