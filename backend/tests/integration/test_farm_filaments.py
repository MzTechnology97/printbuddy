"""Printfarm (fork): filament catalog per printer model, from the slicer profiles. Data is made up."""

import pytest

from backend.app.farm.plan import filaments
from backend.app.farm.plates.models import FarmPrinterModelMap
from backend.app.farm.plates.printer_map import PrinterModelResolver

API = "/api/v1/farm"


def entry(vendor, name, ftype):
    return {"vendor": vendor, "filament": name, "type": ftype, "sources": ["system"], "slicers": ["OrcaSlicer"],
            "presets": [f"{name} @X"], "compatible_printers": ["X 0.4 nozzle"]}  # fmt: skip


CATALOG = {
    "Bambu Lab P2S": [entry("Bambu Lab", "Bambu PLA Basic", "PLA")],
    "Creality K1": [entry("Generic", "Generic PETG", "PETG"), entry("SUNLU", "SUNLU PLA", "pla")],
    "Creality K2 Pro": [entry("Generic", "Generic ABS", "ABS")],
}


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(filaments, "catalog", lambda: CATALOG)


@pytest.mark.asyncio
async def test_catalog_per_printer_model(async_client, db_session, printer_factory, catalog):
    await printer_factory(name="P2S", model="N7")  # how Printbuddy may store a P2S
    await printer_factory(name="K2", model="K2")
    db_session.add(FarmPrinterModelMap(source_model="creality k2 pro", target_model="K2"))
    await db_session.commit()

    full = (await async_client.get(f"{API}/filament-catalog")).json()
    assert [(m["model"], m["printer_model"], len(m["filaments"])) for m in full] == [
        ("Bambu Lab P2S", "N7", 1),
        ("Creality K1", None, 2),  # no K1 in this farm
        ("Creality K2 Pro", "K2", 1),
    ]
    for model, key in (("N7", "Bambu Lab P2S"), ("K2", "Creality K2 Pro"), ("K1", "Creality K1")):
        r = await async_client.get(f"{API}/filament-catalog", params={"model": model})
        assert [m["model"] for m in r.json()] == [key], model
    assert (await async_client.get(f"{API}/filament-catalog", params={"model": "Ender-3"})).status_code == 404


def test_hand_spool_offers_the_model_materials(catalog):
    resolver = PrinterModelResolver({}, ["K1"])
    assert filaments.materials("K1", resolver) == ["PETG", "PLA"]
    assert filaments.materials("Sovol Zero", resolver) == []


def test_shipped_catalog_has_every_farm_model():
    shipped = filaments.catalog()
    assert {"Bambu Lab P2S", "Creality K1", "Creality K2 Pro", "Sovol SV08", "Sovol Zero"} <= shipped.keys()
    assert all(e["vendor"] and e["filament"] and e["type"] for entries in shipped.values() for e in entries)
