"""Printfarm (fork): products from the Seller Hub listings picked by hand. All SKUs, ASINs and titles are made up."""

import httpx
import pytest

from backend.app.farm.plan import service
from backend.app.farm.products import routes as product_routes

API = "/api/v1/farm"
IMG = "https://m.media-amazon.com/images/I/"


def plan() -> dict:
    """Shape of Seller Hub's GET /api/print-plan, only the fields the import reads."""
    return {
        "parts": [
            # Two listings with the same ASIN: Seller Hub already makes them one part, so one row.
            {
                "key": "asin:B0FAKE0001",
                "name": "Supporto di prova",
                "image_url": IMG + "FAKEIMG01._SL300_.jpg",
                "skus": [
                    {"sku": "TEST-A1", "asin": "B0FAKE0001", "title": "Supporto di prova, nero"},
                    {"sku": "TEST-A1-FBM", "asin": "B0FAKE0001", "title": "Supporto di prova, nero"},
                ],
            },
            {
                "key": "asin:B0FAKE0002",
                "name": "Gancio di prova",
                "image_url": "",
                "skus": [{"sku": "TEST-B1", "asin": "B0FAKE0002", "title": "Gancio di prova", "image_url": ""}],
            },
            # A named part: packs of the same piece under different ASINs, one product.
            {
                "key": "parte:coppia",
                "name": "Coppia di prova",
                "skus": [
                    {"sku": "TEST-C2", "asin": "B0FAKE0003", "title": "Coppia di prova, 2 pezzi"},
                    {"sku": "TEST-C5", "asin": "B0FAKE0004", "title": "Coppia di prova, 5 pezzi"},
                ],
            },
            {"key": "asin:B0FAKE0009", "name": "Senza codici", "skus": [{"sku": "", "asin": ""}]},
        ],
        "excluded_inactive": 4,
        "ignored": [{"sku": "TEST-IGN", "asin": "B0FAKE0099", "title": "Non stampato"}],
    }


@pytest.fixture
async def hub(async_client, monkeypatch):
    """Seller Hub answering with ``plan()``; the test may change ``state["plan"]`` or make it fail."""
    state = {"plan": plan(), "down": False}

    async def fake_get(url):
        assert url == "http://hub.test:8000"
        if state["down"]:
            raise httpx.ConnectError("down")
        return state["plan"]

    monkeypatch.setattr(service, "_get_print_plan", fake_get)
    r = await async_client.put(f"{API}/plan/settings", json={"url": "http://hub.test:8000"})
    assert r.status_code == 200, r.text
    return state


@pytest.fixture
def fetched(monkeypatch, tmp_path) -> list[str]:
    """No network: every image download is recorded and answered with a few bytes."""
    urls: list[str] = []

    async def fake_fetch(url):
        urls.append(url)
        return b"jpg"

    monkeypatch.setattr(product_routes, "_fetch_image", fake_fetch)
    monkeypatch.setattr(product_routes, "get_project_attachments_dir", lambda pid: tmp_path / str(pid))
    return urls


async def listings(client) -> dict:
    r = await client.get(f"{API}/import/sellerhub/listings")
    assert r.status_code == 200, r.text
    return r.json()


async def products(client) -> dict:
    return {p["name"]: p for p in (await client.get(f"{API}/products")).json()["products"]}


@pytest.mark.asyncio
async def test_lists_listings_without_a_product_one_row_per_part(async_client, hub):
    body = await listings(async_client)
    assert [(r["key"], r["skus"], r["asins"]) for r in body["listings"]] == [
        ("asin:B0FAKE0001", ["TEST-A1", "TEST-A1-FBM"], ["B0FAKE0001"]),
        ("asin:B0FAKE0002", ["TEST-B1"], ["B0FAKE0002"]),
        ("parte:coppia", ["TEST-C2", "TEST-C5"], ["B0FAKE0003", "B0FAKE0004"]),
    ]
    first = body["listings"][0]
    assert (first["name"], first["title"]) == ("Supporto di prova", "Supporto di prova, nero")
    assert first["image_url"] == IMG + "FAKEIMG01.jpg"
    assert body["listings"][1]["image_url"] is None
    # Inactive listings never reach Seller Hub's plan, "not printed" ones are apart: counted, never proposed.
    assert (body["excluded_inactive"], body["ignored"]) == (4, 1)
    # Reading proposes, it never creates.
    assert await products(async_client) == {}


