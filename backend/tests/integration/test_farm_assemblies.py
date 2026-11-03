"""Printfarm (fork): groups of a kit's parts assembled together before the product. Names are made up."""

import pytest

API = "/api/v1/farm"


async def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
async def handles(async_client):
    """A handle set: 2 half-moons (two halves each), 2 pins, sold also as a pack of 2."""
    codes = {}
    for name in ("Mezza luna 1", "Mezza luna 2", "Perno"):
        codes[name] = (await ok(await async_client.post(f"{API}/components", json={"name": name})))["code"]
    body = {
        "name": "Set maniglie",
        "parts": [{"component_code": codes[n], "quantity": 2} for n in ("Mezza luna 1", "Mezza luna 2", "Perno")],
        "variants": [{"skus": ["SET-X2"], "pieces": 2}],
    }
    pid = (await ok(await async_client.post(f"{API}/products", json=body)))["project_id"]
    await ok(await async_client.put(f"{API}/products/{pid}/kind", json={"kind": "kit"}))
    return pid, codes


async def save(client, pid, groups):
    return await client.put(f"{API}/products/{pid}/assemblies", json={"assemblies": groups})


@pytest.mark.asyncio
async def test_groups_to_assemble_on_the_sheet_and_for_seller_hub(async_client, handles):
    pid, codes = handles
    half = {
        "name": "Maniglia mezza luna",
        "per_unit": 2,
        "parts": [{"component_code": codes["Mezza luna 1"]}, {"component_code": codes["Mezza luna 2"]}],
    }
    body = await ok(await save(async_client, pid, [half]))
    [group] = body["assemblies"]
    assert (group["key"], group["name"], group["per_unit"], group["warnings"]) == ("A1", "Maniglia mezza luna", 2, [])
    view = await ok(await async_client.get(f"{API}/products/{pid}/view"))
    assert view["assemblies"] == body["assemblies"]

    [product] = (await ok(await async_client.get(f"{API}/products")))["products"]
    expected = [
        {
            "key": "A1",
            "name": "Maniglia mezza luna",
            "per_unit": 2,
            "parts": [
                {"component_code": codes["Mezza luna 1"], "quantity": 1},
                {"component_code": codes["Mezza luna 2"], "quantity": 1},
            ],
        }
    ]
    assert product["assemblies"] == expected  # the pin, in no group, is packed directly
    [pack] = product["variants"]
    assert pack["assemblies"] == [{**expected[0], "per_unit": 4}]  # two sets per sale

    # Keys stay: renamed and saved again it is still A1; a new group gets A2, never a key used before.
    pin = {"name": "Perno", "per_unit": 2, "parts": [{"component_code": codes["Perno"]}]}
    body = await ok(await save(async_client, pid, [{**half, "key": "A1", "name": "Mezza luna"}, pin]))
    assert [(g["key"], g["name"]) for g in body["assemblies"]] == [("A1", "Mezza luna"), ("A2", "Perno")]
    body = await ok(await save(async_client, pid, [pin]))
    assert [g["key"] for g in body["assemblies"]] == ["A3"]

    # Quantities that do not add up are saved, with a warning on the sheet.
    body = await ok(await save(async_client, pid, [{**half, "per_unit": 3}]))
    assert body["assemblies"][0]["warnings"] == [
        {"component_code": codes["Mezza luna 1"], "expected": 3, "quantity": 2},
        {"component_code": codes["Mezza luna 2"], "expected": 3, "quantity": 2},
    ]

    # A part taken out of the product leaves its group.
    await ok(await async_client.put(f"{API}/products/{pid}/parts/{codes['Mezza luna 2']}", json={"quantity": 0}))
    view = await ok(await async_client.get(f"{API}/products/{pid}/view"))
    assert [p["component_code"] for p in view["assemblies"][0]["parts"]] == [codes["Mezza luna 1"]]


@pytest.mark.asyncio
async def test_a_group_holds_only_the_products_own_parts_once(async_client, handles):
    pid, codes = handles
    other = (await ok(await async_client.post(f"{API}/components", json={"name": "Altro"})))["code"]
    await ok(await async_client.post(f"{API}/products", json={"name": "Altro", "parts": [{"component_code": other}]}))

    r = await save(async_client, pid, [{"name": "G", "parts": [{"component_code": other}]}])
    assert r.status_code == 422  # another product's component: never in this product's groups
    twice = [
        {"name": "G1", "parts": [{"component_code": codes["Perno"]}]},
        {"name": "G2", "parts": [{"component_code": codes["Perno"]}]},
    ]
    assert (await save(async_client, pid, twice)).status_code == 422  # one group per part
    await ok(await async_client.put(f"{API}/products/{pid}/kind", json={"kind": "set"}))
    r = await save(async_client, pid, [{"name": "G", "parts": [{"component_code": codes["Perno"]}]}])
    assert r.status_code == 422  # a set is one component
