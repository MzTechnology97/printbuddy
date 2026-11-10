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
