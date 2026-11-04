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

    A print finished outside the queue (``farm.jobs.outside``) is a job too, with its ``source``:
    ``outside`` when its components are known, ``to_assign`` until the user says them, or
    ``not_production``.
    """

    __tablename__ = "farm_print_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    component_code: Mapped[str] = mapped_column(String(12), index=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)  # the component's
    # The filament the printer really fed (``service.record_used_colors``): the batch's colour when known
    used_color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plate_ids: Mapped[list] = mapped_column(JSON)  # alternatives, preferred first
    expected_pieces: Mapped[int] = mapped_column(Integer)  # the first alternative's yield, until a plate is chosen
    # waiting | queued | cancelled; outside | to_assign | not_production
    status: Mapped[str] = mapped_column(String(16), default="waiting", index=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)  # why it is still waiting
    queue_item_id: Mapped[int | None] = mapped_column(ForeignKey("print_queue.id", ondelete="SET NULL"), nullable=True)
    printer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plate_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)  # yield of the chosen plate
    # Objects the printer excluded (``service.record_excluded_objects``): {"objects": {printer id: name or None},
    # "matched": all on the plate, "end": read as the print ended}; NULL = never read while it printed
    excluded: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # ``pieces`` less the excluded copies (complete sets for a set), as last counted with every one matched
    printed_pieces: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # What the print dialog sets on the upstream queue item: ams_mapping, scheduled_time (ISO, UTC), manual_start...
    queue_options: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # A print outside the queue: "archive:<id>" or "klipper:<printer id>:<Moonraker job id>"
    source: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    outside_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # the print's file name
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # its end, UTC
    listed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # when it became a batch, UTC
    proposal: Mapped[list | None] = mapped_column(JSON, nullable=True)  # to_assign: components its name suggests
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


add_columns(
    "farm_print_jobs",
    {
        "queue_options": "JSON",
        "used_color": "VARCHAR(50)",
        "source": "VARCHAR(80)",
        "outside_name": "VARCHAR(255)",
        "finished_at": "DATETIME",
        "listed_at": "DATETIME",
        "proposal": "JSON",
        "excluded": "JSON",
        "printed_pieces": "INTEGER",
    },
)
