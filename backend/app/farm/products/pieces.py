"""Farm API: the pieces of a product, mapped from the product page in one step.

The distinct pieces (recognised by geometry, :mod:`backend.app.farm.plates.geometry`) found in
the 3MF files of the product's linked folders, each with a picture of the piece alone. Saving
"how many per product" links the piece to a shape everywhere, gets the component of that shape
in the product's colour (an existing one without a shape can take it, keeping its code) and
sets the product's part.

A product printed and sold as a set (``kind == "set"``) has one component, the set: "how many
per set" goes into its recipe instead, and plates yield complete sets.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
from collections import Counter
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont
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
from backend.app.farm.products.folders import excluded_by, project_folders
from backend.app.farm.products.models import FarmComponent, FarmProduct, FarmProductPart, FarmShape
from backend.app.models.library import LibraryFile
from backend.app.models.project import Project

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
                .order_by(FarmProductPart.sort_order)
            )
        ).all()
    )


async def _used_elsewhere(db: AsyncSession, component_id: int, project_id: int) -> bool:
    return bool(
        await db.scalar(
            select(func.count())
            .select_from(FarmProductPart)
            .where(FarmProductPart.component_id == component_id, FarmProductPart.project_id != project_id)
        )
    )


async def _make_set(db: AsyncSession, product: FarmProduct, project: Project) -> FarmComponent | None:
    """Turn the product's parts into one set component; None when it has no parts yet.

    The first part only this product uses, once per unit, keeps its code: Seller Hub's stock of it
    already counts sets (a whole product, or one of a left/right pair). Otherwise the set gets a
    new code, since stock of a piece used several times per unit would not be sets. The shapes of
    every part go into the recipe with their quantities; the other parts leave the product, and
    components no other product uses are retired.
    """
    parts = await _parts(db, product.project_id)
    if not parts:
        return None
    if keep := next((c for p, c in parts if c.recipe is not None), None):
        return keep  # already a set
    recipe: dict[int, int] = {}
    for part, comp in parts:
        for shape_id in products.component_shapes(comp):
            recipe[shape_id] = recipe.get(shape_id, 0) + part.quantity
    own = [(p, c) for p, c in parts if p.quantity == 1 and not await _used_elsewhere(db, c.id, product.project_id)]
    if own:
        keep_part, keep = own[0]
    else:
        keep = FarmComponent(code=await products._next_code(db), name=project.name[:200], color=parts[0][1].color)
        db.add(keep)
        await db.flush()
        keep_part = FarmProductPart(project_id=product.project_id, component_id=keep.id, sort_order=0)
        db.add(keep_part)
    keep.recipe = [{"shape_id": s, "quantity": q} for s, q in recipe.items()]
    keep.shape_id = None
    keep_part.quantity = 1
    for part, comp in parts:
        if comp.id == keep.id:
            continue
        await db.delete(part)
        if not await _used_elsewhere(db, comp.id, product.project_id):
            comp.retired_at = comp.retired_at or products._now()
    return keep


class KindIn(BaseModel):
    kind: Literal["set", "kit"]


@router.put("/products/{project_id}/kind", dependencies=[products._update])
async def set_kind(project_id: int, data: KindIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Printed and sold as a set (one component, pieces per set) or as a kit (parts printed apart)."""
    product = await _product(db, project_id)
    if data.kind == "set":
        await _make_set(db, product, await db.get(Project, project_id))
    elif any(c.recipe and len(c.recipe) > 1 for _, c in await _parts(db, project_id)):
        raise HTTPException(409, "This set has several pieces: make each one a part before calling it a kit")
    else:
        for part, comp in await _parts(db, project_id):
            if comp.recipe:  # a set of one piece: back to that shape x its quantity
                shape_id, quantity = comp.recipe[0]["shape_id"], comp.recipe[0]["quantity"]
                await products._check_shape(db, await products._shape_names(db), shape_id, comp.color, comp.code)
                comp.shape_id, comp.recipe, part.quantity = shape_id, None, quantity
            elif comp.recipe == []:
                comp.recipe = None
    product.kind = data.kind
    product.updated_at = products._now()
    await db.commit()
    return {"project_id": project_id, "kind": product.kind}


