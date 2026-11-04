"""Plate tables. Yields name shapes (``farm_shapes``); a confirmed plate serves every colour of a shape."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base
from backend.app.farm.columns import add_columns
from backend.app.farm.products.models import FarmShape


class FarmPrinterModelMap(Base):
    """3MF ``printer_model`` -> ``Printer.model`` in Printbuddy, set once by hand.

    Bambu models resolve automatically; rows are for custom profiles (Klipper)
    and win over the automatic match.
    """

    __tablename__ = "farm_printer_model_map"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_model: Mapped[str] = mapped_column(String(100), unique=True)  # stored casefolded
    target_model: Mapped[str] = mapped_column(String(50))


class FarmPlate(Base):
    """One plate of a project file: what it prints and on which printer model.

    Proposed from the 3MF, then confirmed by the user. ``source_sha256`` is the file
    version the proposal was read from; a new version re-proposes changed yields.
    """

    __tablename__ = "farm_plates"
    __table_args__ = (UniqueConstraint("library_file_id", "plate_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    library_file_id: Mapped[int] = mapped_column(ForeignKey("library_files.id", ondelete="CASCADE"), index=True)
    plate_index: Mapped[int] = mapped_column(Integer)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    printer_model: Mapped[str | None] = mapped_column(String(100), nullable=True)  # from the 3MF
    materials: Mapped[list | None] = mapped_column(JSON, nullable=True)  # filament types used, e.g. ["PETG"]
    nozzle_diameter: Mapped[float | None] = mapped_column(Float, nullable=True)
    print_time_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filament_grams: Mapped[float | None] = mapped_column(Float, nullable=True)
    sliced_file_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parse_version: Mapped[int | None] = mapped_column(Integer, nullable=True)  # service.PARSE_VERSION; NULL = 1
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed | confirmed
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    yields: Mapped[list["FarmPlateYield"]] = relationship(
        back_populates="plate", cascade="all, delete-orphan", lazy="selectin", order_by="FarmPlateYield.id"
    )


class FarmPlateYield(Base):
    """Pieces one plate produces: 3MF object name x quantity, linked to a shape."""

    __tablename__ = "farm_plate_yields"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate_id: Mapped[int] = mapped_column(ForeignKey("farm_plates.id", ondelete="CASCADE"), index=True)
    object_name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer)
    # Shape, set when confirming; NULL until linked. Several object names can share one shape.
    shape_id: Mapped[int | None] = mapped_column(ForeignKey(FarmShape.id), nullable=True, index=True)
    # Before shapes, yields named a component code (C-…); still honoured for those plates.
    component_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # Geometry of the object (plates.geometry): NULL = not measured yet, [] = no mesh to measure.
    fingerprint: Mapped[list | None] = mapped_column(JSON, nullable=True)

    plate: Mapped[FarmPlate] = relationship(back_populates="yields")


add_columns("farm_plates", {"parse_version": "INTEGER"})
add_columns("farm_plate_yields", {"shape_id": "INTEGER", "fingerprint": "JSON"})
