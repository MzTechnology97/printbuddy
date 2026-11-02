"""Farm API: products (projects with SKUs and parts) and components with stable codes.

``GET /farm/products`` and ``GET /farm/components`` are the read-only contract
with Seller Hub, which caches them and keeps sales and component stock.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.projects import get_project_attachments_dir
from backend.app.core.auth import (
    caller_is_api_key,
    check_permission,
    get_api_key,
    require_permission_if_auth_enabled,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.plates.service import plates_by_component, plates_by_shape
from backend.app.farm.products.folders import linked_project_ids, set_linked_projects
from backend.app.farm.products.models import FarmComponent, FarmProduct, FarmProductPart, FarmShape
from backend.app.models.archive import PrintArchive
from backend.app.models.library import LibraryFile, LibraryFolder
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project
from backend.app.schemas.print_queue import serialize_utc_datetime

router = APIRouter(prefix="/farm", tags=["farm"])

CODE_RE = re.compile(r"^C-\d{4,6}$")
QTY_MAX = 1000  # same bound as Seller Hub's bills of materials
RECENT_DAYS = 7
# Only Amazon's image CDN: the import must not turn Printbuddy into a fetcher of arbitrary URLs.
AMAZON_IMAGE_RE = re.compile(r"^https://m\.media-amazon\.com/images/I/[\w.+%-]+\.(jpg|jpeg|png)$")
IMAGE_MAX_BYTES = 5_000_000
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

_create = Depends(require_permission_if_auth_enabled(Permission.PROJECTS_CREATE))
_update = Depends(require_permission_if_auth_enabled(Permission.PROJECTS_UPDATE))


async def _reader(
    _: Annotated[object, Depends(require_permission_if_auth_enabled(Permission.PROJECTS_READ))],
    is_key: Annotated[bool, Depends(caller_is_api_key)],
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Seller Hub reads with an API key, which needs ``can_read_status`` even while login is off."""
    if is_key:
        check_permission(await get_api_key(authorization, x_api_key, db), "read_status")


_read = Depends(_reader)


def _clean(value: str, limit: int) -> str:
    return " ".join(_CONTROL_RE.sub(" ", value).split())[:limit]


def _codes(values: list[str], label: str) -> list[str]:
    """Trimmed, de-duplicated identifiers (SKU/ASIN) in the given order."""
    out: list[str] = []
    for v in values:
        v = _clean(v, 100)
        if " " in v:
            raise HTTPException(422, f"{label} {v!r} contains spaces")
        if v and v not in out:
            out.append(v)
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PartIn(BaseModel):
    component_code: str = Field(pattern=CODE_RE.pattern)
    quantity: int = Field(default=1, ge=1, le=QTY_MAX)


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    skus: list[str] = Field(default_factory=list, max_length=100)
    asins: list[str] = Field(default_factory=list, max_length=100)
    parts: list[PartIn] = Field(default_factory=list, max_length=100)


class ProductCreate(ProductIn):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    # Turn an existing project into a product instead of creating a new one.
    project_id: int | None = None


class ComponentIn(BaseModel):
    # Without a name, a component with shape and colour is called "<shape> – <colour>".
    name: str | None = Field(default=None, min_length=1, max_length=200)
    shape_id: int | None = None
    color: str | None = Field(default=None, min_length=1, max_length=50)


class ComponentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    # Retired: no longer made; its code stays reserved and it can't go in new parts.
    retired: bool | None = None


class FolderProjectsIn(BaseModel):
    project_ids: list[int] = Field(default_factory=list, max_length=500)


class ShapeIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


# ---------------------------------------------------------------- read (Seller Hub contract)


async def _component_names(db: AsyncSession) -> dict[int, FarmComponent]:
    return {c.id: c for c in (await db.execute(select(FarmComponent))).scalars()}