@router.get("/products/{project_id}/pieces", dependencies=[products._read])
async def product_pieces(project_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """Distinct pieces in the 3MF projects of the product's folders, with their part in the product."""
    product = await _product(db, project_id)
    folders = await project_folders(db, project_id)
    files = dict(
        (
            await db.execute(
                select(LibraryFile.id, LibraryFile.filename)
                .where(LibraryFile.deleted_at.is_(None), LibraryFile.folder_id.in_(folders))
                .where(LibraryFile.filename.ilike("%.3mf"), LibraryFile.filename.not_ilike("%.gcode.3mf"))
                .where(LibraryFile.id.notin_(await excluded_by(db, project_id)))
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
    set_comp = next((c for _, c in parts if c.recipe is not None), None) if product.kind == "set" else None
    by_shape = {c.shape_id: (part, c) for part, c in parts if c.shape_id}
    shapes = await products._shape_names(db)
    colors = (await plan.load_config(db))["colors"]

    def comp_dict(comp: FarmComponent | None) -> dict | None:
        if comp is None:
            return None
        hex_ = colors.get((comp.color or "").lower())
        return {"code": comp.code, "name": comp.name, "color": comp.color, "color_hex": hex_}

    per_set = {r["shape_id"]: r["quantity"] for r in (set_comp.recipe or [])} if set_comp else {}
    resolver = await PrinterModelResolver.load(db)
    product_colors = Counter(c.color for _, c in parts if c.color)

    pieces = []
    for members in group([r[0].fingerprint for r in rows]):
        items = [rows[i] for i in members]
        names = Counter(y.object_name for y, _, _ in items)
        shape_counts = Counter(y.shape_id for y, _, _ in items if y.shape_id is not None)
        shape_id = shape_counts.most_common(1)[0][0] if shape_counts else None
        part, comp = by_shape.get(shape_id, (None, None))
        if product.kind == "set":
            part, comp = None, set_comp if shape_id in per_set else None
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
                "quantity": per_set.get(shape_id) if product.kind == "set" else part.quantity if part else None,
                "component": comp_dict(comp),
            }
        )
    open_names = sorted({n for p in pieces if p["shape_id"] is None for n in p["names"]})
    proposals = await suggest_shapes(db, open_names)
    for p in pieces:
        if p["shape_id"] is None:
            p["suggested_shape_id"] = next((proposals[n] for n in p["names"] if n in proposals), None)
    pieces.sort(key=lambda p: (p["quantity"] is not None, -p["plates"]))  # still to decide first
    return {
        "kind": product.kind,
        "set": comp_dict(set_comp),
        "default_color": product_colors.most_common(1)[0][0] if product_colors else None,
        # Parts imported without a shape (whole products from Seller Hub): a piece can take one of them.
        "unshaped_parts": [
            {"code": c.code, "name": c.name, "quantity": part.quantity}
            for part, c in parts
            if c.shape_id is None and c.recipe is None and c.retired_at is None and product.kind != "set"
        ],
        "pieces": pieces,
    }


async def _piece_png(db: AsyncSession, y: FarmPlateYield, color: str | None) -> bytes:
    """PNG of one object alone, in ``color`` (clean hex); cached until the 3MF changes. 404 when unreadable."""
    from backend.app.api.routes import library as lib

    plate = await db.get(FarmPlate, y.plate_id)
    lib_file = await db.scalar(LibraryFile.active().where(LibraryFile.id == plate.library_file_id)) if plate else None
    if lib_file is None:
        raise HTTPException(404, "Piece not found")
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
    return path.read_bytes()


def _hex(color: str | None) -> str | None:
    try:
        return plan.clean_hex(color) if color else None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _png(data: bytes) -> Response:
    return Response(data, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/pieces/{yield_id}/image")
async def piece_image(
    yield_id: int,
    color: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: None = RequireCameraStreamTokenIfAuthEnabled,
):
    """PNG of one object alone, in ``color`` (hex); kept until the 3MF changes."""
    y = await db.get(FarmPlateYield, yield_id)
    if y is None:
        raise HTTPException(404, "Piece not found")
    return _png(await _piece_png(db, y, _hex(color)))


async def _shape_yield(db: AsyncSession, shape_id: int) -> FarmPlateYield | None:
    """A measured object of the shape to draw: on a confirmed plate first, in a 3MF still used."""
    from backend.app.farm.products.folders import excluded_files

    return await db.scalar(
        select(FarmPlateYield)
        .join(FarmPlate, FarmPlate.id == FarmPlateYield.plate_id)
        .join(LibraryFile, LibraryFile.id == FarmPlate.library_file_id)
        .where(
            FarmPlateYield.shape_id == shape_id,
            FarmPlateYield.fingerprint.isnot(None),
            LibraryFile.deleted_at.is_(None),
            FarmPlate.library_file_id.notin_(await excluded_files(db)),
        )
        .order_by(FarmPlate.status != "confirmed", FarmPlateYield.id)
        .limit(1)
    )


def _side_by_side(pngs: list[tuple[bytes, int]]) -> bytes:
    """The pieces of a set in one picture, each once, "×n" under those used n times."""
    images = [Image.open(io.BytesIO(data)).convert("RGBA") for data, _ in pngs]
    size = max(i.height for i in images)
    caption = 28 if any(n > 1 for _, n in pngs) else 0
    out = Image.new("RGBA", (sum(i.width for i in images), size + caption), (0, 0, 0, 0))
    draw = ImageDraw.Draw(out)
    x = 0
    for image, (_, n) in zip(images, pngs, strict=True):
        out.paste(image, (x, 0), image)
        if n > 1:
            draw.text(
                (x + image.width // 2, size + caption // 2),
                f"×{n}",
                fill=(90, 90, 90, 255),
                anchor="mm",
                font=ImageFont.load_default(size=22),
            )
        x += image.width
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


@router.get("/components/{code}/image")
async def component_image(
    code: str, db: AsyncSession = Depends(get_db), _: None = RequireCameraStreamTokenIfAuthEnabled
):
    """The component as printed, in its colour: its shape, or a set's pieces side by side. 404 without pieces."""
    comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == code))
    if comp is None:
        raise HTTPException(404, "Component not found")
    color = _hex((await plan.load_config(db))["colors"].get((comp.color or "").lower()))
    entries = comp.recipe or ([{"shape_id": comp.shape_id, "quantity": 1}] if comp.shape_id else [])
    pngs = []
    for entry in entries:
        y = await _shape_yield(db, entry["shape_id"])
        if y is not None:
            try:
                pngs.append((await _piece_png(db, y, color), entry["quantity"]))
            except HTTPException:
                continue  # an unreadable 3MF: the set is drawn without that piece
    if not pngs:
        raise HTTPException(404, "No piece to draw")
    if len(pngs) == 1 and pngs[0][1] == 1:
        return _png(pngs[0][0])
    return _png(await asyncio.to_thread(_side_by_side, pngs))


class PartQuantityIn(BaseModel):
    quantity: int = Field(ge=0, le=products.QTY_MAX)  # 0: out of the product


@router.put("/products/{project_id}/parts/{code}", dependencies=[products._update])
async def set_part(project_id: int, code: str, data: PartQuantityIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Add a component to a kit, change how many go in one unit, or take it out (0).

    A component taken out that no other product uses is retired: Seller Hub stops counting it.
    """
    product = await _product(db, project_id)
    comp = await db.scalar(select(FarmComponent).where(FarmComponent.code == code))
    if comp is None:
        raise HTTPException(404, "Component not found")
    parts = await _parts(db, project_id)
    part = next((p for p, c in parts if c.id == comp.id), None)
    if data.quantity == 0:
        if part is not None:
            await db.delete(part)
            if not await _used_elsewhere(db, comp.id, project_id):
                comp.retired_at = comp.retired_at or products._now()
    elif part is not None:
        if comp.recipe is not None and data.quantity != 1:
            raise HTTPException(422, "A set is one per unit: change how many pieces go in the set")
        part.quantity = data.quantity
    else:
        if product.kind == "set" and parts:
            raise HTTPException(409, "A set has one component: add the piece to its set instead")
        if comp.retired_at is not None:
            raise HTTPException(409, f"{comp.code} is retired")
        last = max((p.sort_order for p, _ in parts), default=-1)
        db.add(
            FarmProductPart(project_id=project_id, component_id=comp.id, quantity=data.quantity, sort_order=last + 1)
        )
    product.updated_at = products._now()
    await db.commit()
    return {"project_id": project_id, "component_code": comp.code, "quantity": data.quantity}


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

    if product.kind == "set":
        project = await db.get(Project, project_id)
        comp = await _make_set(db, product, project)
        if comp is None:  # first piece of the set: its component, named as the product
            color = products._clean(data.color or "", 50) or None
            comp = FarmComponent(code=await products._next_code(db), name=project.name[:200], color=color, recipe=[])
            db.add(comp)
            await db.flush()
            db.add(FarmProductPart(project_id=project_id, component_id=comp.id, quantity=1, sort_order=0))
        recipe = [r for r in comp.recipe or [] if r["shape_id"] != shape_id]
        if data.quantity:
            recipe.append({"shape_id": shape_id, "quantity": data.quantity})
        comp.recipe = recipe
        product.updated_at = products._now()
        await db.commit()
        return {
            "shape_id": shape_id,
            "component_code": comp.code,
            "quantity": data.quantity,
            "plates_confirmed": confirmed,
        }

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