@pytest.mark.asyncio
async def test_a_listing_whose_sku_or_asin_a_product_owns_is_not_proposed(async_client, hub):
    # One SKU of the part is enough, and a variant's SKU counts like the product's own.
    r = await async_client.post(f"{API}/products", json={"name": "Già fatto", "skus": ["TEST-A1-FBM"]})
    assert r.status_code == 200, r.text
    r = await async_client.post(
        f"{API}/products", json={"name": "Con variante", "variants": [{"skus": ["TEST-C5"], "pieces": 5}]}
    )
    assert r.status_code == 200, r.text
    assert [r["key"] for r in (await listings(async_client))["listings"]] == ["asin:B0FAKE0002"]

    await async_client.post(f"{API}/products", json={"name": "Per ASIN", "asins": ["B0FAKE0002"]})
    assert (await listings(async_client))["listings"] == []


@pytest.mark.asyncio
async def test_only_the_chosen_listings_become_products(async_client, hub, fetched):
    r = await async_client.post(f"{API}/import/sellerhub/listings", json={"keys": ["asin:B0FAKE0001"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["name"] for c in body["created"]] == ["Supporto di prova"]
    assert (body["already_owned"], body["not_found"], body["covers_added"]) == ([], [], 1)
    assert fetched == [IMG + "FAKEIMG01.jpg"]

    made = await products(async_client)
    assert list(made) == ["Supporto di prova"]
    p = made["Supporto di prova"]
    assert (p["skus"], p["asins"], p["parts"]) == (["TEST-A1", "TEST-A1-FBM"], ["B0FAKE0001"], [])
    # The others are still there to choose later.
    assert [r["key"] for r in (await listings(async_client))["listings"]] == ["asin:B0FAKE0002", "parte:coppia"]


@pytest.mark.asyncio
async def test_import_skips_what_is_owned_or_gone_and_never_doubles(async_client, hub, fetched):
    await async_client.post(f"{API}/products", json={"name": "Fatto nel frattempo", "skus": ["TEST-B1"]})
    # Seller Hub changed between the list and the click: the same SKU now on two parts.
    hub["plan"]["parts"].append(
        {"key": "asin:B0FAKE0005", "name": "Doppione", "skus": [{"sku": "TEST-C2", "asin": "B0FAKE0005"}]}
    )
    keys = ["parte:coppia", "asin:B0FAKE0005", "asin:B0FAKE0002", "asin:B0FAKE0042", "parte:coppia"]
    body = (await async_client.post(f"{API}/import/sellerhub/listings", json={"keys": keys})).json()
    assert [c["key"] for c in body["created"]] == ["parte:coppia"]
    assert body["already_owned"] == ["asin:B0FAKE0005", "asin:B0FAKE0002"]
    assert body["not_found"] == ["asin:B0FAKE0042"]
    assert (await products(async_client))["Coppia di prova"]["asins"] == ["B0FAKE0003", "B0FAKE0004"]


@pytest.mark.asyncio
async def test_seller_hub_missing_or_down(async_client, hub):
    hub["down"] = True
    r = await async_client.get(f"{API}/import/sellerhub/listings")
    assert r.status_code == 502
    r = await async_client.post(f"{API}/import/sellerhub/listings", json={"keys": ["asin:B0FAKE0001"]})
    assert r.status_code == 502
    assert await products(async_client) == {}

    await async_client.put(f"{API}/plan/settings", json={"url": ""})
    assert (await async_client.get(f"{API}/import/sellerhub/listings")).status_code == 409
    assert (await async_client.post(f"{API}/import/sellerhub/listings", json={"keys": []})).status_code == 422


@pytest.mark.asyncio
async def test_thumbnail_only_from_amazon_and_small(async_client, fetched):
    r = await async_client.get(f"{API}/import/sellerhub/thumbnail", params={"url": IMG + "FAKEIMG01._SL300_.jpg"})
    assert (r.status_code, r.content, r.headers["content-type"]) == (200, b"jpg", "image/jpeg")
    assert fetched == [IMG + "FAKEIMG01._SL160_.jpg"]
    for url in ["http://169.254.169.254/latest/meta-data", "https://example.com/images/I/FAKE.jpg", IMG + "x.gif"]:
        assert (await async_client.get(f"{API}/import/sellerhub/thumbnail", params={"url": url})).status_code == 400
    assert len(fetched) == 1


async def run_covers():
    """One hourly photo run (``database.async_session`` is the test database here)."""
    from backend.app.core import database
    from backend.app.farm.products import sellerhub_import

    async with database.async_session() as db:
        return await sellerhub_import.sync_plan_covers(db)


@pytest.mark.asyncio
async def test_ignore_leaves_a_listing_out_of_the_window_but_in_the_panel(async_client, hub):
    assert [r["dismissed"] for r in (await listings(async_client))["listings"]] == [False, False, False]
    r = await async_client.post(f"{API}/import/sellerhub/listings/ignore", json={"keys": ["asin:B0FAKE0002"]})
    assert r.status_code == 200, r.text
    rows = {r["key"]: r["dismissed"] for r in (await listings(async_client))["listings"]}
    assert rows == {"asin:B0FAKE0001": False, "asin:B0FAKE0002": True, "parte:coppia": False}
    assert await products(async_client) == {}
    assert (await async_client.post(f"{API}/import/sellerhub/listings/ignore", json={"keys": []})).status_code == 422


@pytest.mark.asyncio
async def test_an_ignored_listing_can_still_be_added_and_imports_are_to_complete(async_client, hub, fetched):
    await async_client.post(f"{API}/import/sellerhub/listings/ignore", json={"keys": ["asin:B0FAKE0002"]})
    r = await async_client.post(f"{API}/import/sellerhub/listings", json={"keys": ["asin:B0FAKE0002"]})
    assert [c["name"] for c in r.json()["created"]] == ["Gancio di prova"]
    await async_client.post(f"{API}/products", json={"name": "A mano", "skus": ["TEST-HAND"]})
    made = await products(async_client)
    assert (made["Gancio di prova"]["to_complete"], made["A mano"]["to_complete"]) == (True, False)
    assert "asin:B0FAKE0002" not in [r["key"] for r in (await listings(async_client))["listings"]]


@pytest.mark.asyncio
async def test_the_photo_found_later_arrives_on_the_next_hourly_run(async_client, hub, fetched):
    await async_client.post(f"{API}/import/sellerhub/listings", json={"keys": ["asin:B0FAKE0002"]})
    assert fetched == []  # no photo on Seller Hub yet
    hub["plan"]["parts"][1]["skus"][0]["image_url"] = IMG + "FAKEIMG02.jpg"
    assert (await run_covers())["added"] == 1
    assert fetched == [IMG + "FAKEIMG02.jpg"]
    # The listing's photo changes: the cover set by the import follows it; then nothing more to do.
    hub["plan"]["parts"][1]["skus"][0]["image_url"] = IMG + "FAKEIMG2B.jpg"
    assert (await run_covers())["replaced"] == 1
    assert (await run_covers())["unchanged"] == 1


@pytest.mark.asyncio
async def test_hourly_photos_never_touch_a_cover_uploaded_by_hand(async_client, hub, fetched, db_session):
    from backend.app.models.project import Project

    r = await async_client.post(f"{API}/products", json={"name": "Foto mia", "skus": ["TEST-A1"]})
    project = await db_session.get(Project, r.json()["project_id"])
    project.cover_image_filename, project.cover_source_url = "mine.jpg", None
    await db_session.commit()
    assert (await run_covers())["manual"] == 1
    assert fetched == []
    await db_session.refresh(project)
    assert project.cover_image_filename == "mine.jpg"


@pytest.mark.asyncio
async def test_hourly_photos_with_seller_hub_down_or_not_set_change_nothing(async_client, hub, fetched):
    await async_client.post(f"{API}/import/sellerhub/listings", json={"keys": ["asin:B0FAKE0002"]})
    hub["plan"]["parts"][1]["skus"][0]["image_url"] = IMG + "FAKEIMG02.jpg"
    hub["down"] = True
    assert await run_covers() is None
    await async_client.put(f"{API}/plan/settings", json={"url": ""})
    assert await run_covers() is None
    assert fetched == []
