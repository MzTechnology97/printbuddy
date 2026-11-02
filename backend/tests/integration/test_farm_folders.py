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
