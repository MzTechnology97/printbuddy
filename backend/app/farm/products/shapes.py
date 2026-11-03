"""A product's own shapes, and how the objects of its 3MFs match them (see ``isolation``).

An object is a piece of the product when its geometry is one of a shape's ``fingerprints``
(``object_names`` for objects without a measured mesh). Nothing is written on the objects, so
the same 3MF serves several products, each reading it with its own shapes, and a re-saved 3MF
keeps its pieces. A plate is ready for a product when every object on it is one of its pieces
or set aside as not part of it.

Components from before the move to one product each (``project_id`` None) still read the old
links on the objects (``farm_plate_yields.shape_id``) and confirmed plates.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.farm.plates.geometry import same_piece
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.products.folders import excluded_files
from backend.app.farm.products.models import (
    FarmComponent,
    FarmProduct,
    FarmShape,
    component_shapes,
    component_yield,
    not_part,
)


def measured(fp: list | None) -> bool:
    return bool(fp) and bool(fp[0])


class ProductShapes:
    """The shapes of one product and the pieces of its 3MFs."""

    def __init__(self, product: FarmProduct | None, shapes: list[FarmShape], codes: set[str] | None = None):
        self.product = product
        self.shapes = shapes
        self.codes = codes or set()  # its components' codes: plates from before shapes name them

    def of(self, y: FarmPlateYield) -> int | None:
        """The product's shape of this object, by geometry (by name without a measured mesh).

        shortcut: a shape with no geometry yet (named by hand, or taken over with its component
        before the product had 3MFs) keeps the old link on the object; it ends at the first Salva.
        """
        fp = y.fingerprint
        if measured(fp):
            found = next((s.id for s in self.shapes if any(same_piece(fp, f) for f in s.fingerprints or [])), None)
        else:
            found = next((s.id for s in self.shapes if y.object_name in (s.object_names or [])), None)
        if found is None and y.shape_id is not None:
            found = next(
                (s.id for s in self.shapes if s.id == y.shape_id and not s.fingerprints and not s.object_names), None
            )
        return found

    def aside(self, y: FarmPlateYield) -> bool:
        return self.product is not None and not_part(self.product, y.fingerprint)

    def ready(self, plate: FarmPlate) -> bool:
        """Every object on the plate is a piece of the product or set aside: it can print for it."""
        return bool(plate.yields) and all(
            self.of(y) is not None or self.aside(y) or (y.component_code in self.codes and plate.status == "confirmed")
            for y in plate.yields
        )  # objects named by a component code (before shapes) count once their plate was confirmed


async def product_shapes(db: AsyncSession, project_ids: set[int] | None = None) -> dict[int, ProductShapes]:
    """``{project_id: ProductShapes}`` for these products (every product when None)."""
    query = select(FarmProduct)
    shapes_query = select(FarmShape).where(FarmShape.project_id.isnot(None)).order_by(FarmShape.id)
    if project_ids is not None:
        query = query.where(FarmProduct.project_id.in_(project_ids))
        shapes_query = shapes_query.where(FarmShape.project_id.in_(project_ids))
    out = {p.project_id: ProductShapes(p, []) for p in (await db.execute(query)).scalars()}
    for shape in (await db.execute(shapes_query)).scalars():
        out.setdefault(shape.project_id, ProductShapes(None, [])).shapes.append(shape)
    codes = select(FarmComponent.project_id, FarmComponent.code).where(FarmComponent.project_id.in_(out))
    for project_id, code in (await db.execute(codes)).all():
        out[project_id].codes.add(code)
    return out


def plate_yield(c: FarmComponent, plate: FarmPlate, own: dict[int, ProductShapes]) -> int:
    """Units of the component the plate gives; 0 when the plate is not ready for its product."""
    if c.project_id is None:  # before the move to one product each: the old links
        return component_yield(c, plate.yields) if plate.status == "confirmed" else 0
    shapes = own.get(c.project_id)
    if shapes is None or not shapes.ready(plate):
        return 0
    return component_yield(c, plate.yields, shapes.of)


async def candidate_plates(
    db: AsyncSession, components: list[FarmComponent], files: dict[int, set[int]]
) -> list[FarmPlate]:
    """Plates that may yield these components: their products' 3MFs (``files``, see
    ``folders.component_files``), plus the old shape links of components without a product."""
    own = {f for c in components if c.project_id is not None for f in files.get(c.id, ())}
    legacy = [c for c in components if c.project_id is None]
    shapes = {s for c in legacy for s in component_shapes(c)}
    codes = {c.code for c in legacy if not component_shapes(c)}
    conditions = []
    if own:
        conditions.append(FarmPlate.library_file_id.in_(own))
    if shapes or codes:
        conditions.append(
            (FarmPlate.status == "confirmed")
            & FarmPlate.yields.any(FarmPlateYield.shape_id.in_(shapes) | FarmPlateYield.component_code.in_(codes))
        )
    if not conditions:
        return []
    query = (
        select(FarmPlate)
        .where(or_(*conditions), FarmPlate.library_file_id.notin_(await excluded_files(db)))
        .order_by(FarmPlate.library_file_id, FarmPlate.plate_index)
    )
    return list((await db.execute(query)).scalars())
