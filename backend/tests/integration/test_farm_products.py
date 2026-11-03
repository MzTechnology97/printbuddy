"""Printfarm (fork) products: components with stable codes, products on projects, Seller Hub contract.

All SKUs, ASINs and names are made up.
"""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.core.auth import generate_api_key
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.products import routes as product_routes
from backend.app.farm.products.folders import FarmFolderLink
from backend.app.farm.products.models import FarmProduct, FarmProductPart
from backend.app.models.api_key import APIKey
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project

API = "/api/v1/farm"


async def component(client: AsyncClient, name: str) -> str:
    r = await client.post(f"{API}/components", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["code"]


async def product(client: AsyncClient, **body) -> int:
    r = await client.post(f"{API}/products", json=body)
    assert r.status_code == 200, r.text
    return r.json()["project_id"]


async def product_files(db, project_ids: list[int], count: int = 1) -> list[int]:
    """3MF files in one folder linked to these products: a product's plates come from its own 3MFs."""
    folder = LibraryFolder(name="Prodotto")
    db.add(folder)
    await db.flush()
    files = [
        LibraryFile(folder_id=folder.id, filename=f"p{i}.3mf", file_path=f"/x/p{i}.3mf", file_type="3mf", file_size=1)
        for i in range(count)
    ]
    db.add_all([*files, *(FarmFolderLink(folder_id=folder.id, project_id=pid) for pid in project_ids)])
    await db.commit()
    return [f.id for f in files]


async def products(client: AsyncClient) -> list[dict]:
    r = await client.get(f"{API}/products")
    assert r.status_code == 200, r.text
    return r.json()["products"]


class TestComponents:
    @pytest.mark.asyncio
    async def test_codes_are_sequential_and_renamable(self, async_client):
        assert await component(async_client, "Base") == "C-0001"
        assert await component(async_client, "  Gancio\n ") == "C-0002"
        r = await async_client.patch(f"{API}/components/C-0001", json={"name": "Base X"})
        assert (r.json()["code"], r.json()["name"]) == ("C-0001", "Base X")
        r = await async_client.get(f"{API}/components")
        assert [(c["code"], c["name"]) for c in r.json()] == [("C-0001", "Base X"), ("C-0002", "Gancio")]

    @pytest.mark.asyncio
    async def test_rename_unknown_is_404(self, async_client):
        r = await async_client.patch(f"{API}/components/C-0999", json={"name": "x"})
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_project_names_for_pickers(async_client, db_session):
    db_session.add_all([Project(name="Zeta"), Project(name="Alfa")])
    await db_session.commit()
    pid = await product(async_client, name="Beta")
    rows = (await async_client.get(f"{API}/projects")).json()
    assert [r["name"] for r in rows] == ["Alfa", "Beta", "Zeta"]
    assert {"id": pid, "name": "Beta"} in rows


class TestProducts:
    @pytest.mark.asyncio
    async def test_contract_shape(self, async_client):
        base, hook = await component(async_client, "Base X"), await component(async_client, "Gancio")
        pid = await product(
            async_client,
            name="Portaoggetti X",
            skus=["PX-01", " PX-02 ", "PX-01"],
            asins=["B0TEST0001"],
            parts=[{"component_code": base, "quantity": 1}, {"component_code": hook, "quantity": 2}],
        )
        r = await async_client.get(f"{API}/products")
        body = r.json()
        assert body["generated_at"].endswith("Z")
        [p] = body["products"]
        assert p["project_id"] == pid
        assert p["name"] == "Portaoggetti X"
        assert p["skus"] == ["PX-01", "PX-02"]
        assert p["asins"] == ["B0TEST0001"]
        assert p["updated_at"].endswith("Z")
        # A set adds its pieces (recipe); a component with pieces adds its picture (image_url, image_version).
        none = {"recipe": None, "image_url": None, "image_version": None}
        assert p["parts"] == [
            {"component_code": "C-0001", "name": "Base X", "quantity": 1, "plates": [], **none},
            {"component_code": "C-0002", "name": "Gancio", "quantity": 2, "plates": [], **none},
        ]
        assert p["kind"] is None

    @pytest.mark.asyncio
    async def test_parts_list_confirmed_plates(self, async_client, db_session):
        base, hook = await component(async_client, "Base X"), await component(async_client, "Gancio")
        pid = await product(
            async_client, name="X", parts=[{"component_code": base}, {"component_code": hook, "quantity": 2}]
        )
        f1, f2 = await product_files(db_session, [pid], 2)
        # A mixed plate (4 bases + 6 hooks) and a plate still to confirm.
        mixed = FarmPlate(
            library_file_id=f1, plate_index=1, printer_model="P1S", materials=["PETG"], status="confirmed"
        )
        mixed.yields = [
            FarmPlateYield(object_name="Base", quantity=4, component_code=base),
            FarmPlateYield(object_name="Hook", quantity=6, component_code=hook),
        ]
        draft = FarmPlate(library_file_id=f2, plate_index=1, printer_model="P1S", materials=["PLA"])
        draft.yields = [FarmPlateYield(object_name="Hook", quantity=9, component_code=hook)]
        db_session.add_all([mixed, draft])
        await db_session.commit()

        [p] = await products(async_client)
        plates = {part["component_code"]: part["plates"] for part in p["parts"]}
        assert plates[base] == [
            {"library_file_id": f1, "plate": 1, "printer_model": "P1S", "material": "PETG", "yield": 4}
        ]
        assert [x["yield"] for x in plates[hook]] == [6]

    @pytest.mark.asyncio
    async def test_a_component_prints_only_from_its_products_3mfs(self, async_client, db_session):
        """Audi hooks and Land Rover gutters, both called "Assemblaggio": neither takes the other's plates."""
        codes, plates = {}, {}
        for name in ("Audi", "Grondaie"):
            pid = await product(async_client, name=name)
            body = {"name": "Assemblaggio", "project_id": pid}
            shape = (await async_client.post(f"{API}/shapes", json=body)).json()["id"]
            r = await async_client.post(f"{API}/components", json={"shape_id": shape, "color": "Nero"})
            codes[name] = r.json()["code"]
            assert r.json()["project_id"] == pid
            await async_client.put(f"{API}/products/{pid}/parts/{codes[name]}", json={"quantity": 1})
            [file_id] = await product_files(db_session, [pid])
            plate = FarmPlate(library_file_id=file_id, plate_index=1, printer_model="A1", materials=["PLA"])
            plate.status = "confirmed"
            plate.yields = [FarmPlateYield(object_name="Assemblaggio", quantity=2, shape_id=shape)]
            db_session.add(plate)
            await db_session.commit()
            plates[name] = (pid, file_id, plate.id)

        for name, p in {p["name"]: p for p in await products(async_client)}.items():
            assert [x["library_file_id"] for x in p["parts"][0]["plates"]] == [plates[name][1]]
            view = (await async_client.get(f"{API}/products/{plates[name][0]}/view")).json()
            assert [x["library_file_id"] for x in view["parts"][0]["plates"]] == [plates[name][1]]
        r = await async_client.post(
            f"{API}/jobs", json={"jobs": [{"component_code": codes["Grondaie"], "plate_ids": [plates["Audi"][2]]}]}
        )
        assert r.status_code == 422  # the Audi plate is not one of the gutters' plates

    @pytest.mark.asyncio
    async def test_a_component_is_never_shared_by_two_products(self, async_client):
        hook = await component(async_client, "Gancio")
        await product(async_client, name="X", skus=["X-1"], parts=[{"component_code": hook, "quantity": 2}])
        r = await async_client.post(
            f"{API}/products", json={"name": "Y", "skus": ["Y-1"], "parts": [{"component_code": hook}]}
        )
        assert r.status_code == 409
        assert [p["name"] for p in await products(async_client)] == ["X"]
        listed = {c["code"]: c for c in (await async_client.get(f"{API}/components")).json()}
        assert listed[hook]["project_id"] == (await products(async_client))[0]["project_id"]

    @pytest.mark.asyncio
    async def test_update_replaces_skus_and_parts(self, async_client):
        a, b = await component(async_client, "A"), await component(async_client, "B")
        pid = await product(async_client, name="X", skus=["X-1"], parts=[{"component_code": a}])
        r = await async_client.put(
            f"{API}/products/{pid}",
            json={"name": "X nuovo", "skus": ["X-2"], "parts": [{"component_code": b, "quantity": 3}]},
        )
        assert r.status_code == 200, r.text
        [p] = await products(async_client)
        assert (p["name"], p["skus"], p["parts"][0]["component_code"], p["parts"][0]["quantity"]) == (
            "X nuovo",
            ["X-2"],
            b,
            3,
        )

    @pytest.mark.asyncio
    async def test_sku_belongs_to_one_product(self, async_client):
        await product(async_client, name="X", skus=["SAME"])
        r = await async_client.post(f"{API}/products", json={"name": "Y", "skus": ["SAME"]})
        assert r.status_code == 409
        assert len(await products(async_client)) == 1  # the failed create left nothing behind

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "parts",
        [
            [{"component_code": "C-0404"}],  # unknown
            [{"component_code": "C-0001"}, {"component_code": "C-0001"}],  # twice
            [{"component_code": "C-0001", "quantity": 0}],
            [{"component_code": "gancio"}],
        ],
    )
    async def test_bad_parts_rejected(self, async_client, parts):
        await component(async_client, "A")
        r = await async_client.post(f"{API}/products", json={"name": "X", "parts": parts})
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_existing_project_becomes_product_and_back(self, async_client, db_session):
        project = Project(name="Lavoro esistente")
        db_session.add(project)
        await db_session.commit()
        pid = project.id
        assert await products(async_client) == []
        assert await product(async_client, project_id=pid, skus=["E-1"]) == pid
        r = await async_client.post(f"{API}/products", json={"project_id": pid})
        assert r.status_code == 409
        assert [p["name"] for p in await products(async_client)] == ["Lavoro esistente"]

        r = await async_client.delete(f"{API}/products/{pid}")
        assert r.status_code == 200
        assert await products(async_client) == []
        assert await db_session.get(Project, pid) is not None

    @pytest.mark.asyncio
    async def test_removing_a_product_retires_its_own_components(self, async_client):
        own, other = await component(async_client, "Solo suo"), await component(async_client, "Di Y")
        pid = await product(async_client, name="X", parts=[{"component_code": own}])
        await product(async_client, name="Y", parts=[{"component_code": other}])
        r = await async_client.delete(f"{API}/products/{pid}")
        assert r.json() == {"project_id": pid, "retired": [own]}
        retired = {c["code"]: c["retired"] for c in (await async_client.get(f"{API}/components")).json()}
        assert (retired[own], retired[other]) == (True, False)

    @pytest.mark.asyncio
    async def test_deleting_project_drops_product_rows(self, async_client, db_session):
        a = await component(async_client, "A")
        pid = await product(async_client, name="X", parts=[{"component_code": a}])
        await db_session.delete(await db_session.get(Project, pid))
        await db_session.commit()
        assert (await db_session.execute(select(FarmProduct))).scalars().all() == []
        assert (await db_session.execute(select(FarmProductPart))).scalars().all() == []

    @pytest.mark.asyncio
    async def test_overview_counts_queue_and_recent_prints(
        self, async_client, db_session, printer_factory, archive_factory
    ):
        pid = await product(async_client, name="X")
        other = await product(async_client, name="Y")
        printer = await printer_factory()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db_session.add_all(
            [
                PrintQueueItem(printer_id=printer.id, project_id=pid, status="pending"),
                PrintQueueItem(printer_id=printer.id, project_id=pid, status="pending"),
                PrintQueueItem(printer_id=printer.id, project_id=pid, status="printing"),
                PrintQueueItem(printer_id=printer.id, project_id=pid, status="completed"),
            ]
        )
        await db_session.commit()
        await archive_factory(printer.id, project_id=pid, quantity=4, completed_at=now - timedelta(days=1))
        await archive_factory(printer.id, project_id=pid, quantity=9, completed_at=now - timedelta(days=30))
        await archive_factory(printer.id, project_id=pid, quantity=5, status="failed", completed_at=now)

        r = await async_client.get(f"{API}/products/overview")
        rows = {row["project_id"]: row for row in r.json()}
        assert rows[pid] == {"project_id": pid, "has_cover": False, "queued": 2, "printing": 1, "printed_recent": 4}
        assert rows[other]["queued"] == rows[other]["printed_recent"] == 0


