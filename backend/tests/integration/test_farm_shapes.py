"""Printfarm (fork): components as shape + colour, plates yielding shapes, shape proposals.

All names are made up or generic.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import Base
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.plates.service import object_key, suggest_shapes

API = "/api/v1/farm"


async def post(client, path: str, body: dict) -> dict:
    r = await client.post(API + path, json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def confirmed_plate(db, plate_index: int, yields: list[tuple[str, int, int | None]], file_id: int = 42):
    plate = FarmPlate(library_file_id=file_id, plate_index=plate_index, printer_model="A1", materials=["PLA"])
    plate.status = "confirmed"
    plate.yields = [FarmPlateYield(object_name=n, quantity=q, shape_id=s) for n, q, s in yields]
    db.add(plate)
    await db.commit()
    return plate


class TestShapesAndComponents:
    @pytest.mark.asyncio
    async def test_component_is_shape_plus_colour(self, async_client):
        top = await post(async_client, "/shapes", {"name": "Tartufo sopra"})
        assert (await async_client.post(f"{API}/shapes", json={"name": "tartufo SOPRA"})).status_code == 409

        black = await post(async_client, "/components", {"shape_id": top["id"], "color": "Nero"})
        assert black == {
            "code": "C-0001",
            "name": "Tartufo sopra – Nero",
            "shape_id": top["id"],
            "shape": "Tartufo sopra",
            "color": "Nero",
            "retired": False,
        }
        # One active component per shape and colour; another colour is a new component.
        r = await async_client.post(f"{API}/components", json={"shape_id": top["id"], "color": "nero"})
        assert r.status_code == 409
        white = await post(async_client, "/components", {"shape_id": top["id"], "color": "Bianco"})
        assert white["code"] == "C-0002"
        r = await async_client.post(f"{API}/components", json={"shape_id": 999, "color": "Nero"})
        assert r.status_code == 422
        assert (await async_client.post(f"{API}/components", json={})).status_code == 422

    @pytest.mark.asyncio
    async def test_retired_component_keeps_code_and_leaves_new_parts(self, async_client):
        old = await post(async_client, "/components", {"name": "Set di 2 intero"})
        r = await async_client.patch(f"{API}/components/{old['code']}", json={"retired": True})
        assert r.json()["retired"] is True
        r = await async_client.post(f"{API}/products", json={"name": "X", "parts": [{"component_code": old["code"]}]})
        assert r.status_code == 422
        # Still listed (Seller Hub may hold stock under it) and never handed out again.
        listed = (await async_client.get(f"{API}/components")).json()
        assert [(c["code"], c["retired"]) for c in listed] == [(old["code"], True)]
        assert (await post(async_client, "/components", {"name": "Nuovo"}))["code"] == "C-0002"

    @pytest.mark.asyncio
    async def test_component_made_before_shapes_takes_a_shape_and_keeps_its_code(self, async_client, db_session):
        case = await post(async_client, "/shapes", {"name": "Case 18650"})
        old = await post(async_client, "/components", {"name": "Contenitore doppio 18650"})
        await post(async_client, "/products", {"name": "Contenitore", "parts": [{"component_code": old["code"]}]})
        await confirmed_plate(db_session, 1, [("Case.stl", 8, case["id"])])

        r = await async_client.patch(f"{API}/components/{old['code']}", json={"shape_id": case["id"]})
        assert r.status_code == 200, r.text
        assert (r.json()["code"], r.json()["name"], r.json()["shape"], r.json()["color"]) == (
            old["code"],
            "Contenitore doppio 18650",
            "Case 18650",
            None,
        )
        part = (await async_client.get(f"{API}/products")).json()["products"][0]["parts"][0]
        assert [(x["plate"], x["yield"]) for x in part["plates"]] == [(1, 8)]

        # Same rules as a new component: shape must exist, one active component per shape and colour.
        other = await post(async_client, "/components", {"name": "Tappo"})
        assert (await async_client.patch(f"{API}/components/{other['code']}", json={"shape_id": 999})).status_code == 422
        r = await async_client.patch(f"{API}/components/{other['code']}", json={"shape_id": case["id"]})
        assert r.status_code == 409
        r = await async_client.patch(f"{API}/components/{other['code']}", json={"shape_id": case["id"], "color": "Nero"})
        assert r.json()["color"] == "Nero"
        # Renaming alone leaves the shape; null unlinks it.
        r = await async_client.patch(f"{API}/components/{old['code']}", json={"name": "Contenitore"})
        assert r.json()["shape_id"] == case["id"]
        r = await async_client.patch(f"{API}/components/{old['code']}", json={"shape_id": None})
        assert r.json()["shape_id"] is None

    @pytest.mark.asyncio
    async def test_merge_one_piece_under_two_codes(self, async_client, db_session):
        single = await post(async_client, "/components", {"name": "Contenitore"})
        double = await post(async_client, "/components", {"name": "Contenitore (2)"})
        cap = await post(async_client, "/components", {"name": "Tappo"})
        await post(async_client, "/products", {"name": "Da 1", "parts": [
            {"component_code": single["code"]}, {"component_code": cap["code"], "quantity": 2}]})
        await post(async_client, "/products", {"name": "Da 2", "parts": [
            {"component_code": double["code"], "quantity": 2}, {"component_code": cap["code"], "quantity": 4}]})
        await post(async_client, "/products", {"name": "Misto", "parts": [
            {"component_code": single["code"]}, {"component_code": double["code"], "quantity": 3}]})
        plate = FarmPlate(library_file_id=7, plate_index=1, printer_model="A1", materials=["PLA"], status="confirmed")
        plate.yields = [FarmPlateYield(object_name="Case", quantity=8, component_code=double["code"])]
        db_session.add(plate)
        await db_session.commit()

        r = await async_client.post(f"{API}/components/{double['code']}/merge", json={"into": single["code"]})
        # products: those now using the kept code
        assert r.json() == {"merged": double["code"], "into": single["code"], "products": 3}
        products = {p["name"]: p for p in (await async_client.get(f"{API}/products")).json()["products"]}
        lines = {name: [(x["component_code"], x["quantity"]) for x in p["parts"]] for name, p in products.items()}
        assert lines["Da 2"] == [(single["code"], 2), (cap["code"], 4)]
        assert lines["Misto"] == [(single["code"], 4)]
        assert [(x["plate"], x["yield"]) for x in products["Da 2"]["parts"][0]["plates"]] == [(1, 8)]
        listed = {c["code"]: c["retired"] for c in (await async_client.get(f"{API}/components")).json()}
        assert listed[double["code"]] is True

        # Into itself or into a retired code: refused; unknown code: 404.
        bad = [(single["code"], single["code"]), (cap["code"], double["code"])]
        for code, into in bad:
            r = await async_client.post(f"{API}/components/{code}/merge", json={"into": into})
            assert r.status_code == 422
        r = await async_client.post(f"{API}/components/C-9999/merge", json={"into": single["code"]})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_plates_summary_per_file(async_client, db_session):
    await confirmed_plate(db_session, 1, [("sopra", 15, None)], file_id=42)
    await confirmed_plate(db_session, 2, [("sopra", 2, None)], file_id=42)
    db_session.add(FarmPlate(library_file_id=43, plate_index=1, printer_model="A1", materials=["PLA"]))
    await db_session.commit()
    rows = (await async_client.get(f"{API}/plates/summary")).json()
    assert sorted(rows, key=lambda r: r["library_file_id"]) == [
        {"library_file_id": 42, "proposed": 0, "confirmed": 2},
        {"library_file_id": 43, "proposed": 1, "confirmed": 0},
    ]


class TestPlatesByShape:
    @pytest.mark.asyncio
    async def test_one_plate_serves_every_colour(self, async_client, db_session):
        top = await post(async_client, "/shapes", {"name": "Tartufo sopra"})
        bottom = await post(async_client, "/shapes", {"name": "Tartufo sotto"})
        codes = {}
        for shape in (top, bottom):
            for color in ("Nero", "Bianco"):
                c = await post(async_client, "/components", {"shape_id": shape["id"], "color": color})
                codes[shape["name"], color] = c["code"]
        for color in ("Nero", "Bianco"):
            await post(
                async_client,
                "/products",
                {
                    "name": f"Set di 2 {color}",
                    "parts": [
                        {"component_code": codes["Tartufo sopra", color], "quantity": 2},
                        {"component_code": codes["Tartufo sotto", color], "quantity": 2},
                    ],
                },
            )
        await confirmed_plate(db_session, 1, [("sopra", 15, top["id"]), ("sotto", 15, bottom["id"])])
        await confirmed_plate(db_session, 2, [("sopra", 2, top["id"]), ("sotto (1)", 2, bottom["id"])])

        products = (await async_client.get(f"{API}/products")).json()["products"]
        assert len(products) == 2
        for p in products:
            for part in p["parts"]:
                assert part["quantity"] == 2
                assert [(x["plate"], x["yield"]) for x in part["plates"]] == [(1, 15), (2, 2)]


class TestShapeProposals:
    @pytest.mark.parametrize(
        ("name", "key"),
        [
            ("Perno Maniglia Grande -1 -1", "perno maniglia grande"),
            ("Trurffle_sotto (1) (1)", "trurffle sotto"),
            ("Maniglia-Mezza luna 1", "maniglia mezza luna 1"),
            ("Maniglia Portiera grande 2", "maniglia portiera grande 2"),
            ("Hook.stl", "hook"),
        ],
    )
    def test_object_key(self, name, key):
        assert object_key(name) == key

    @pytest.mark.asyncio
    async def test_proposals_from_confirmed_names_and_shape_names(self, async_client, db_session):
        top = await post(async_client, "/shapes", {"name": "Tartufo sopra"})
        bottom = await post(async_client, "/shapes", {"name": "Tartufo sotto"})
        big = await post(async_client, "/shapes", {"name": "Maniglia Portiera grande"})
        big2 = await post(async_client, "/shapes", {"name": "Maniglia Portiera grande 2"})
        pin = await post(async_client, "/shapes", {"name": "Perno Maniglia Grande"})
        await confirmed_plate(
            db_session, 1, [("Trurffle_sopra (1) (1)", 15, top["id"]), ("Trurffle_sotto", 15, bottom["id"])]
        )

        found = await suggest_shapes(
            db_session,
            [
                "Trurffle_sopra k2",  # extra word: still the confirmed "trurffle sopra"
                "Trurffle_sotto (1) (1)",
                "Perno Maniglia Grande -1 -1",
                "Maniglia Portiera grande 2",  # its own shape, not "grande" + a number
                "Maniglia Portiera grande",
                "Coperchio",
            ],
        )
        assert found == {
            "Trurffle_sopra k2": top["id"],
            "Trurffle_sotto (1) (1)": bottom["id"],
            "Perno Maniglia Grande -1 -1": pin["id"],
            "Maniglia Portiera grande 2": big2["id"],
            "Maniglia Portiera grande": big["id"],
        }

    @pytest.mark.asyncio
    async def test_confirm_with_shapes_through_the_api(self, async_client, db_session):
        top = await post(async_client, "/shapes", {"name": "Tartufo sopra"})
        await confirmed_plate(db_session, 1, [("Trurffle_sopra", 15, top["id"])])
        draft = FarmPlate(library_file_id=43, plate_index=1, printer_model="A1", materials=["PLA"])
        draft.yields = [FarmPlateYield(object_name="Trurffle_sopra k2", quantity=4)]
        db_session.add(draft)
        await db_session.commit()

        listed = (await async_client.get(f"{API}/plates?status=proposed")).json()
        assert listed[0]["yields"][0]["suggested_shape_id"] == top["id"]

        body = {"yields": [{"object_name": "Trurffle_sopra k2", "quantity": 4, "shape_id": 999}]}
        assert (await async_client.put(f"{API}/plates/{draft.id}", json=body)).status_code == 422
        body["yields"][0]["shape_id"] = top["id"]
        r = await async_client.put(f"{API}/plates/{draft.id}", json=body)
        assert r.json()["status"] == "confirmed"
        assert r.json()["yields"][0]["shape_id"] == top["id"]
        by_shape = (await async_client.get(f"{API}/plates?shape_id={top['id']}")).json()
        assert sorted(p["library_file_id"] for p in by_shape) == [42, 43]


@pytest.mark.asyncio
async def test_columns_added_to_tables_from_before_shapes():
    """A database created before shapes gets the new columns at the next start-up."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(
            text("CREATE TABLE farm_components (id INTEGER PRIMARY KEY, code VARCHAR(12), name VARCHAR(200))")
        )
        await conn.execute(
            text(
                "CREATE TABLE farm_plate_yields (id INTEGER PRIMARY KEY, plate_id INTEGER,"
                " object_name VARCHAR(255), quantity INTEGER, component_code VARCHAR(32))"
            )
        )
        await conn.run_sync(Base.metadata.create_all)
        columns = await conn.run_sync(
            lambda sync: {
                t: {c["name"] for c in inspect(sync).get_columns(t)} for t in ("farm_components", "farm_plate_yields")
            }
        )
    await engine.dispose()
    assert {"shape_id", "color", "retired_at"} <= columns["farm_components"]
    assert "shape_id" in columns["farm_plate_yields"]
