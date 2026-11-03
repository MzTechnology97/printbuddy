"""Spools set by hand (``farm_`` prefix, created by ``Base.metadata.create_all``)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, delete, event, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.models.printer import Printer


class FarmPrinterSpool(Base):
    """The spool on a Klipper printer that can't report it (no CFS: K1, Sovol...), as the user said.

    Used as that printer's loaded filament until changed or cleared.
    """

    __tablename__ = "farm_printer_spools"

    printer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    material: Mapped[str] = mapped_column(String(20))
    color_name: Mapped[str] = mapped_column(String(50))
    color: Mapped[str] = mapped_column(String(6))  # hex, from the colour table
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


@event.listens_for(Printer, "after_delete")
def _drop_spool(mapper, connection, target) -> None:
    # SQLite runs without foreign_keys, and may hand the freed id to the next printer.
    connection.execute(delete(FarmPrinterSpool).where(FarmPrinterSpool.printer_id == target.id))