class TestApiKey:
    async def _key(self, db_session, can_read_status: bool) -> str:
        full, key_hash, prefix = generate_api_key()
        db_session.add(APIKey(name="seller hub", key_hash=key_hash, key_prefix=prefix, can_read_status=can_read_status))
        await db_session.commit()
        return full

    @pytest.mark.asyncio
    async def test_key_needs_read_status(self, async_client, db_session):
        ok, denied = await self._key(db_session, True), await self._key(db_session, False)
        for path in ("/products", "/components"):
            assert (await async_client.get(API + path, headers={"X-API-Key": ok})).status_code == 200
            assert (await async_client.get(API + path, headers={"X-API-Key": denied})).status_code == 403
            assert (await async_client.get(API + path, headers={"X-API-Key": "bb_wrong"})).status_code == 401
        # A component's picture: Seller Hub downloads it with the same key (404 here: nothing to draw).
        await async_client.post(f"{API}/components", json={"name": "Base X"})
        image = f"{API}/components/C-0001/image"
        assert (await async_client.get(image, headers={"X-API-Key": ok})).status_code == 404
        assert (await async_client.get(image, headers={"X-API-Key": denied})).status_code == 403
        assert (await async_client.get(image, headers={"X-API-Key": "bb_wrong"})).status_code == 401


