"""Farm API: the pieces of a product, mapped from the product page in one step.

The distinct pieces (recognised by geometry, :mod:`backend.app.farm.plates.geometry`) found in
the 3MF files of the product's linked folders, each with a picture of the piece alone. Saving
"how many per product" links the piece to a shape everywhere, gets the component of that shape
in the product's colour (an existing one without a shape can take it, keeping its code) and
sets the product's part.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import RequireCameraStreamTokenIfAuthEnabled
from backend.app.core.database import get_db
from backend.app.farm.nas.access import read_file, run as access_run
from backend.app.farm.plan import service as plan
from backend.app.farm.plates.geometry import group
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.plates.printer_map import PrinterModelResolver
from backend.app.farm.plates.render import render_png
from backend.app.farm.plates.service import link_by_geometry, object_mesh, suggest_shapes
from backend.app.farm.products import routes as products
from backend.app.farm.products.folders import project_folders
from backend.app.farm.products.models import FarmComponent, FarmProduct, FarmProductPart, FarmShape
from backend.app.models.library import LibraryFile

router = APIRouter(tags=["farm"])  # mounted under /farm by the products router


async def _product(db: AsyncSession, project_id: int) -> FarmProduct:
    product = await db.get(FarmProduct, project_id)
    if product is None:
        raise HTTPException(404, "Not a product")
    return product


async def _parts(db: AsyncSession, project_id: int) -> list[tuple[FarmProductPart, FarmComponent]]:
    return list(
        (
            await db.execute(
                select(FarmProductPart, FarmComponent)
                .join(FarmComponent, FarmComponent.id == FarmProductPart.component_id)
                .where(FarmProductPart.project_id == project_id)
            )
        ).all()
    )


@router.get("/products/{project_id}/pieces", dependencies=[products._read])
async def product_pieces(project_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Distinct pieces in the 3MF projects of the product's folders, with their part in the product."""
    await _product(db, project_id)
    folders = await project_folders(db, project_id)
    files = dict(
        (
            await db.execute(
                select(LibraryFile.id, LibraryFile.filename)
                .where(LibraryFile.deleted_at.is_(None), LibraryFile.folder_id.in_(folders))
                .where(LibraryFile.filename.ilike("%.3mf"), LibraryFile.filename.not_ilike("%.gcode.3mf"))
            )
        ).all()
    )
    rows = (
        await db.execute(
            select(FarmPlateYield, FarmPlate.library_file_id, FarmPlate.printer_model)
            .join(FarmPlate, FarmPlate.id == FarmPlateYield.plate_id)
            .where(FarmPlate.library_file_id.in_(files))
            .order_by(FarmPlateYield.id)
        )
    ).all()
    rows = [r for r in rows if r[0].fingerprint]  # filtered before grouping: same pieces as the Pieces tab
    parts = await _parts(db, project_id)
    by_shape = {c.shape_id: (part, c) for part, c in parts if c.shape_id}
    shapes = await products._shape_names(db)
    colors = (await plan.load_config(db))["colors"]
    resolver = await PrinterModelResolver.load(db)
    product_colors = Counter(c.color for _, c in parts if c.color)

    pieces = []
    for members in group([r[0].fingerprint for r in rows]):
        items = [rows[i] for i in members]
        names = Counter(y.object_name for y, _, _ in items)
        shape_counts = Counter(y.shape_id for y, _, _ in items if y.shape_id is not None)
        shape_id = shape_counts.most_common(1)[0][0] if shape_counts else None
        part, comp = by_shape.get(shape_id, (None, None))
        models = {resolver.resolve(m).target_model or m for _, _, m in items if m}
        pieces.append(
            {
                "yield_ids": [y.id for y, _, _ in items],
                "image_yield_id": items[0][0].id,
                "names": [n for n, _ in names.most_common(5)],
                "plates": len({y.plate_id for y, _, _ in items}),
                "files": sorted({files[f] for _, f, _ in items})[:3],
                "file_count": len({f for _, f, _ in items}),
                "printer_models": sorted(models),
                "hand": int(items[0][0].fingerprint[5]),
                "shape_id": shape_id,
                "shape": shapes.get(shape_id) if shape_id else None,
                "mixed": len(shape_counts) > 1,
                "suggested_shape_id": None,
                "quantity": part.quantity if part else None,
                "component": None
                if comp is None
                else {
                    "code": comp.code,
                    "name": comp.name,
                    "color": comp.color,
                    "color_hex": colors.get((comp.color or "").lower()),
                },
            }
        )
    open_names = sorted({n for p in pieces if p["shape_id"] is None for n in p["names"]})
    proposals = await suggest_shapes(db, open_names)
    for p in pieces:
        if p["shape_id"] is None:
            p["suggested_shape_id"] = next((proposals[n] for n in p["names"] if n in proposals), None)
    pieces.sort(key=lambda p: (p["quantity"] is not None, -p["plates"]))  # still to decide first
    return {
        "default_color": product_colors.most_common(1)[0][0] if product_colors else None,
        # Parts imported without a shape (whole products from Seller Hub): a piece can take one of them.
        "unshaped_parts": [
            {"code": c.code, "name": c.name, "quantity": part.quantity}
            for part, c in parts
            if c.shape_id is None and c.retired_at is None
        ],
        "pieces": pieces,
    }


