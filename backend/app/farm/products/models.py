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

    Normally a shape in a colour ("Tartufo sopra – Nero"). Codes are never reused, so
    components are never deleted: one that is no longer made is retired.
    """

    __tablename__ = "farm_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    shape_id: Mapped[int | None] = mapped_column(ForeignKey("farm_shapes.id"), nullable=True, index=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


add_columns("farm_components", {"shape_id": "INTEGER", "color": "VARCHAR(50)", "retired_at": "DATETIME"})


class FarmProduct(Base):
    """Marks a project as a product: what is sold, reprinted forever (no completion, due date or budget)."""

    __tablename__ = "farm_products"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    skus: Mapped[list] = mapped_column(JSON, default=list)
    asins: Mapped[list] = mapped_column(JSON, default=list)
    # Other listings of the same object: [{"skus", "asins", "color", "pieces"}]. Each sells ``pieces`` units
    # of the product, in ``color`` (parts with a shape take the component of that shape in that colour).
    variants: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


add_columns("farm_products", {"variants": "JSON"})


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
