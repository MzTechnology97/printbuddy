"""Product tables (``farm_`` prefix, created by ``Base.metadata.create_all``)."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint, delete, event, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.models.project import Project


class FarmComponent(Base):
    """A printed part with a stable code (``C-0042``): the key Seller Hub stock and plate yields use.

    Codes are never reused, so components are never deleted (only renamed).
    """

    __tablename__ = "farm_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class FarmProduct(Base):
    """Marks a project as a product: what is sold, reprinted forever (no completion, due date or budget)."""

    __tablename__ = "farm_products"

    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    skus: Mapped[list] = mapped_column(JSON, default=list)
    asins: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


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
