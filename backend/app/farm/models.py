"""Farm tables (``farm_`` prefix, created by ``Base.metadata.create_all``)."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.core.database import Base


class FarmWatchedFolder(Base):
    """An external library folder kept in sync automatically (refresh, relink)."""

    __tablename__ = "farm_watched_folders"

    folder_id: Mapped[int] = mapped_column(ForeignKey("library_folders.id", ondelete="CASCADE"), primary_key=True)
    scan_interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class FarmLibraryFile(Base):
    """Farm state of one file on a watched folder.

    ``library_file_id`` is NULL for files never imported (corrupt). The row keeps
    the same ``library_file_id`` across edits, moves and renames.
    """

    __tablename__ = "farm_library_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    watch_folder_id: Mapped[int] = mapped_column(
        ForeignKey("farm_watched_folders.folder_id", ondelete="CASCADE"), index=True
    )
    library_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_files.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    path: Mapped[str] = mapped_column(String(1000), index=True)
    mtime: Mapped[float] = mapped_column(Float)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok | corrupt | missing
    status_detail: Mapped[str | None] = mapped_column(String(255), nullable=True)
    application: Mapped[str | None] = mapped_column(String(100), nullable=True)
    slicer_detected: Mapped[str | None] = mapped_column(String(20), nullable=True)
    slicer_override: Mapped[str | None] = mapped_column(String(20), nullable=True)
    printer_model_raw: Mapped[str | None] = mapped_column(String(100), nullable=True)
    printer_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    @property
    def slicer(self) -> str | None:
        return self.slicer_override or self.slicer_detected


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
    status: Mapped[str] = mapped_column(String(16), default="proposed")  # proposed | confirmed
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    yields: Mapped[list["FarmPlateYield"]] = relationship(
        back_populates="plate", cascade="all, delete-orphan", lazy="selectin", order_by="FarmPlateYield.id"
    )


class FarmPlateYield(Base):
    """Pieces one plate produces: 3MF object name x quantity, linked to a component code."""

    __tablename__ = "farm_plate_yields"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate_id: Mapped[int] = mapped_column(ForeignKey("farm_plates.id", ondelete="CASCADE"), index=True)
    object_name: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[int] = mapped_column(Integer)
    # Seller Hub component code (C-…), set when confirming; NULL until linked.
    component_code: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    plate: Mapped[FarmPlate] = relationship(back_populates="yields")
