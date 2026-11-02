"""Printfarm (fork): what to print, from Seller Hub's components, plates and loaded colours. Data is made up."""

import httpx
import pytest

from backend.app.farm.plan import service
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield

API = "/api/v1/farm"

PRINTERS = [
    {"id": 1, "name": "A1 uno", "model": "A1", "idle": True, "loaded": [{"type": "PLA", "color": "161616"}]},
    {"id": 2, "name": "A1 due", "model": "A1", "idle": False, "loaded": [{"type": "PLA", "color": "161616"}]},
    {"id": 3, "name": "P1S", "model": "P1S", "idle": True, "loaded": [{"type": "PLA", "color": "ffffff"}]},
    {"id": 4, "name": "K1", "model": "K1", "idle": True, "loaded": []},
]


def component(code, name, status, to_print, days_left):
    return {
        "code": code,
        "name": name,
        "status": status,
        "to_print": to_print,
        "stock": 0,
        "need": to_print,
        "days_left": days_left,
    }


@pytest.fixture
async def farm(async_client, db_session, monkeypatch):
    """Shape 'Sopra' in black, plates on A1 (15 pcs), P1S (20 pcs) and K1 (10 pcs); Seller Hub answering."""
    shape = (await async_client.post(f"{API}/shapes", json={"name": "Sopra"})).json()
    black = (await async_client.post(f"{API}/components", json={"shape_id": shape["id"], "color": "Nero"})).json()
    for index, (model, pieces) in enumerate([("A1", 15), ("P1S", 20), ("K1", 10)], start=1):
        plate = FarmPlate(library_file_id=40 + index, plate_index=1, printer_model=model, materials=["PLA"])
        plate.status = "confirmed"
        plate.print_time_seconds = 3600
        plate.yields = [FarmPlateYield(object_name="sopra", quantity=pieces, shape_id=shape["id"])]
        db_session.add(plate)
    await db_session.commit()

    plan = {
        "components": [
            component(black["code"], "Sopra – Nero", "urgente", 30, 2.0),
            component("C-0900", "Sconosciuto", "esaurito", 2, 0.0),
            component("C-0901", "Basta", "ok", 0, None),
        ]
    }

    async def fake_get(url):
        assert url == "http://hub.test:8000"
        return plan

    monkeypatch.setattr(service, "_get_print_plan", fake_get)
    monkeypatch.setattr(service, "printer_snapshot", lambda printers: PRINTERS)
    r = await async_client.put(f"{API}/plan/settings", json={"url": "http://hub.test:8000/"})
    assert r.status_code == 200, r.text
    return {"black": black["code"]}


@pytest.mark.asyncio
async def test_most_urgent_first_and_best_plate_first(async_client, farm):
    body = (await async_client.get(f"{API}/plan")).json()
    assert body["error"] is None
    assert [c["code"] for c in body["components"]] == ["C-0900", farm["black"]]  # to_print 0 left out

    unknown, black = body["components"]
    assert (unknown["known"], unknown["plates"]) == (False, [])
    assert (black["color"], black["color_hex"], black["shape"], black["missing"]) == ("Nero", "111111", "Sopra", 30)
    # A1 has a free printer with black PLA; P1S is free but white; K1 can't tell what it has loaded.
    assert [(o["printer_model"], o["score"], o["yield"]) for o in black["plates"]] == [
        ("A1", 2, 15),
        ("P1S", 1, 20),
        ("K1", 1, 10),
    ]
    a1 = black["plates"][0]["printers"]
    assert [(p["name"], p["idle"], p["material_ok"], p["color_ok"]) for p in a1] == [
        ("A1 uno", True, True, True),
        ("A1 due", False, True, True),
    ]
    k1 = black["plates"][2]["printers"][0]
    assert (k1["material_ok"], k1["color_ok"]) == (None, None)


@pytest.mark.asyncio
async def test_seller_hub_down_uses_last_answer(async_client, farm, monkeypatch):
    first = (await async_client.get(f"{API}/plan")).json()

    async def down(url):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(service, "_get_print_plan", down)
    body = (await async_client.get(f"{API}/plan")).json()
    assert "Seller Hub did not answer" in body["error"]
    assert body["fetched_at"] == first["fetched_at"]
    assert [c["code"] for c in body["components"]] == [c["code"] for c in first["components"]]


@pytest.mark.asyncio
async def test_settings_validation_and_colour_table(async_client):
    assert (await async_client.put(f"{API}/plan/settings", json={"url": "ftp://x"})).status_code == 422
    assert (await async_client.put(f"{API}/plan/settings", json={"colors": {"Rosa": "zz"}})).status_code == 422
    r = await async_client.put(
        f"{API}/plan/settings", json={"url": "", "tolerance": 50, "colors": {"Rosa": "#FF99CC", "nero": "111111"}}
    )
    body = r.json()
    assert (body["url"], body["tolerance"]) == ("", 50)
    assert body["colors"]["rosa"] == "ff99cc"
    assert body["colors"]["nero"] == "111111"
    assert (await async_client.get(f"{API}/plan")).json()["error"] == "Seller Hub URL not set"


def test_colour_tolerance():
    near_black = {"id": 1, "name": "x", "idle": True, "loaded": [{"type": "PLA", "color": "161616"}]}
    assert service.printer_fit(near_black, ["PLA"], "111111", 80)["color_ok"] is True
    assert service.printer_fit(near_black, ["PLA"], "f5f5f5", 80)["color_ok"] is False
    # Black loaded but in another material does not count.
    assert service.printer_fit(near_black, ["PETG"], "111111", 80)["material_ok"] is False
