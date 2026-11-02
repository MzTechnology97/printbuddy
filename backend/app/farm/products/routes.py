"""Farm API: products (projects with SKUs and parts) and components with stable codes.

``GET /farm/products`` and ``GET /farm/components`` are the read-only contract
with Seller Hub, which caches them and keeps sales and component stock.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import (
    caller_is_api_key,
    check_permission,
    get_api_key,
    require_permission_if_auth_enabled,
)
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.products.models import FarmComponent, FarmProduct, FarmProductPart
from backend.app.models.archive import PrintArchive
from backend.app.models.print_queue import PrintQueueItem
from backend.app.models.project import Project
from backend.app.schemas.print_queue import serialize_utc_datetime

router = APIRouter(prefix="/farm", tags=["farm"])

CODE_RE = re.compile(r"^C-\d{4,6}$")
QTY_MAX = 1000  # same bound as Seller Hub's bills of materials
RECENT_DAYS = 7
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

    products = []
    for product, project in rows:
        parts = [
            {
                "component_code": components[p.component_id].code,
                "name": components[p.component_id].name,
                "quantity": p.quantity,
                # Filled by the plates module (file × printer model × material → yield per component).
                "plates": [],
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
    rows = (await db.execute(select(FarmComponent).order_by(FarmComponent.code))).scalars()
    return [{"code": c.code, "name": c.name} for c in rows]


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


@router.post("/components", dependencies=[_create])
async def create_component(data: ComponentIn, db: AsyncSession = Depends(get_db)) -> dict:
    name = _clean(data.name, 200)
    if not name:
        raise HTTPException(422, "Name is required")
    component = FarmComponent(code=await _next_code(db), name=name)
    db.add(component)
    await db.commit()
    return {"code": component.code, "name": component.name}


@router.patch("/components/{code}", dependencies=[_update])
async def rename_component(code: str, data: ComponentIn, db: AsyncSession = Depends(get_db)) -> dict:
    component = (await db.execute(select(FarmComponent).where(FarmComponent.code == code))).scalar_one_or_none()
    if component is None:
        raise HTTPException(404, "Component not found")
    component.name = _clean(data.name, 200) or component.name
    await db.commit()
    return {"code": component.code, "name": component.name}


# ---------------------------------------------------------------- one-off import from Seller Hub


@router.post("/import/sellerhub", dependencies=[_create])
async def import_sellerhub(plan: dict, db: AsyncSession = Depends(get_db)) -> dict:
    """Import components and bills of materials from Seller Hub's ``GET /api/print-plan`` body.

    Keeps Seller Hub's ``C-…`` codes (its stock is keyed by them). Idempotent: existing
    components keep their name, and a product whose SKU, ASIN or name already exists is skipped.
    """
    parts_by_key = {p["key"]: p for p in plan.get("parts") or [] if isinstance(p, dict) and p.get("key")}
    existing = {c.code: c for c in (await db.execute(select(FarmComponent))).scalars()}
    result = {"components_created": 0, "components_existing": 0, "products_created": 0, "products_skipped": 0}

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

    taken_skus: set[str] = set()
    taken_asins: set[str] = set()
    for p in (await db.execute(select(FarmProduct))).scalars():
        taken_skus.update(p.skus or [])
        taken_asins.update(p.asins or [])
    taken_names = {
        n.casefold()
        for n in (
            await db.execute(select(Project.name).join(FarmProduct, FarmProduct.project_id == Project.id))
        ).scalars()
    }

    for key, lines in boms.items():
        part = parts_by_key.get(key, {})
        rows = [s for s in part.get("skus") or [] if isinstance(s, dict)]
        skus = _codes([str(s.get("sku") or "") for s in rows] or ([key[4:]] if key.startswith("sku:") else []), "SKU")
        asins = _codes(
            [str(s.get("asin") or "") for s in rows] or ([key[5:]] if key.startswith("asin:") else []), "ASIN"
        )
        name = _clean(str(part.get("name") or names[key] or key), 255)
        if taken_skus & set(skus) or taken_asins & set(asins) or name.casefold() in taken_names:
            result["products_skipped"] += 1
            continue
        project = Project(name=name)
        db.add(project)
        await db.flush()
        db.add(FarmProduct(project_id=project.id, skus=skus, asins=asins))
        await db.flush()
        for i, (code, qty) in enumerate(lines):
            db.add(FarmProductPart(project_id=project.id, component_id=existing[code].id, quantity=qty, sort_order=i))
        taken_skus.update(skus)
        taken_asins.update(asins)
        taken_names.add(name.casefold())
        result["products_created"] += 1

    await db.commit()
    return result