@router.get("/products", dependencies=[_read])
async def list_products(db: AsyncSession = Depends(get_db)) -> dict:
    rows = (
        await db.execute(
            select(FarmProduct, Project).join(Project, Project.id == FarmProduct.project_id).order_by(Project.name)
        )
    ).all()
    components = await _component_names(db)
    parts_by_product: dict[int, list[FarmProductPart]] = {}
    for part in (await db.execute(select(FarmProductPart).order_by(FarmProductPart.sort_order))).scalars():
        parts_by_product.setdefault(part.project_id, []).append(part)
    # Plates yield shapes, so a component (shape + colour) gets every plate of its shape.
    by_shape = await plates_by_shape(db, [c.shape_id for c in components.values() if c.shape_id])
    by_code = await plates_by_component(db, [c.code for c in components.values() if not c.shape_id])

    def plates(c: FarmComponent) -> list[dict]:
        return by_shape.get(c.shape_id, []) if c.shape_id else by_code.get(c.code, [])

    products = []
    for product, project in rows:
        parts = [
            {
                "component_code": components[p.component_id].code,
                "name": components[p.component_id].name,
                "quantity": p.quantity,
                # Confirmed plates (file × printer model × material) and their yield of this component.
                "plates": plates(components[p.component_id]),
            }
            for p in parts_by_product.get(product.project_id, [])
        ]
        products.append(
            {
                "project_id": project.id,
                "name": project.name,
                "skus": product.skus or [],
                "asins": product.asins or [],
                "updated_at": serialize_utc_datetime(max(product.updated_at, project.updated_at)),
                "parts": parts,
            }
        )
    return {"generated_at": serialize_utc_datetime(_now()), "products": products}


@router.get("/components", dependencies=[_read])
async def list_components(db: AsyncSession = Depends(get_db)) -> list[dict]:
    shapes = {s.id: s.name for s in (await db.execute(select(FarmShape))).scalars()}
    rows = (await db.execute(select(FarmComponent).order_by(FarmComponent.code))).scalars()
    return [_component_dict(c, shapes) for c in rows]


def _component_dict(c: FarmComponent, shapes: dict[int, str]) -> dict:
    return {
        "code": c.code,
        "name": c.name,
        "shape_id": c.shape_id,
        "shape": shapes.get(c.shape_id) if c.shape_id else None,
        "color": c.color,
        "retired": c.retired_at is not None,
    }


@router.get("/projects", dependencies=[_read])
async def list_project_names(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Every project's id and name, for pickers (upstream ``GET /projects`` computes stats and takes ~1 s)."""
    rows = (await db.execute(select(Project.id, Project.name).order_by(Project.name))).all()
    return [{"id": pid, "name": name} for pid, name in rows]


@router.get("/shapes", dependencies=[_read])
async def list_shapes(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(FarmShape).order_by(FarmShape.name))).scalars()
    return [{"id": s.id, "name": s.name} for s in rows]


