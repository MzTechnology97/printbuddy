"""Printfarm (fork): shapes and components moved to one product each, and the check that nothing is shared.

All meshes are generated, all names made up.
"""

from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.api.routes import library
from backend.app.core.database import Base
from backend.app.farm.products.models import FarmComponent, FarmProductPart
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
async def shared(async_client, db_session, tmp_path):
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
        ids[name] = (await ok(await async_client.post(f"{API}/products", json={"name": name})))["project_id"]
        # Shared as before the move (the API refuses it now): straight into the tables.
        for i, (k, q) in enumerate(parts):
            comp = await db_session.scalar(select(FarmComponent).where(FarmComponent.code == codes[k]))
            db_session.add(FarmProductPart(project_id=ids[name], component_id=comp.id, quantity=q, sort_order=i))
        await db_session.commit()
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


@pytest.mark.integration
async def test_a_split_code_gets_its_own_products_copy_of_the_shape(async_client, db_session, tmp_path):
    """The shape stays with a third product: both halves of the split code move to their own copies."""
    nas = tmp_path / "nas"
    make_mesh_project(nas / "Ami" / "ami.3mf", {1: [("Maniglia", tetra(), None)]})
    folder = await add_external(async_client, nas)
    await async_client.put(f"{API}/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)
    [lib] = await ok(await async_client.get(f"{API}/library/files"))
    ami = (await ok(await async_client.get(f"/api/v1/library/files/{lib['library_file_id']}")))["folder_id"]
    shape = (await ok(await async_client.post(f"{API}/shapes", json={"name": "Maniglia"})))["id"]
    [piece] = await ok(await async_client.get(f"{API}/pieces"))
    await ok(await async_client.put(f"{API}/pieces", json={"yield_ids": piece["yield_ids"], "shape_id": shape}))
    black = await ok(await async_client.post(f"{API}/components", json={"shape_id": shape, "color": "Nero"}))
    yellow = await ok(await async_client.post(f"{API}/components", json={"shape_id": shape, "color": "Giallo"}))

    ids = {}
    for name, code in (("Nero", black["code"]), ("Giallo", yellow["code"]), ("Giallo bis", yellow["code"])):
        ids[name] = (await ok(await async_client.post(f"{API}/products", json={"name": name})))["project_id"]
        await ok(await async_client.put(f"{API}/products/{ids[name]}/folders/{ami}", json={"linked": True}))
        comp = await db_session.scalar(select(FarmComponent).where(FarmComponent.code == code))
        db_session.add(FarmProductPart(project_id=ids[name], component_id=comp.id, quantity=4))
        await db_session.commit()

    report = await migrate(async_client, False)
    copies = {r["product"]: r["new_shape_id"] for r in report["shapes"]["rows"]}
    assert copies["Nero"] is None  # the lowest id: keeps the shape
    [split] = report["components"]["split"]
    assert (split["code"], split["kept_by"]) == (yellow["code"], ids["Giallo"])
    shapes = {
        name: [p["shape_id"] for p in (await ok(await async_client.get(f"{API}/products/{pid}/view")))["parts"]]
        for name, pid in ids.items()
    }
    assert shapes == {"Nero": [shape], "Giallo": [copies["Giallo"]], "Giallo bis": [copies["Giallo bis"]]}
    assert await ok(await async_client.get(f"{API}/checks/isolation")) == {"ok": True, "problems": []}


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


async def snapshot(client, pid: int) -> dict:
    """What a product shows and prints, without ids that a re-saved 3MF renews."""
    view = await ok(await client.get(f"{API}/products/{pid}/view"))
    pieces = (await ok(await client.get(f"{API}/products/{pid}/pieces")))["pieces"]
    return {
        "kind": view["kind"],
        "disabled": view["disabled"],
        "parts": [
            (
                p["code"],
                p["name"],
                p["quantity"],
                p["shape"],
                p["color"],
                p["retired"],
                [(r["shape"], r["quantity"]) for r in p["recipe"] or []],
                sorted((x["plate"], x["yield"]) for x in p["plates"]),
            )
            for p in view["parts"]
        ],
        "pieces": sorted((p["shape"] or "", p["quantity"] or 0, p["not_part"]) for p in pieces),
        "shapes": sorted(s["name"] for s in await ok(await client.get(f"{API}/shapes?project_id={pid}"))),
        "components": sorted(
            (c["code"], c["name"], c["retired"])
            for c in await ok(await client.get(f"{API}/components?project_id={pid}"))
        ),
    }


@pytest.mark.integration
async def test_nothing_done_on_one_product_changes_another(async_client, tmp_path):
    """Set di 2 and Set di 5 on the same 3MF: every action on one leaves the other exactly as it was."""
    nas = tmp_path / "nas"
    project = nas / "Tartufi" / "tartufi.3mf"
    make_mesh_project(project, {1: [("Sopra", tetra(), None), ("Sotto", box(10, 20, 5), None)]})
    folder = await add_external(async_client, nas)
    await async_client.put(f"{API}/library/folders/{folder['id']}", json={"scan_interval_seconds": 0})
    await scan(async_client, folder)
    [lib] = await ok(await async_client.get(f"{API}/library/files"))
    tartufi = (await ok(await async_client.get(f"/api/v1/library/files/{lib['library_file_id']}")))["folder_id"]

    ids = {}
    for name in ("Set di 2", "Set di 5"):
        ids[name] = (await ok(await async_client.post(f"{API}/products", json={"name": name})))["project_id"]
        await ok(await async_client.put(f"{API}/products/{ids[name]}/folders/{tartufi}", json={"linked": True}))
        for piece in (await ok(await async_client.get(f"{API}/products/{ids[name]}/pieces")))["pieces"]:
            body = {"yield_ids": piece["yield_ids"], "quantity": 1, "shape_name": piece["names"][0], "color": "Nero"}
            await ok(await async_client.put(f"{API}/products/{ids[name]}/pieces", json=body))
    a, b = ids["Set di 2"], ids["Set di 5"]
    before = await snapshot(async_client, b)
    assert [p[3] for p in before["parts"]] == ["Sopra", "Sotto"]
    assert all(p[7] == [(1, 1)] for p in before["parts"])

    async def pieces_of_a():
        return {p["names"][0]: p for p in (await ok(await async_client.get(f"{API}/products/{a}/pieces")))["pieces"]}

    async def unchanged(action: str):
        assert await snapshot(async_client, b) == before, action

    sopra = (await pieces_of_a())["Sopra"]
    await ok(
        await async_client.put(f"{API}/products/{a}/pieces", json={"yield_ids": sopra["yield_ids"], "quantity": 3})
    )
    await unchanged("Salva")

    sotto = (await pieces_of_a())["Sotto"]
    await ok(
        await async_client.put(f"{API}/products/{a}/pieces", json={"yield_ids": sotto["yield_ids"], "quantity": 0})
    )
    await unchanged("Non fa parte")
    await ok(await async_client.put(f"{API}/products/{a}/pieces/restore", json={"yield_ids": sotto["yield_ids"]}))
    body = {"yield_ids": sotto["yield_ids"], "quantity": 1, "shape_name": "Sotto", "color": "Nero"}
    await ok(await async_client.put(f"{API}/products/{a}/pieces", json=body))
    await unchanged("Rimetti")

    for kind in ("set", "kit", "set"):
        await ok(await async_client.put(f"{API}/products/{a}/kind", json={"kind": kind}))
        await unchanged(f"kind {kind}")

    shapes = {s["name"]: s["id"] for s in await ok(await async_client.get(f"{API}/shapes?project_id={a}"))}
    await ok(await async_client.patch(f"{API}/shapes/{shapes['Sopra']}", json={"name": "Sopra grande"}))
    await unchanged("rinomina")
    await ok(await async_client.post(f"{API}/shapes/{shapes['Sotto']}/merge", json={"into": shapes["Sopra"]}))
    await unchanged("Stessa forma di")
    of_b = await ok(await async_client.get(f"{API}/shapes?project_id={b}"))
    r = await async_client.post(f"{API}/shapes/{shapes['Sopra']}/merge", json={"into": of_b[0]["id"]})
    assert r.status_code == 409  # never into another product

    for active in (False, True):
        await ok(await async_client.put(f"{API}/products/{a}/active", json={"active": active}))
        await unchanged(f"attivo {active}")

    for part in (await ok(await async_client.get(f"{API}/products/{a}/view")))["parts"]:
        await ok(await async_client.put(f"{API}/products/{a}/parts/{part['code']}", json={"quantity": 0}))
    await unchanged("Togli")

    # The 3MF saved again with the objects renamed: recognised by geometry, for both products.
    make_mesh_project(project, {1: [("Sopra v2", tetra(), None), ("Sotto v2", box(10, 20, 5), None)]})
    await scan(async_client, folder)
    await unchanged("3MF salvato di nuovo")

    await ok(await async_client.delete(f"{API}/products/{a}"))
    await unchanged("Rimuovi")
    assert await ok(await async_client.get(f"{API}/checks/isolation")) == {"ok": True, "problems": []}