@router.get("/pieces/{yield_id}/image")
async def piece_image(
    yield_id: int,
    color: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """PNG of one object alone, in ``color`` (hex); kept until the 3MF changes."""
    from backend.app.api.routes import library as lib

    y = await db.get(FarmPlateYield, yield_id)
    plate = await db.get(FarmPlate, y.plate_id) if y else None
    lib_file = await db.scalar(LibraryFile.active().where(LibraryFile.id == plate.library_file_id)) if plate else None
    if lib_file is None:
        raise HTTPException(404, "Piece not found")
    try:
        color = plan.clean_hex(color) if color else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    key = hashlib.sha256(f"{lib_file.id}|{plate.source_sha256}|{y.object_name}|{color}".encode()).hexdigest()[:32]
    cache = lib.get_library_thumbnails_dir() / "farm_pieces"
    path = cache / f"{key}.png"
    if not path.exists():
        name = y.object_name
        try:
            mesh = await access_run(read_file, lib_file.file_path, lambda fh: object_mesh(fh, name))
        except Exception as exc:  # unreadable or odd 3MF: no picture, the page shows the name
            raise HTTPException(404, f"Cannot read the piece: {exc}"[:200]) from exc
        if mesh is None:
            raise HTTPException(404, "No mesh for this piece")
        png = await asyncio.to_thread(render_png, mesh[0], mesh[1], color)
        cache.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.part")
        tmp.write_bytes(png)
        os.replace(tmp, path)
    return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


class ProductPieceIn(BaseModel):
    yield_ids: list[int] = Field(min_length=1, max_length=5000)
    quantity: int = Field(ge=0, le=products.QTY_MAX)  # per product sold; 0: not part of it
    shape_id: int | None = None  # None: the piece's shape, or a new one named ``shape_name``
    shape_name: str | None = Field(default=None, max_length=200)
    component_code: str | None = Field(default=None, pattern=products.CODE_RE.pattern)  # existing, without shape
    color: str | None = Field(default=None, max_length=50)


@router.put("/products/{project_id}/pieces", dependencies=[products._update])
async def set_product_piece(project_id: int, data: ProductPieceIn, db: AsyncSession = Depends(get_db)) -> dict:
    """One piece of the product: shape everywhere, component in the product's colour, quantity per product."""
    product = await _product(db, project_id)
    yields = list((await db.execute(select(FarmPlateYield).where(FarmPlateYield.id.in_(data.yield_ids)))).scalars())
    if not yields:
        raise HTTPException(422, "Unknown piece")
    shapes = await products._shape_names(db)

    shape_id = data.shape_id
    if shape_id is not None and shape_id not in shapes:
        raise HTTPException(422, "Unknown shape")
    if shape_id is None:
        current = Counter(y.shape_id for y in yields if y.shape_id is not None).most_common(1)
        shape_id = current[0][0] if current else None
    if shape_id is None:
        if data.quantity == 0:
            return {"shape_id": None, "component_code": None, "quantity": 0, "plates_confirmed": 0}
        name = products._clean(data.shape_name or "", 200)
        if not name:
            raise HTTPException(422, "Name the piece")
        shape = await db.scalar(select(FarmShape).where(func.lower(FarmShape.name) == name.lower()))
        if shape is None:
            shape = FarmShape(name=name)
            db.add(shape)
            await db.flush()
        shape_id, shapes[shape.id] = shape.id, shape.name

    for y in yields:
        y.shape_id = shape_id
    await db.flush()
    plates = list((await db.execute(select(FarmPlate).where(FarmPlate.id.in_({y.plate_id for y in yields})))).scalars())
    confirmed = await link_by_geometry(db, plates)

    parts = await _parts(db, project_id)
    part, comp = next(((p, c) for p, c in parts if c.shape_id == shape_id), (None, None))
    if comp is None and data.quantity > 0:
        color = products._clean(data.color or "", 50) or None
        if data.component_code:
            comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == data.component_code))
            if comp is None or comp.retired_at is not None:
                raise HTTPException(404, "Component not found")
            if comp.shape_id not in (None, shape_id):
                raise HTTPException(409, f"{comp.code} is already another shape")
            color = color or comp.color
            # 409 when the shape already has its component in this colour: merge the two codes instead.
            await products._check_shape(db, shapes, shape_id, color, comp.code)
            comp.shape_id, comp.color = shape_id, color
            part = next((p for p, c in parts if c.id == comp.id), None)
        else:
            comp = await db.scalar(
                select(FarmComponent).where(
                    FarmComponent.shape_id == shape_id,
                    func.lower(func.coalesce(FarmComponent.color, "")) == (color or "").lower(),
                    FarmComponent.retired_at.is_(None),
                )
            )
            if comp is None:
                name = " – ".join(x for x in (shapes[shape_id], color) if x)
                comp = FarmComponent(code=await products._next_code(db), name=name, shape_id=shape_id, color=color)
                db.add(comp)
                await db.flush()
    if comp is not None:
        if data.quantity == 0 and part is not None:
            await db.delete(part)
        elif data.quantity > 0 and part is None:
            last = max((p.sort_order for p, _ in parts), default=-1)
            db.add(
                FarmProductPart(
                    project_id=project_id, component_id=comp.id, quantity=data.quantity, sort_order=last + 1
                )
            )
        elif data.quantity > 0:
            part.quantity = data.quantity
    product.updated_at = products._now()
    await db.commit()
    return {
        "shape_id": shape_id,
        "component_code": comp.code if comp else None,
        "quantity": data.quantity,
        "plates_confirmed": confirmed,
    }
