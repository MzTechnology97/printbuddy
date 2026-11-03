"""Farm print jobs: one print of a component, waiting for a free printer that fits one of its plates."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.core.database import Base
from backend.app.farm.columns import add_columns


class FarmPrintJob(Base):
    """``waiting`` until the dispatcher finds a printer, then ``queued`` with an upstream queue item.

    What happens next (printing, done, failed) is the queue item's status; the job is
    ``cancelled`` only by the user.
    """

    __tablename__ = "farm_print_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    component_code: Mapped[str] = mapped_column(String(12), index=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plate_ids: Mapped[list] = mapped_column(JSON)  # alternatives, preferred first
    expected_pieces: Mapped[int] = mapped_column(Integer)  # the first alternative's yield, until a plate is chosen
    status: Mapped[str] = mapped_column(String(16), default="waiting", index=True)  # waiting | queued | cancelled
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)  # why it is still waiting
    queue_item_id: Mapped[int | None] = mapped_column(ForeignKey("print_queue.id", ondelete="SET NULL"), nullable=True)
    printer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)  # yield of the chosen plate
    # What the print dialog sets on the upstream queue item: ams_mapping, scheduled_time (ISO, UTC), manual_start...
    queue_options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


add_columns("farm_print_jobs", {"queue_options": "JSON"})
