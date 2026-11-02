"""Farm tables (``farm_`` prefix, created by ``Base.metadata.create_all``)."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

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
