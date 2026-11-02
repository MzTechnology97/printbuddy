"""Printfarm (fork): library folders linked to several projects, subfolders included. Names are made up."""

import pytest
from sqlalchemy import select

from backend.app.farm.products.folders import FarmFolderLink
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.project import Project


@pytest.fixture
async def nas(db_session):
    """Prodotto X / Orca / v2, a sibling folder, one file in each, and three projects."""
    root = LibraryFolder(name="Prodotto X")
    db_session.add(root)
    await db_session.flush()
    orca = LibraryFolder(name="Orca", parent_id=root.id)
    other = LibraryFolder(name="Altro")
    db_session.add_all([orca, other])
    await db_session.flush()
    v2 = LibraryFolder(name="v2", parent_id=orca.id)
    db_session.add(v2)
    await db_session.flush()
    for folder in (root, orca, v2, other):
        db_session.add(
            LibraryFile(
                folder_id=folder.id,
                filename=f"{folder.name}.3mf",
                file_path=f"/x/{folder.name}.3mf",
                file_type="3mf",
                file_size=1,
            )
        )
    projects = [Project(name=n) for n in ("Nero", "Bianco", "Vecchio")]
    db_session.add_all(projects)
    await db_session.commit()
    return {"root": root.id, "orca": orca.id, "v2": v2.id, "other": other.id, **{p.name: p.id for p in projects}}


async def link(client, folder_id: int, project_ids: list[int]):
    r = await client.put(f"/api/v1/farm/folders/{folder_id}/projects", json={"project_ids": project_ids})
    assert r.status_code == 200, r.text
    return r.json()["project_ids"]


@pytest.mark.asyncio
async def test_one_folder_several_products_with_subfolders(async_client, nas):
    assert await link(async_client, nas["root"], [nas["Nero"], nas["Bianco"]]) == sorted([nas["Nero"], nas["Bianco"]])

    for project in ("Nero", "Bianco"):
        folders = (await async_client.get(f"/api/v1/library/folders/by-project/{nas[project]}")).json()
        assert [f["name"] for f in folders] == ["Prodotto X", "Prodotto X / Orca", "Prodotto X / Orca / v2"]
        files = (await async_client.get(f"/api/v1/library/files?project_id={nas[project]}")).json()
        assert sorted(f["filename"] for f in files) == ["Orca.3mf", "Prodotto X.3mf", "v2.3mf"]

    tree = (await async_client.get("/api/v1/library/folders")).json()
    root = next(f for f in tree if f["id"] == nas["root"])
    assert root["project_name"] == "Bianco, Nero"


@pytest.mark.asyncio
async def test_upstream_link_still_counts_and_moves_on_save(async_client, db_session, nas):
    folder = await db_session.get(LibraryFolder, nas["other"])
    folder.project_id = nas["Vecchio"]
    await db_session.commit()
    files = (await async_client.get(f"/api/v1/library/files?project_id={nas['Vecchio']}")).json()
    assert [f["filename"] for f in files] == ["Altro.3mf"]
    linked = (await async_client.get(f"/api/v1/farm/folders/{nas['other']}/projects")).json()["project_ids"]
    assert linked == [nas["Vecchio"]]

    # Saving keeps the old link and adds a product; the single upstream column is emptied.
    both = await link(async_client, nas["other"], [nas["Vecchio"], nas["Nero"]])
    assert both == sorted([nas["Vecchio"], nas["Nero"]])
    await db_session.refresh(folder)
    assert folder.project_id is None
    assert await link(async_client, nas["other"], []) == []
    assert (await async_client.get(f"/api/v1/library/folders/by-project/{nas['Vecchio']}")).json() == []


@pytest.mark.asyncio
async def test_validation_and_cleanup(async_client, db_session, nas):
    r = await async_client.put(f"/api/v1/farm/folders/{nas['root']}/projects", json={"project_ids": [9999]})
    assert r.status_code == 422
    assert (await async_client.put("/api/v1/farm/folders/9999/projects", json={"project_ids": []})).status_code == 404

    await link(async_client, nas["root"], [nas["Nero"]])
    await db_session.delete(await db_session.get(Project, nas["Nero"]))
    await db_session.commit()
    assert (await db_session.execute(select(FarmFolderLink))).scalars().all() == []


@pytest.mark.asyncio
async def test_product_view_for_the_project_page(async_client, db_session, nas):
    from backend.app.farm.plates.models import FarmPlate, FarmPlateYield

    api = "/api/v1/farm"
    assert (await async_client.get(f"{api}/products/{nas['Nero']}/view")).status_code == 404  # not a product yet
    shape = (await async_client.post(f"{api}/shapes", json={"name": "Sopra"})).json()
    comp = (await async_client.post(f"{api}/components", json={"shape_id": shape["id"], "color": "Nero"})).json()
    body = {"project_id": nas["Nero"], "parts": [{"component_code": comp["code"], "quantity": 2}]}
    assert (await async_client.post(f"{api}/products", json=body)).status_code == 200
    await link(async_client, nas["root"], [nas["Nero"]])
    db_session.add(
        LibraryFile(folder_id=nas["orca"], filename="Sopra.stl", file_path="/x/Sopra.stl", file_type="stl", file_size=1)
    )
    orca_file = (await db_session.execute(select(LibraryFile).where(LibraryFile.filename == "Orca.3mf"))).scalar_one()
    plate = FarmPlate(library_file_id=orca_file.id, plate_index=1, printer_model="A1", materials=["PLA"])
    plate.status, plate.print_time_seconds = "confirmed", 3600
    plate.yields = [FarmPlateYield(object_name="sopra", quantity=15, shape_id=shape["id"])]
    db_session.add_all([plate, FarmPlate(library_file_id=orca_file.id, plate_index=2, printer_model="A1")])
    await db_session.commit()

    view = (await async_client.get(f"{api}/products/{nas['Nero']}/view")).json()
    [part] = view["parts"]
    assert (part["code"], part["shape"], part["color"], part["quantity"]) == (comp["code"], "Sopra", "Nero", 2)
    assert [(p["plate"], p["yield"], p["printer_model"], p["print_time_seconds"]) for p in part["plates"]] == [
        (1, 15, "A1", 3600)
    ]
    # Only 3MF projects of the linked folders, STL left out, with their plate counts.
    files = {f["filename"]: f for f in view["files"]}
    assert sorted(files) == ["Orca.3mf", "Prodotto X.3mf", "v2.3mf"]
    assert (files["Orca.3mf"]["confirmed"], files["Orca.3mf"]["proposed"], files["Orca.3mf"]["opened"]) == (1, 1, True)
    assert (files["v2.3mf"]["opened"], files["v2.3mf"]["folder"]) == (False, "Prodotto X / Orca / v2")
