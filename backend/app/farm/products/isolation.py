"""Every shape and component inside one product: the move from shared rows, and its check.

Shapes (a piece's geometry) and components (its stock code) used to serve every product
holding them, so a mistake on one product changed others (Audi Q3 hooks printed for Land
Rover gutters). Now each belongs to one product (``project_id``): a shape knows its pieces by
geometry (``fingerprints``; ``object_names`` for objects without a measured mesh), inside
that product's 3MFs only.

``POST /farm/migrations/isolate-products`` moves the existing data, a dry run by default:
- a shape goes to the product with the lowest id among those whose components use it (else
  whose 3MFs hold it) and keeps its id; every other product holding it gets a copy with the
  same name, and each keeps only the geometries found in its own 3MFs;
- a component goes to its product with the lowest id and keeps its code; each other product
  gets a new code (stock 0 in Seller Hub) with the same name, colour and its own shapes;
- an active component in no product is retired (reversible, the code stays).
Rows that already have a product are left alone, so a second run changes nothing.

``GET /farm/checks/isolation`` lists anything still shared, read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.farm.plates.geometry import same_piece
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.products import routes as products
from backend.app.farm.products.folders import file_products
from backend.app.farm.products.models import (
    FarmComponent,
    FarmProduct,
    FarmProductPart,
    FarmShape,
    component_shapes,
    not_part,
)
from backend.app.models.project import Project

router = APIRouter(tags=["farm"])  # mounted under /farm by the products router


def _measured(fp: list | None) -> bool:
    return bool(fp) and bool(fp[0])


async def _objects(db: AsyncSession, product_rows: dict[int, FarmProduct]) -> dict[int, dict[int, list]]:
    """Objects linked to a shape in each product's 3MFs: ``{project_id: {shape_id: [(fingerprint, name)]}}``.

    Pieces the product marked as not part of it are left out.
    """
    files = await file_products(db)
    rows = await db.execute(
        select(
            FarmPlate.library_file_id, FarmPlateYield.shape_id, FarmPlateYield.fingerprint, FarmPlateYield.object_name
        )
        .join(FarmPlate, FarmPlate.id == FarmPlateYield.plate_id)
        .where(FarmPlateYield.shape_id.isnot(None), FarmPlate.library_file_id.in_(files))
    )
    out: dict[int, dict[int, list]] = {}
    for file_id, shape_id, fp, name in rows.all():
        for pid in files[file_id]:
            if pid in product_rows and not not_part(product_rows[pid], fp):
                out.setdefault(pid, {}).setdefault(shape_id, []).append((fp, name))
    return out


def _geometry(objects: list) -> tuple[list, list]:
    """Distinct measured fingerprints, and the names of objects without a measured mesh."""
    fps: list = []
    names: list = []
    for fp, name in objects:
        if not _measured(fp):
            if name not in names:
                names.append(name)
        elif not any(same_piece(fp, f) for f in fps):
            fps.append(fp)
    return fps, names


async def isolate(db: AsyncSession) -> dict:
    """Give every shape and component one product (see the module doc). The caller commits or rolls back."""
    product_rows = {p.project_id: p for p in (await db.execute(select(FarmProduct))).scalars()}
    names = dict((await db.execute(select(Project.id, Project.name).where(Project.id.in_(product_rows)))).all())
    objects = await _objects(db, product_rows)
    parts = list((await db.execute(select(FarmProductPart).order_by(FarmProductPart.project_id))).scalars())
    components = {c.id: c for c in (await db.execute(select(FarmComponent).order_by(FarmComponent.id))).scalars()}
    shapes = {s.id: s for s in (await db.execute(select(FarmShape).order_by(FarmShape.id))).scalars()}

    users: dict[int, list[int]] = {}  # component -> products, lowest id first
    for part in parts:
        if part.project_id not in users.setdefault(part.component_id, []):
            users[part.component_id].append(part.project_id)
    by_parts: dict[int, set[int]] = {}  # product -> shapes its components are printed as
    for part in parts:
        by_parts.setdefault(part.project_id, set()).update(component_shapes(components[part.component_id]))

    shape_for: dict[tuple[int, int], int] = {}  # (product, old shape) -> the product's own shape
    shape_rows, unused = [], []
    for sid, shape in shapes.items():
        if shape.project_id is not None:
            shape_for[shape.project_id, sid] = sid
            continue
        holders = sorted(pid for pid in product_rows if sid in by_parts.get(pid, ()) or sid in objects.get(pid, {}))
        if not holders:
            unused.append({"shape_id": sid, "name": shape.name})
            continue
        owner = next((pid for pid in holders if sid in by_parts.get(pid, ())), holders[0])
        for pid in holders:
            fps, unmeasured = _geometry(objects.get(pid, {}).get(sid, []))
            if pid == owner:
                shape.project_id, shape.fingerprints, shape.object_names = pid, fps, unmeasured or None
                target = shape
            else:
                target = FarmShape(name=shape.name, project_id=pid, fingerprints=fps, object_names=unmeasured or None)
                db.add(target)
                await db.flush()
            shape_for[pid, sid] = target.id
            shape_rows.append(
                {
                    "shape_id": sid,
                    "name": shape.name,
                    "project_id": pid,
                    "product": names.get(pid),
                    "new_shape_id": None if target is shape else target.id,
                    "geometries": len(fps),
                    "unmeasured": unmeasured,
                }
            )

    def own(comp: FarmComponent, pid: int, source: FarmComponent) -> None:
        comp.project_id = pid
        if source.shape_id:
            comp.shape_id = shape_for.get((pid, source.shape_id), source.shape_id)
        if source.recipe is not None:
            comp.recipe = [
                {"shape_id": shape_for.get((pid, r["shape_id"]), r["shape_id"]), "quantity": r["quantity"]}
                for r in source.recipe
            ]

    assigned, split, retired = 0, [], []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for cid, comp in components.items():
        if comp.project_id is not None:
            continue
        holders = users.get(cid, [])
        if not holders:
            if comp.retired_at is None:
                comp.retired_at = now
                retired.append({"code": comp.code, "name": comp.name})
            continue
        own(comp, holders[0], comp)
        assigned += 1
        new = []
        for pid in holders[1:]:
            twin = FarmComponent(
                code=await products._next_code(db), name=comp.name, color=comp.color, retired_at=comp.retired_at
            )
            own(twin, pid, comp)
            db.add(twin)
            await db.flush()
            for part in parts:
                if part.project_id == pid and part.component_id == cid:
                    part.component_id = twin.id
            new.append({"project_id": pid, "product": names.get(pid), "code": twin.code})
        if new:
            split.append(
                {
                    "code": comp.code,
                    "name": comp.name,
                    "kept_by": holders[0],
                    "product": names.get(holders[0]),
                    "new": new,
                }
            )
    await db.flush()
    return {
        "products": len(product_rows),
        "shapes": {
            "kept": sum(1 for r in shape_rows if r["new_shape_id"] is None),
            "copied": sum(1 for r in shape_rows if r["new_shape_id"] is not None),
            "unused": unused,
            "rows": shape_rows,
        },
        "components": {"assigned": assigned, "split": split, "retired": retired},
    }


async def check(db: AsyncSession) -> dict:
    """Anything a product shares with another: components and shapes without one product, or crossing."""
    shapes = {s.id: s for s in (await db.execute(select(FarmShape))).scalars()}
    problems = []
    rows = await db.execute(
        select(FarmProductPart.project_id, FarmComponent).join(
            FarmComponent, FarmComponent.id == FarmProductPart.component_id
        )
    )
    for project_id, comp in rows.all():
        if comp.project_id != project_id:
            problems.append(
                {
                    "problem": "component_of_another_product",
                    "code": comp.code,
                    "project_id": project_id,
                    "owner": comp.project_id,
                }
            )
    for comp in (await db.execute(select(FarmComponent).where(FarmComponent.retired_at.is_(None)))).scalars():
        if comp.project_id is None:
            problems.append({"problem": "component_without_product", "code": comp.code})
            continue
        for sid in sorted(component_shapes(comp)):
            shape = shapes.get(sid)
            if shape is None or shape.project_id != comp.project_id:
                problems.append(
                    {
                        "problem": "shape_of_another_product",
                        "code": comp.code,
                        "shape_id": sid,
                        "owner": shape.project_id if shape else None,
                    }
                )
    return {"ok": not problems, "problems": problems}


@router.post("/migrations/isolate-products", dependencies=[products._update])
async def isolate_products(dry_run: bool = True, db: AsyncSession = Depends(get_db)) -> dict:
    """Move shared shapes and components to one product each; ``dry_run`` (default) only reports it."""
    report = await isolate(db)
    if dry_run:
        await db.rollback()
    else:
        await db.commit()
    return {"dry_run": dry_run, **report}


@router.get("/checks/isolation", dependencies=[products._read])
async def isolation_check(db: AsyncSession = Depends(get_db)) -> dict:
    return await check(db)
