"""3MF printer model -> Printbuddy printer model (the queue's ``target_model``)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.farm.models import FarmPrinterModelMap
from backend.app.models.printer import Printer
from backend.app.utils.printer_models import PRINTER_MODEL_MAP, normalize_printer_model, normalize_printer_model_id

# SSDP codes Printbuddy may store in Printer.model that upstream's PRINTER_MODEL_ID_MAP lacks.
# shortcut: upstream PR candidate (add "N7": "P2S" there), drop this once merged.
_EXTRA_MODEL_CODES = {"N7": "P2S"}


def canonical_model(model: str | None) -> str | None:
    """Comparable short name for a 3MF model or a Printer.model ("Bambu Lab P2S", "P2S", "N7" -> "p2s")."""
    if not model or not model.strip():
        return None
    m = model.strip()
    m = _EXTRA_MODEL_CODES.get(m.upper(), m)
    m = normalize_printer_model(m) or m
    m = normalize_printer_model_id(m) or m
    return m.casefold()


@dataclass(slots=True)
class Resolution:
    target_model: str | None
    how: str  # manual | auto | unmapped


class PrinterModelResolver:
    """Loads the map and the farm's printers once, then resolves many plates."""

    def __init__(self, manual: dict[str, str], printer_models: list[str]) -> None:
        self.manual = manual
        self.by_canonical: dict[str, str] = {}
        for model in printer_models:
            key = canonical_model(model)
            if key:
                self.by_canonical.setdefault(key, model)

    @classmethod
    async def load(cls, db: AsyncSession) -> PrinterModelResolver:
        rows = (await db.execute(select(FarmPrinterModelMap))).scalars().all()
        models = (await db.execute(select(Printer.model).where(Printer.model.isnot(None)).distinct())).scalars().all()
        return cls({r.source_model: r.target_model for r in rows}, list(models))

    def resolve(self, source_model: str | None) -> Resolution:
        if not source_model:
            return Resolution(None, "unmapped")
        manual = self.manual.get(source_model.strip().casefold())
        if manual:
            return Resolution(manual, "manual")
        # Same model after normalisation: every Bambu, and Klipper printers named like the profile.
        target = self.by_canonical.get(canonical_model(source_model) or "")
        return Resolution(target, "auto") if target else Resolution(None, "unmapped")

    def known_names(self) -> list[str]:
        """Model names worth looking for in NAS paths, longest first."""
        names = {*PRINTER_MODEL_MAP.values(), *self.by_canonical.values(), *self.manual.values()}
        return sorted((n for n in names if n), key=len, reverse=True)


def models_in_path(path: str, names: list[str]) -> set[str]:
    """Printer models named in a path ("P1S-PLA-box.3mf", "K2 Pro/...").

    Longest names first, so "A1 Mini" doesn't also count as "A1".
    """
    if not names:
        return set()
    pattern = re.compile(
        r"(?<![A-Za-z0-9])(" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9])", re.IGNORECASE
    )
    return {canonical_model(m.group(1)) for m in pattern.finditer(path)} - {None}
