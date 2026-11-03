"""Product tables (``farm_`` prefix, created by ``Base.metadata.create_all``)."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, delete, event, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.farm.columns import add_columns
from backend.app.models.project import Project


class FarmShape(Base):
    """A geometric piece ("Tartufo sopra"): plates yield shapes, the same plate for every colour."""

    __tablename__ = "farm_shapes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class FarmComponent(Base):
    """A printed part with a stable code (``C-0042``): the key Seller Hub stock uses.

    Normally a shape in a colour ("Tartufo sopra – Nero"). A set printed together is one
    component with a ``recipe`` instead: the shapes of one set and how many of each
    ("Tartufo" = 1 sopra + 1 sotto), so plates yield complete sets. Codes are never reused, so
    components are never deleted: one that is no longer made is retired.
    """

    __tablename__ = "farm_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    shape_id: Mapped[int | None] = mapped_column(ForeignKey("farm_shapes.id"), nullable=True, index=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    recipe: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"shape_id", "quantity"}]; then no shape_id
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


add_columns(
    "farm_components", {"shape_id": "INTEGER", "color": "VARCHAR(50)", "retired_at": "DATETIME", "recipe": "JSON"}
)


def component_shapes(c: FarmComponent) -> set[int]:
    """Shapes a component is printed as: its set's, or its own."""
    if c.recipe:
        return {r["shape_id"] for r in c.recipe}
    return {c.shape_id} if c.shape_id else set()


def component_yield(c: FarmComponent, yields) -> int:
    """Units of the component a plate's yields give: complete sets for a set, else its pieces.

    Before shapes, plates named components by code (``component_code``); those still count.
    """
    if c.recipe:
        have: dict[int, int] = {}
        for y in yields:
            if y.shape_id is not None:
                have[y.shape_id] = have.get(y.shape_id, 0) + y.quantity
        return min(have.get(r["shape_id"], 0) // r["quantity"] for r in c.recipe)
    return sum(
        y.quantity
        for y in yields
        if (c.shape_id and y.shape_id == c.shape_id) or (not c.shape_id and y.component_code == c.code)
    )


class FarmProduct(Base):
    """Marks a project as a product: what is sold, reprinted forever (no completion, due date or budget)."""

    __tablename__ = "farm_products"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    skus: Mapped[list] = mapped_column(JSON, default=list)
    asins: Mapped[list] = mapped_column(JSON, default=list)
    # Other listings of the same object: [{"skus", "asins", "color", "pieces"}]. Each sells ``pieces`` units
    # of the product, in ``color`` (parts with a shape take the component of that shape in that colour).
    variants: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # "set": printed and sold together, one component (a set of pieces); "kit": parts printed apart; None: not said.
    kind: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Not made any more for now: kept with its data, left out of what to print.
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Pieces of its 3MF that are not part of it ("0 per product"), by geometry: [fingerprint, ...].
    not_part: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


add_columns("farm_products", {"variants": "JSON", "kind": "VARCHAR(10)", "disabled_at": "DATETIME", "not_part": "JSON"})


def not_part(product: FarmProduct, fp: list | None) -> bool:
    """The piece with this geometry was marked as not part of the product."""
    from backend.app.farm.plates.geometry import same_piece

    return bool(fp) and any(same_piece(fp, f) for f in product.not_part or [])


class FarmProductPart(Base):
    """One line of a product's bill of materials: component × pieces per unit sold."""

    __tablename__ = "farm_product_parts"
    __table_args__ = (UniqueConstraint("project_id", "component_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("farm_products.project_id", ondelete="CASCADE"), index=True)
    component_id: Mapped[int] = mapped_column(ForeignKey("farm_components.id"), index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)


@event.listens_for(Project, "after_delete")
def _drop_product_rows(mapper, connection, target) -> None:
    # SQLite runs without foreign_keys, and may hand the freed id to the next project.
    connection.execute(delete(FarmProductPart).where(FarmProductPart.project_id == target.id))
    connection.execute(delete(FarmProduct).where(FarmProduct.project_id == target.id))
