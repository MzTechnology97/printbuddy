"""Printfarm (fork): shapes and components moved to one product each, and the check that nothing is shared.

All meshes are generated, all names made up.
"""

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.api.routes import library
from backend.app.core.database import Base
from backend.tests.integration.test_farm_library import add_external, scan
from backend.tests.integration.test_farm_pieces import box, make_mesh_project, tetra

API = "/api/v1/farm"


@pytest.fixture(autouse=True)
def allow_test_external_root(monkeypatch, tmp_path):
    monkeypatch.setattr(library.app_settings, "external_roots", str(tmp_path))


async def ok(r):
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
async def shared(async_client, tmp_path):
    """Set di 2 and Set di 5 on the same 3MF sharing two codes; Audi and gutters on one shape by mistake."""
    nas = tmp_path / "nas"
    make_mesh_project(nas / "Tartufi" / "tartufi.3mf", {1: [("Sopra", tetra(), None), ("Sotto", box(10, 20, 5), None)]})
    make_mesh_project(nas / "Audi" / "audi.3mf", {1: [("Assemblaggio", box(30, 30, 2), None)] * 2})
    make_mesh_project(nas / "Gronde" / "gronde.3mf", {1: [("Assemblaggio", box(5, 5, 40), None)]})
    folder = await add_external(async_client, nas)
    await async_client.put(f"{API}/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)
    files = {
        Path(f["path"]).name: f["library_file_id"] for f in await ok(await async_client.get(f"{API}/library/files"))
    }

    async def folder_of(name):
        return (await ok(await async_client.get(f"/api/v1/library/files/{files[name]}")))["folder_id"]

    shapes = {
        n: (await ok(await async_client.post(f"{API}/shapes", json={"name": n})))["id"]
        for n in ("Tartufo sopra", "Tartufo sotto", "Assemblaggio")
    }
    pieces = await ok(await async_client.get(f"{API}/pieces"))
    for piece in pieces:
        name = {"Sopra": "Tartufo sopra", "Sotto": "Tartufo sotto"}.get(piece["names"][0], "Assemblaggio")
        await ok(
            await async_client.put(f"{API}/pieces", json={"yield_ids": piece["yield_ids"], "shape_id": shapes[name]})
        )
    codes = {}
    for key, shape, color in (
        ("sopra", "Tartufo sopra", "Nero"),
        ("sotto", "Tartufo sotto", "Nero"),
        ("audi", "Assemblaggio", "Nero"),
        ("gronde", "Assemblaggio", "Grigio"),
    ):
        body = {"shape_id": shapes[shape], "color": color}
        codes[key] = (await ok(await async_client.post(f"{API}/components", json=body)))["code"]
    codes["old"] = (await ok(await async_client.post(f"{API}/components", json={"name": "Vecchio"})))["code"]

    ids = {}
    for name, folder_file, parts in (
        ("Set di 2", "tartufi.3mf", [("sopra", 1), ("sotto", 1)]),
        ("Set di 5", "tartufi.3mf", [("sopra", 5), ("sotto", 5)]),
        ("Audi", "audi.3mf", [("audi", 2)]),
        ("Gronde", "gronde.3mf", [("gronde", 1)]),
    ):
        body = {"name": name, "parts": [{"component_code": codes[k], "quantity": q} for k, q in parts]}
        ids[name] = (await ok(await async_client.post(f"{API}/products", json=body)))["project_id"]
        fid = await folder_of(folder_file)
        await ok(await async_client.put(f"{API}/products/{ids[name]}/folders/{fid}", json={"linked": True}))
    return {"shapes": shapes, "codes": codes, "ids": ids}


async def migrate(client, dry_run: bool):
    return await ok(await client.post(f"{API}/migrations/isolate-products?dry_run={str(dry_run).lower()}"))


@pytest.mark.integration
async def test_shared_shapes_and_codes_move_to_one_product_each(async_client, shared):
    ids, codes, shapes = shared["ids"], shared["codes"], shared["shapes"]
    before = await ok(await async_client.get(f"{API}/checks/isolation"))
    assert before["ok"] is False
    shared_code = {"problem": "component_of_another_product", "code": codes["sopra"], "project_id": ids["Set di 5"]}
    assert {**shared_code, "owner": None} in before["problems"]

    dry = await migrate(async_client, True)
    assert (dry["shapes"]["kept"], dry["shapes"]["copied"]) == (3, 3)
    assert await ok(await async_client.get(f"{API}/checks/isolation")) == before  # a dry run writes nothing

    report = await migrate(async_client, False)
    assert report["dry_run"] is False
    assert (report["shapes"]["kept"], report["shapes"]["copied"], report["shapes"]["unused"]) == (3, 3, [])
    rows = {(r["name"], r["product"]): r for r in report["shapes"]["rows"]}
    # The mixed shape stays with Audi, with Audi's geometry only; the gutters get their own.
    assert rows["Assemblaggio", "Audi"]["new_shape_id"] is None
    assert rows["Assemblaggio", "Audi"]["geometries"] == rows["Assemblaggio", "Gronde"]["geometries"] == 1
    assert rows["Tartufo sopra", "Set di 2"]["new_shape_id"] is None
    copy = rows["Tartufo sopra", "Set di 5"]["new_shape_id"]
    assert copy not in (None, shapes["Tartufo sopra"])

    components = report["components"]
    assert components["retired"] == [{"code": codes["old"], "name": "Vecchio"}]
    split = {s["code"]: s for s in components["split"]}
    assert set(split) == {codes["sopra"], codes["sotto"]}
    assert split[codes["sopra"]]["kept_by"] == ids["Set di 2"]
    [new] = split[codes["sopra"]]["new"]
    assert new["project_id"] == ids["Set di 5"]

    # Set di 5 now has its own codes on its own shapes; Seller Hub's codes of the Set di 2 are unchanged.
    view = await ok(await async_client.get(f"{API}/products/{ids['Set di 5']}/view"))
    assert {p["code"] for p in view["parts"]} == {new["code"], split[codes["sotto"]]["new"][0]["code"]}
    assert copy in {p["shape_id"] for p in view["parts"]}
    view = await ok(await async_client.get(f"{API}/products/{ids['Set di 2']}/view"))
    assert {p["code"] for p in view["parts"]} == {codes["sopra"], codes["sotto"]}
    listed = {c["code"]: c for c in await ok(await async_client.get(f"{API}/components"))}
    assert listed[codes["old"]]["retired"] is True

    assert await ok(await async_client.get(f"{API}/checks/isolation")) == {"ok": True, "problems": []}
    again = await migrate(async_client, False)  # nothing left to move
    assert (again["shapes"]["kept"], again["shapes"]["copied"]) == (0, 0)
    assert again["components"] == {"assigned": 0, "split": [], "retired": []}


@pytest.mark.asyncio
async def test_shape_names_repeat_across_products_in_an_older_database():
    """A database from before kept shape names unique overall: start-up drops that, keeping the rows."""
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "CREATE TABLE farm_shapes (id INTEGER NOT NULL PRIMARY KEY, name VARCHAR(200) NOT NULL,"
                " created_at DATETIME DEFAULT (CURRENT_TIMESTAMP), UNIQUE (name))"
            )
        )
        await conn.execute(text("INSERT INTO farm_shapes (id, name) VALUES (7, 'Tartufo sopra')"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("INSERT INTO farm_shapes (name, project_id) VALUES ('Tartufo sopra', 2)"))
        rows = (await conn.execute(text("SELECT id, name, project_id FROM farm_shapes ORDER BY id"))).all()
        await conn.run_sync(Base.metadata.create_all)  # a second start-up leaves it alone
    await engine.dispose()
    assert [tuple(r) for r in rows] == [(7, "Tartufo sopra", None), (8, "Tartufo sopra", 2)]