@router.get("/products/{project_id}/view", dependencies=[_read])
async def product_view(project_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """What the project page of a product shows: parts with their confirmed plates, and its 3MF files.

    404 for projects that are not products (the page then keeps the upstream panels).
    STL/STEP models are left out: only 3MF projects have plates.
    """
    from backend.app.farm.models import FarmLibraryFile
    from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
    from backend.app.farm.plates.printer_map import PrinterModelResolver
    from backend.app.farm.products.folders import project_folders

    product = await db.get(FarmProduct, project_id)
    if product is None:
        raise HTTPException(404, "Not a product")
    parts = (
        await db.execute(
            select(FarmProductPart, FarmComponent)
            .join(FarmComponent, FarmComponent.id == FarmProductPart.component_id)
            .where(FarmProductPart.project_id == project_id)
            .order_by(FarmProductPart.sort_order)
        )
    ).all()
    shapes = await _shape_names(db)
    shape_ids = {c.shape_id for _, c in parts if c.shape_id}
    codes = {c.code for _, c in parts if not c.shape_id}
    plates = list(
        (
            await db.execute(
                select(FarmPlate).where(
                    FarmPlate.status == "confirmed",
                    FarmPlate.yields.any(
                        FarmPlateYield.shape_id.in_(shape_ids) | FarmPlateYield.component_code.in_(codes)
                    ),
                )
            )
        ).scalars()
    )
    folders = await project_folders(db, project_id)
    files = list(
        (
            await db.execute(
                LibraryFile.active()
                .where(LibraryFile.folder_id.in_(folders), LibraryFile.filename.ilike("%.3mf"))
                .where(LibraryFile.filename.not_ilike("%.gcode.3mf"))
                .order_by(LibraryFile.filename)
            )
        ).scalars()
    )
    file_ids = {f.id for f in files} | {p.library_file_id for p in plates}
    paths = dict(
        (
            await db.execute(
                select(FarmLibraryFile.library_file_id, FarmLibraryFile.path).where(
                    FarmLibraryFile.library_file_id.in_(file_ids)
                )
            )
        ).all()
    )
    counts: dict[int, dict] = {}
    for file_id, status, n in (
        await db.execute(
            select(FarmPlate.library_file_id, FarmPlate.status, func.count())
            .where(FarmPlate.library_file_id.in_({f.id for f in files}))
            .group_by(FarmPlate.library_file_id, FarmPlate.status)
        )
    ).all():
        counts.setdefault(file_id, {"proposed": 0, "confirmed": 0})[status] = n
    resolver = await PrinterModelResolver.load(db)

    def plates_of(c: FarmComponent) -> list[dict]:
        out = []
        for p in plates:
            pieces = sum(
                y.quantity
                for y in p.yields
                if (c.shape_id and y.shape_id == c.shape_id) or (not c.shape_id and y.component_code == c.code)
            )
            if pieces:
                out.append(
                    {
                        "plate_id": p.id,
                        "library_file_id": p.library_file_id,
                        "file": paths.get(p.library_file_id, ""),
                        "plate": p.plate_index,
                        "printer_model": resolver.resolve(p.printer_model).target_model or p.printer_model,
                        "material": "+".join(p.materials or []),
                        "yield": pieces,
                        "print_time_seconds": p.print_time_seconds,
                        "filament_grams": p.filament_grams,
                        "sliced": p.sliced_file_id is not None,
                    }
                )
        return sorted(out, key=lambda o: (-o["yield"], o["printer_model"] or ""))

    return {
        "project_id": project_id,
        "skus": product.skus or [],
        "asins": product.asins or [],
        "parts": [
            {
                **_component_dict(c, shapes),
                "quantity": part.quantity,
                "plates": plates_of(c),
            }
            for part, c in parts
        ],
        "files": [
            {
                "library_file_id": f.id,
                "filename": f.filename,
                "path": paths.get(f.id, f.filename),
                "folder": folders.get(f.folder_id, ""),
                **counts.get(f.id, {"proposed": 0, "confirmed": 0}),
                "opened": f.id in counts,
            }
            for f in files
        ],
    }


@router.get("/products/overview", dependencies=[_read])
async def products_overview(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Per product: cover, queued and printing jobs, pieces printed in the last days (for the UI)."""
    rows = (
        await db.execute(
            select(Project.id, Project.cover_image_filename).join(FarmProduct, FarmProduct.project_id == Project.id)
        )
    ).all()
    out = {
        pid: {"project_id": pid, "has_cover": bool(cover), "queued": 0, "printing": 0, "printed_recent": 0}
        for pid, cover in rows
    }
    queue = await db.execute(
        select(PrintQueueItem.project_id, PrintQueueItem.status, func.count())
        .where(PrintQueueItem.project_id.in_(list(out)), PrintQueueItem.status.in_(["pending", "printing"]))
        .group_by(PrintQueueItem.project_id, PrintQueueItem.status)
    )
    for pid, status, n in queue.all():
        out[pid]["queued" if status == "pending" else "printing"] = n
    printed = await db.execute(
        select(PrintArchive.project_id, func.coalesce(func.sum(PrintArchive.quantity), 0))
        .where(
            PrintArchive.project_id.in_(list(out)),
            PrintArchive.status == "completed",
            PrintArchive.completed_at >= _now() - timedelta(days=RECENT_DAYS),
        )
        .group_by(PrintArchive.project_id)
    )
    for pid, n in printed.all():
        out[pid]["printed_recent"] = int(n)
    return list(out.values())


# ---------------------------------------------------------------- edit (UI)


async def _apply(db: AsyncSession, project: Project, product: FarmProduct, data: ProductIn) -> None:
    project.name = _clean(data.name, 255) or project.name
    skus, asins = _codes(data.skus, "SKU"), _codes(data.asins, "ASIN")
    others = (await db.execute(select(FarmProduct).where(FarmProduct.project_id != project.id))).scalars()
    for other in others:
        # Seller Hub maps sales to a product by SKU/ASIN: one owner each.
        clash = set(skus) & set(other.skus or []) or set(asins) & set(other.asins or [])
        if clash:
            raise HTTPException(409, f"{', '.join(sorted(clash))} already belongs to product {other.project_id}")

    codes = [p.component_code for p in data.parts]
    if len(set(codes)) != len(codes):
        raise HTTPException(422, "A component can appear only once per product")
    by_code = {
        c.code: c for c in (await db.execute(select(FarmComponent).where(FarmComponent.code.in_(codes)))).scalars()
    }
    if missing := [c for c in codes if c not in by_code]:
        raise HTTPException(422, f"Unknown components: {', '.join(missing)}")
    if retired := [c for c in codes if by_code[c].retired_at is not None]:
        raise HTTPException(422, f"Retired components: {', '.join(retired)}")

    product.skus, product.asins, product.updated_at = skus, asins, _now()
    for old in (await db.execute(select(FarmProductPart).where(FarmProductPart.project_id == project.id))).scalars():
        await db.delete(old)
    await db.flush()
    for i, p in enumerate(data.parts):
        db.add(
            FarmProductPart(
                project_id=project.id, component_id=by_code[p.component_code].id, quantity=p.quantity, sort_order=i
            )
        )


@router.post("/products", dependencies=[_create])
async def create_product(data: ProductCreate, db: AsyncSession = Depends(get_db)) -> dict:
    if data.project_id is not None:
        project = await db.get(Project, data.project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if await db.get(FarmProduct, project.id) is not None:
            raise HTTPException(409, "This project is already a product")
    elif data.name and _clean(data.name, 255):
        project = Project(name=_clean(data.name, 255))
        db.add(project)
        await db.flush()
    else:
        raise HTTPException(422, "Name or project_id is required")
    product = FarmProduct(project_id=project.id, skus=[], asins=[])
    db.add(product)
    await db.flush()
    await _apply(
        db,
        project,
        product,
        ProductIn(**{**data.model_dump(exclude={"project_id"}), "name": data.name or project.name}),
    )
    await db.commit()
    return {"project_id": project.id}


@router.put("/products/{project_id}", dependencies=[_update])
async def update_product(project_id: int, data: ProductIn, db: AsyncSession = Depends(get_db)) -> dict:
    product = await db.get(FarmProduct, project_id)
    project = await db.get(Project, project_id)
    if product is None or project is None:
        raise HTTPException(404, "Product not found")
    await _apply(db, project, product, data)
    await db.commit()
    return {"project_id": project_id}


@router.delete("/products/{project_id}", dependencies=[_update])
async def unmark_product(project_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Stop treating the project as a product; the project itself and its prints stay."""
    product = await db.get(FarmProduct, project_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    for part in (await db.execute(select(FarmProductPart).where(FarmProductPart.project_id == project_id))).scalars():
        await db.delete(part)
    await db.delete(product)
    await db.commit()
    return {"project_id": project_id}


async def _next_code(db: AsyncSession) -> str:
    # Codes are never deleted, so max + 1 never hands out a code twice.
    numbers = [int(c[2:]) for c in (await db.execute(select(FarmComponent.code))).scalars()]
    return f"C-{max(numbers, default=0) + 1:04d}"


async def _shape_names(db: AsyncSession) -> dict[int, str]:
    return {s.id: s.name for s in (await db.execute(select(FarmShape))).scalars()}


@router.post("/components", dependencies=[_create])
async def create_component(data: ComponentIn, db: AsyncSession = Depends(get_db)) -> dict:
    shapes = await _shape_names(db)
    color = _clean(data.color, 50) if data.color else None
    if data.shape_id is not None:
        if data.shape_id not in shapes:
            raise HTTPException(422, "Unknown shape")
        twin = await db.scalar(
            select(FarmComponent).where(
                FarmComponent.shape_id == data.shape_id,
                func.lower(FarmComponent.color) == (color or "").lower(),
                FarmComponent.retired_at.is_(None),
            )
        )
        if twin is not None:
            raise HTTPException(409, f"{twin.code} is already this shape in this colour")
    default = " – ".join(x for x in (shapes.get(data.shape_id or 0), color) if x)
    name = _clean(data.name or default, 200)
    if not name:
        raise HTTPException(422, "Name, or shape and colour, required")
    component = FarmComponent(code=await _next_code(db), name=name, shape_id=data.shape_id, color=color)
    db.add(component)
    await db.commit()
    return _component_dict(component, shapes)


@router.patch("/components/{code}", dependencies=[_update])
async def update_component(code: str, data: ComponentUpdate, db: AsyncSession = Depends(get_db)) -> dict:
    component = (await db.execute(select(FarmComponent).where(FarmComponent.code == code))).scalar_one_or_none()
    if component is None:
        raise HTTPException(404, "Component not found")
    if data.name is not None:
        component.name = _clean(data.name, 200) or component.name
    if data.retired is not None:
        component.retired_at = (component.retired_at or _now()) if data.retired else None
    await db.commit()
    return _component_dict(component, await _shape_names(db))


@router.post("/shapes", dependencies=[_create])
async def create_shape(data: ShapeIn, db: AsyncSession = Depends(get_db)) -> dict:
    name = _clean(data.name, 200)
    if not name:
        raise HTTPException(422, "Name is required")
    if await db.scalar(select(FarmShape).where(func.lower(FarmShape.name) == name.lower())):
        raise HTTPException(409, "A shape with this name already exists")
    shape = FarmShape(name=name)
    db.add(shape)
    await db.commit()
    return {"id": shape.id, "name": shape.name}


@router.patch("/shapes/{shape_id}", dependencies=[_update])
async def rename_shape(shape_id: int, data: ShapeIn, db: AsyncSession = Depends(get_db)) -> dict:
    shape = await db.get(FarmShape, shape_id)
    if shape is None:
        raise HTTPException(404, "Shape not found")
    name = _clean(data.name, 200) or shape.name
    if await db.scalar(select(FarmShape).where(func.lower(FarmShape.name) == name.lower(), FarmShape.id != shape_id)):
        raise HTTPException(409, "A shape with this name already exists")
    shape.name = name
    await db.commit()
    return {"id": shape.id, "name": shape.name}


# ---------------------------------------------------------------- library folders <-> projects

_folders_read = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_READ))
_folders_write = Depends(require_permission_if_auth_enabled(Permission.LIBRARY_UPDATE_ALL))


@router.get("/folders/{folder_id}/projects", dependencies=[_folders_read])
async def get_folder_projects(folder_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Projects and products linked to a library folder (its subfolders come with it)."""
    if await db.get(LibraryFolder, folder_id) is None:
        raise HTTPException(404, "Folder not found")
    return {"folder_id": folder_id, "project_ids": await linked_project_ids(db, folder_id)}


@router.put("/folders/{folder_id}/projects", dependencies=[_folders_write])
async def put_folder_projects(folder_id: int, data: FolderProjectsIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Link a folder to several projects/products at once; an empty list unlinks it."""
    folder = await db.get(LibraryFolder, folder_id)
    if folder is None:
        raise HTTPException(404, "Folder not found")
    found = set((await db.execute(select(Project.id).where(Project.id.in_(data.project_ids)))).scalars())
    if missing := sorted(set(data.project_ids) - found):
        raise HTTPException(422, f"Unknown projects: {missing}")
    await set_linked_projects(db, folder, data.project_ids)
    await db.commit()
    return {"folder_id": folder_id, "project_ids": await linked_project_ids(db, folder_id)}


from backend.app.farm.plan.routes import router as plan_router  # noqa: E402  (plan imports products models)

router.include_router(plan_router)

from backend.app.farm.jobs.routes import router as jobs_router  # noqa: E402

router.include_router(jobs_router)


# ---------------------------------------------------------------- one-off import from Seller Hub


async def _fetch_image(url: str) -> bytes | None:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        return None
    ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("image/")
    return r.content if ok and len(r.content) <= IMAGE_MAX_BYTES else None


async def _set_amazon_cover(project: Project, url: object) -> bool:
    """Save the Amazon listing image as the project cover, unless it already has one."""
    if project.cover_image_filename or not isinstance(url, str) or not AMAZON_IMAGE_RE.match(url):
        return False
    content = await _fetch_image(url)
    if content is None:
        return False
    folder = get_project_attachments_dir(project.id)
    folder.mkdir(parents=True, exist_ok=True)
    filename = f"cover_{uuid.uuid4().hex}{os.path.splitext(url)[1].lower()}"
    (folder / filename).write_bytes(content)
    project.cover_image_filename = filename
    return True


@router.post("/import/sellerhub", dependencies=[_create])
async def import_sellerhub(plan: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """Import components and bills of materials from Seller Hub's ``GET /api/print-plan`` body.

    Keeps Seller Hub's ``C-…`` codes (its stock is keyed by them). Idempotent: existing
    components keep their name, and a product whose SKU or ASIN already exists (or, without
    either, whose name does) is skipped. Products without a photo get the Amazon image.
    """
    parts_by_key = {p["key"]: p for p in plan.get("parts") or [] if isinstance(p, dict) and p.get("key")}
    existing = {c.code: c for c in (await db.execute(select(FarmComponent))).scalars()}
    result = {
        "components_created": 0,
        "components_existing": 0,
        "products_created": 0,
        "products_skipped": 0,
        "covers_added": 0,
    }

    boms: dict[str, list[tuple[str, int]]] = {}
    names: dict[str, str] = {}
    for comp in plan.get("components") or []:
        code = comp.get("code") if isinstance(comp, dict) else None
        if not isinstance(code, str) or not CODE_RE.match(code):
            continue
        if code in existing:
            result["components_existing"] += 1
        else:
            existing[code] = FarmComponent(code=code, name=_clean(str(comp.get("name") or code), 200) or code)
            db.add(existing[code])
            result["components_created"] += 1
        for use in comp.get("products") or []:
            if not isinstance(use, dict) or not isinstance(use.get("key"), str):
                continue
            qty = use.get("qty")
            if isinstance(qty, bool) or not isinstance(qty, int):
                continue
            if 1 <= qty <= QTY_MAX:
                boms.setdefault(use["key"], []).append((code, qty))
                names.setdefault(use["key"], str(use.get("name") or ""))
    await db.flush()

    # SKU / ASIN / name (casefolded) → project_id of the product that owns it.
    owners: dict[str, int] = {}
    for p, name in (await db.execute(select(FarmProduct, Project.name).join(Project))).all():
        owners.update({f"sku:{s}": p.project_id for s in p.skus or []})
        owners.update({f"asin:{a}": p.project_id for a in p.asins or []})
        owners.setdefault(f"name:{name.casefold()}", p.project_id)

    for key, lines in boms.items():
        part = parts_by_key.get(key, {})
        rows = [s for s in part.get("skus") or [] if isinstance(s, dict)]
        skus = _codes([str(s.get("sku") or "") for s in rows] or ([key[4:]] if key.startswith("sku:") else []), "SKU")
        asins = _codes(
            [str(s.get("asin") or "") for s in rows] or ([key[5:]] if key.startswith("asin:") else []), "ASIN"
        )
        name = _clean(str(part.get("name") or names[key] or key), 255)
        # Amazon has distinct listings with the same title: the name only identifies products without SKU/ASIN.
        ids = [f"sku:{s}" for s in skus] + [f"asin:{a}" for a in asins] or [f"name:{name.casefold()}"]
        owner = next((owners[i] for i in ids if i in owners), None)
        if owner is not None:
            project = await db.get(Project, owner)
            result["products_skipped"] += 1
        else:
            project = Project(name=name)
            db.add(project)
            await db.flush()
            db.add(FarmProduct(project_id=project.id, skus=skus, asins=asins))
            await db.flush()
            for i, (code, qty) in enumerate(lines):
                db.add(
                    FarmProductPart(project_id=project.id, component_id=existing[code].id, quantity=qty, sort_order=i)
                )
            owners.update(dict.fromkeys(ids, project.id))
            result["products_created"] += 1
        if await _set_amazon_cover(project, part.get("image_url")):
            result["covers_added"] += 1

    await db.commit()
    return result
