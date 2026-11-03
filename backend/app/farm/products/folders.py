"""Library folders linked to projects and products: several per folder, subfolders included.

Upstream links a folder to one project (``LibraryFolder.project_id``) and the project
shows only the files directly inside it. On the NAS a product's folder holds slicer
subfolders (Orca, Bambu, …) and one folder can serve several products (colour
variants), so links live in ``farm_folder_links`` and a link covers the subfolders.
Upstream ``project_id`` links still count.

A product can leave out single 3MF projects of its folders (``farm_excluded_files``). A file
no product uses (every product holding it excludes it) gets no plates, no re-slice, and its
stored plates are hidden from the plan and Accoda; they come back when it is included again.
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, String, delete, event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.project import Project


class FarmFolderLink(Base):
    __tablename__ = "farm_folder_links"

    folder_id: Mapped[int] = mapped_column(ForeignKey("library_folders.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)


class FarmExcludedFile(Base):
    """A 3MF project a product leaves out, by its path in the watched folder (``farm_library_files.path``).

    shortcut: keyed by path, so a renamed or moved file is included again; key it by
    ``farm_library_files.id`` if that gets annoying.
    """

    __tablename__ = "farm_excluded_files"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    path: Mapped[str] = mapped_column(String(1000), primary_key=True)


@event.listens_for(Project, "after_delete")
def _drop_project_links(mapper, connection, target) -> None:
    # SQLite runs without foreign_keys, and may hand the freed id to the next project.
    connection.execute(delete(FarmFolderLink).where(FarmFolderLink.project_id == target.id))
    connection.execute(delete(FarmExcludedFile).where(FarmExcludedFile.project_id == target.id))


@event.listens_for(LibraryFolder, "after_delete")
def _drop_folder_links(mapper, connection, target) -> None:
    connection.execute(delete(FarmFolderLink).where(FarmFolderLink.folder_id == target.id))


async def linked_project_ids(db: AsyncSession, folder_id: int) -> list[int]:
    ids = set(
        (await db.execute(select(FarmFolderLink.project_id).where(FarmFolderLink.folder_id == folder_id))).scalars()
    )
    upstream = await db.scalar(select(LibraryFolder.project_id).where(LibraryFolder.id == folder_id))
    return sorted(ids | ({upstream} if upstream else set()))


async def set_linked_projects(db: AsyncSession, folder: LibraryFolder, project_ids: list[int]) -> None:
    """Replace the folder's project links; the upstream single link moves into the farm table."""
    await db.execute(delete(FarmFolderLink).where(FarmFolderLink.folder_id == folder.id))
    db.add_all(FarmFolderLink(folder_id=folder.id, project_id=pid) for pid in dict.fromkeys(project_ids))
    folder.project_id = None


async def project_folders(db: AsyncSession, project_id: int) -> dict[int, str]:
    """Folders of a project: each linked folder, then its subfolders. ``{folder_id: "Folder / Orca"}``."""
    rows = (await db.execute(select(LibraryFolder.id, LibraryFolder.parent_id, LibraryFolder.name))).all()
    names = {fid: name for fid, _, name in rows}
    children: dict[int | None, list[int]] = {}
    for fid, parent, _ in rows:
        children.setdefault(parent, []).append(fid)
    roots = set(
        (await db.execute(select(FarmFolderLink.folder_id).where(FarmFolderLink.project_id == project_id))).scalars()
    )
    roots |= set((await db.execute(select(LibraryFolder.id).where(LibraryFolder.project_id == project_id))).scalars())

    labels: dict[int, str] = {}
    for root in sorted(roots & names.keys(), key=lambda f: names[f].casefold()):
        queue = [(root, names[root])]
        while queue:
            fid, label = queue.pop(0)
            if fid in labels:
                continue  # also under another linked folder
            labels[fid] = label
            kids = sorted(children.get(fid, []), key=lambda f: names[f].casefold())
            queue[:0] = [(kid, f"{label} / {names[kid]}") for kid in kids]
    return labels


async def linked_folders(db: AsyncSession, project_id: int) -> list[int]:
    """Folders linked to the project itself (their subfolders come with them)."""
    ids = set(
        (await db.execute(select(FarmFolderLink.folder_id).where(FarmFolderLink.project_id == project_id))).scalars()
    )
    ids |= set((await db.execute(select(LibraryFolder.id).where(LibraryFolder.project_id == project_id))).scalars())
    return sorted(ids)


async def excluded_by(db: AsyncSession, project_id: int) -> set[int]:
    """Library file ids of the 3MF projects this product leaves out."""
    from backend.app.farm.models import FarmLibraryFile

    paths = select(FarmExcludedFile.path).where(FarmExcludedFile.project_id == project_id)
    query = select(FarmLibraryFile.library_file_id).where(
        FarmLibraryFile.path.in_(paths), FarmLibraryFile.library_file_id.isnot(None)
    )
    return set((await db.execute(query)).scalars())


async def excluded_files(db: AsyncSession) -> set[int]:
    """Library file ids no product uses: every product whose linked folders hold the file excludes it."""
    from backend.app.farm.models import FarmLibraryFile
    from backend.app.farm.products.models import FarmProduct

    excluded: dict[str, set[int]] = {}
    for project_id, path in (await db.execute(select(FarmExcludedFile.project_id, FarmExcludedFile.path))).all():
        excluded.setdefault(path, set()).add(project_id)
    if not excluded:
        return set()
    products = set((await db.execute(select(FarmProduct.project_id))).scalars())
    folders = {
        fid: (parent, pid)
        for fid, parent, pid in (
            await db.execute(select(LibraryFolder.id, LibraryFolder.parent_id, LibraryFolder.project_id))
        ).all()
    }
    links: dict[int, set[int]] = {}
    for fid, pid in (await db.execute(select(FarmFolderLink.folder_id, FarmFolderLink.project_id))).all():
        links.setdefault(fid, set()).add(pid)

    def holders(fid: int | None) -> set[int]:  # products linking the folder or one above it
        seen, out = set(), set()
        while fid in folders and fid not in seen:
            seen.add(fid)
            parent, upstream = folders[fid]
            out |= links.get(fid, set()) | ({upstream} if upstream else set())
            fid = parent
        return out & products

    rows = await db.execute(
        select(FarmLibraryFile.library_file_id, FarmLibraryFile.path, LibraryFile.folder_id)
        .join(LibraryFile, LibraryFile.id == FarmLibraryFile.library_file_id)
        .where(FarmLibraryFile.path.in_(excluded))
    )
    out = set()
    for file_id, path, folder_id in rows.all():
        users = holders(folder_id)
        if users and users <= excluded[path]:
            out.add(file_id)
    return out


async def decorate_tree(db: AsyncSession, folder_map: dict) -> None:
    """Show every linked project on the File Manager tree (upstream shows one)."""
    rows = (
        await db.execute(
            select(FarmFolderLink.folder_id, Project.id, Project.name)
            .join(Project, Project.id == FarmFolderLink.project_id)
            .order_by(Project.name)
        )
    ).all()
    for folder_id, project_id, name in rows:
        item = folder_map.get(folder_id)
        if item is None:
            continue
        if item.project_id is None:
            item.project_id, item.project_name = project_id, name
        elif item.project_id != project_id:
            item.project_name = f"{item.project_name}, {name}"