def plan() -> dict:
    """Shape of Seller Hub's GET /api/print-plan (only the fields the import reads)."""
    return {
        "parts": [
            {
                "key": "asin:B0TEST0001",
                "name": "Portaoggetti X",
                "skus": [{"sku": "PX-01", "asin": "B0TEST0001"}, {"sku": "PX-02", "asin": "B0TEST0001"}],
            },
            {"key": "parte:lampada", "name": "Lampada", "skus": [{"sku": "LA-01", "asin": "B0TEST0002"}]},
        ],
        "components": [
            {
                "code": "C-0003",
                "name": "Gancio",
                "stock": 10,
                "products": [
                    {"key": "asin:B0TEST0001", "name": "Portaoggetti X", "qty": 2},
                    {"key": "parte:lampada", "name": "Lampada", "qty": 1},
                ],
            },
            {
                "code": "C-0007",
                "name": "Base X",
                "stock": 0,
                "products": [{"key": "asin:B0TEST0001", "name": "Portaoggetti X", "qty": 1}],
            },
            {"code": "C-0012", "name": "Senza prodotti", "products": []},
            {"code": "bad", "name": "ignored", "products": []},
        ],
    }


class TestSellerHubImport:
    @pytest.mark.asyncio
    async def test_import_keeps_codes_and_is_idempotent(self, async_client):
        r = await async_client.post(f"{API}/import/sellerhub", json=plan())
        assert r.json() == {
            "components_created": 3,
            "components_existing": 0,
            "products_created": 2,
            "products_skipped": 0,
            "covers_added": 0,
        }
        r = await async_client.post(f"{API}/import/sellerhub", json=plan())
        assert r.json() == {
            "components_created": 0,
            "components_existing": 3,
            "products_created": 0,
            "products_skipped": 2,
            "covers_added": 0,
        }

        comps = (await async_client.get(f"{API}/components")).json()
        assert [c["code"] for c in comps] == ["C-0003", "C-0007", "C-0012"]
        by_name = {p["name"]: p for p in await products(async_client)}
        x = by_name["Portaoggetti X"]
        assert (x["skus"], x["asins"]) == (["PX-01", "PX-02"], ["B0TEST0001"])
        assert [(p["component_code"], p["quantity"]) for p in x["parts"]] == [("C-0003", 2), ("C-0007", 1)]
        assert [(p["component_code"], p["quantity"]) for p in by_name["Lampada"]["parts"]] == [("C-0003", 1)]
        # New components continue after the highest imported code, never reusing one.
        assert await component(async_client, "Nuovo") == "C-0013"

    @pytest.mark.asyncio
    async def test_import_keeps_listings_with_the_same_title(self, async_client):
        data = plan()
        # Two Amazon listings, same title, different ASIN and component.
        data["parts"].append(
            {"key": "asin:B0TEST0003", "name": "Lampada", "skus": [{"sku": "LA-02", "asin": "B0TEST0003"}]}
        )
        data["components"][2]["products"] = [{"key": "asin:B0TEST0003", "name": "Lampada", "qty": 1}]
        r = await async_client.post(f"{API}/import/sellerhub", json=data)
        assert r.json()["products_created"] == 3
        lamps = [p for p in await products(async_client) if p["name"] == "Lampada"]
        assert sorted(p["asins"][0] for p in lamps) == ["B0TEST0002", "B0TEST0003"]

    @pytest.mark.asyncio
    async def test_import_adds_amazon_image_as_cover(self, async_client, db_session, monkeypatch, tmp_path):
        fetched: list[str] = []

        async def fake_fetch(url: str) -> bytes:
            fetched.append(url)
            return b"jpeg bytes"

        monkeypatch.setattr(product_routes, "_fetch_image", fake_fetch)
        monkeypatch.setattr(product_routes, "get_project_attachments_dir", lambda pid: tmp_path / str(pid))
        existing = await product(async_client, name="Fatto a mano", skus=["LA-01"])
        data = plan()
        data["parts"][0]["image_url"] = "https://m.media-amazon.com/images/I/31TEST+x.jpg"
        data["parts"][1]["image_url"] = "https://example.com/images/I/evil.jpg"  # not Amazon's CDN: ignored

        r = await async_client.post(f"{API}/import/sellerhub", json=data)
        assert r.json()["covers_added"] == 1
        assert fetched == ["https://m.media-amazon.com/images/I/31TEST+x.jpg"]
        x = (await db_session.execute(select(Project).where(Project.name == "Portaoggetti X"))).scalar_one()
        assert (tmp_path / str(x.id) / x.cover_image_filename).read_bytes() == b"jpeg bytes"
        assert x.cover_image_filename.endswith(".jpg")

        # Existing products get the image too, and a cover is never replaced.
        data["parts"][1]["image_url"] = "https://m.media-amazon.com/images/I/41LAMP.png"
        r = await async_client.post(f"{API}/import/sellerhub", json=data)
        assert r.json()["covers_added"] == 1
        assert fetched[-1].endswith("41LAMP.png")
        assert (await db_session.get(Project, existing)).cover_image_filename is not None
        r = await async_client.post(f"{API}/import/sellerhub", json=data)
        assert r.json()["covers_added"] == 0
        assert len(fetched) == 2

    @pytest.mark.asyncio
    async def test_import_skips_products_already_defined(self, async_client, db_session):
        await product(async_client, name="Fatto a mano", skus=["LA-01"])
        r = await async_client.post(f"{API}/import/sellerhub", json=plan())
        assert r.json()["products_created"] == 1
        assert r.json()["products_skipped"] == 1
        names = (await db_session.execute(select(Project.name))).scalars().all()
        assert sorted(names) == ["Fatto a mano", "Portaoggetti X"]
