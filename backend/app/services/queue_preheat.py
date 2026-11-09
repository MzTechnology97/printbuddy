"""Preheat the chamber before a queued print of a warm-chamber material (port of upstream #1468).

Off by default (setting ``preheat_enabled``). When on, a queued item on an enclosed Bambu printer whose plate prints
a material listed in ``preheat_targets`` (``ABS=45, ASA=45``: material = chamber °C, the highest of the plate's
filaments counts) is not started at once: the scheduler heats the bed, and the chamber heater on models that have
one, and starts the print when the chamber sensor reaches the target, or when ``preheat_max_wait_minutes`` is over
(as upstream: the print starts anyway, a printer is never held). A P1S has no chamber sensor: it holds the whole
wait. Klipper printers and open printers (A1) are never preheated.

It runs across scheduler ticks instead of waiting inside one, so the other printers keep being dispatched. It
starts only where the scheduler would start the print now: printer connected and idle, plate confirmed clear, no
other job on it. While it heats, the printer takes no other job (``blocks``, ``printers``) and the item shows why
it waits. If the item stops reaching the start (cancelled, deleted, printer offline or busy, plate check reset,
scheduled later) the wait ends and the heaters it turned on go off, unless the printer is printing.
"""

import logging
import math
import re
import time
from dataclasses import dataclass

from backend.app.services.printer_manager import CHAMBER_TEMP_SUPPORTED_MODELS, printer_manager
from backend.app.services.printer_providers.factory import normalize_provider

logger = logging.getLogger(__name__)

PREHEAT_MARK = "Preheating"  # start of the waiting reason this module writes (and clears)
DEFAULT_TARGETS = "ABS=45, ASA=45"
DEFAULT_MAX_WAIT_MINUTES = 15
BED_C = 90  # the bed is the heat source of the passive chambers; the print's own G-code sets its bed after
TOLERANCE_C = 2
_ACTIVE_PRINT_STATES = frozenset({"PREPARE", "SLICING", "RUNNING", "PAUSE"})  # as print_scheduler's

# Active chamber heater (M141), as upstream's list after its #3335 fix (X1E included). X1C and P2S only read the
# chamber: the bed warms it.
CHAMBER_HEATER_MODELS = frozenset(
    ["X1E", "X2D", "H2C", "H2D", "H2DPRO", "H2S", "C13", "N6", "O1C", "O1C2", "O1D", "O1E", "O2D", "O1S"]
)
# Cooling/heating airduct flap: in cooling mode it vents the chamber, so it goes to heating while preheating.
AIRDUCT_MODELS = frozenset(
    ["P2S", "X2D", "H2C", "H2D", "H2DPRO", "H2S", "N7", "N6", "O1C", "O1C2", "O1D", "O1E", "O2D", "O1S"]
)
ENCLOSED_NO_SENSOR = frozenset(["P1S", "C12"])


def _model(model: str | None) -> str:
    return (model or "").replace(" ", "").upper()  # "H2D Pro" -> "H2DPRO"


def parse_targets(text: str | None) -> dict[str, int]:
    """``"ABS=45, asa: 45"`` -> ``{"ABS": 45, "ASA": 45}``; anything else in the text is ignored."""
    return {m.upper(): int(c) for m, c in re.findall(r"([A-Za-z0-9+-]+)\s*[=:]\s*(\d+)", text or "")}


def chamber_target(types: list[str], targets: dict[str, int]) -> int:
    """Highest target of the plate's filaments; a variant without its own row takes its base (ABS-GF -> ABS)."""
    best = 0
    for t in types:
        words = (t or "").upper().split()  # "PLA Basic" -> PLA
        if words:
            best = max(best, targets.get(words[0], targets.get(words[0].split("-")[0], 0)))
    return best


@dataclass
class Preheat:
    item_id: int
    target: int
    deadline: float  # time.monotonic()
    has_sensor: bool
    chamber_on: bool  # M141 sent: turned off again if the print does not start
    airduct_before: str | None  # the flap mode it had, when switched to heating


class QueuePreheat:
    def __init__(self):
        self.active: dict[int, Preheat] = {}  # printer_id -> its preheat
        self._seen: set[tuple[int, int]] = set()  # (item, printer) that reached the start this tick
        self._airduct_back: dict[int, str] = {}  # printer -> flap mode to put back for its next unheated job

    def blocks(self, printer_id: int, item_id: int) -> bool:
        """The printer is preheating for another item: this one waits."""
        entry = self.active.get(printer_id)
        return entry is not None and entry.item_id != item_id

    def printer_of(self, item_id: int) -> int | None:
        return next((pid for pid, e in self.active.items() if e.item_id == item_id), None)

    def printers(self, except_item: int | None = None) -> set[int]:
        return {pid for pid, e in self.active.items() if e.item_id != except_item}

    async def ready(self, scheduler, db, item) -> bool:
        """True: start the item now. False: its printer is preheating for it (reason set on the item)."""
        pid = item.printer_id
        self._seen.add((item.id, pid))
        entry = self.active.get(pid)
        if entry is None:
            for other_pid in [p for p, e in self.active.items() if e.item_id == item.id]:
                self._stop(other_pid)  # an "any model" job that moved to another printer
            return await self._begin(scheduler, db, item)
        if entry.item_id != item.id:
            return False
        chamber = self._chamber(pid)
        if entry.has_sensor and chamber is not None and chamber >= entry.target - TOLERANCE_C:
            logger.info("Queue item %s: chamber at %.0f/%d °C, starting", item.id, chamber, entry.target)
        elif time.monotonic() >= entry.deadline:
            logger.info(
                "Queue item %s: preheat wait over (chamber %s/%d °C), starting anyway", item.id, chamber, entry.target
            )
        else:
            await self._show(db, item, entry, chamber)
            return False
        if (item.waiting_reason or "").startswith(PREHEAT_MARK):
            item.waiting_reason = None
        return True

    def finished(self, printer_id: int, *, sent: bool) -> None:
        """After the start: a print that went out keeps the heat; one that did not turns it off."""
        entry = self.active.pop(printer_id, None)
        if entry is None:
            return
        if sent:
            if entry.airduct_before:
                self._airduct_back[printer_id] = entry.airduct_before
        else:
            self._heaters_off(printer_id, entry)

    async def sweep(self, db, pending: list) -> None:
        """End of a tick: preheats whose item did not reach the start stop, and their reason goes."""
        by_id = {i.id: i for i in pending}
        dirty = False
        for pid, entry in list(self.active.items()):
            if (entry.item_id, pid) in self._seen:
                continue
            logger.info("Queue item %s: preheat on printer %s stopped (no longer starting there)", entry.item_id, pid)
            self._stop(pid)
            item = by_id.get(entry.item_id)
            if item is not None and (item.waiting_reason or "").startswith(PREHEAT_MARK):
                item.waiting_reason = None
                dirty = True
        self._seen.clear()
        if dirty:
            await db.commit()

    # ---------------------------------------------------------------- internals

    async def _begin(self, scheduler, db, item) -> bool:
        pid = item.printer_id
        target, printer = await self._target(scheduler, db, item)
        if not target:
            self._put_airduct_back(pid)
            return True
        model = _model(printer.model)
        has_sensor = model in CHAMBER_TEMP_SUPPORTED_MODELS
        chamber = self._chamber(pid) if has_sensor else None
        if chamber is not None and chamber >= target - TOLERANCE_C:
            return True  # still warm, e.g. from the print before
        client = printer_manager.get_client(pid)
        try:
            if client is None or not client.set_bed_temperature(BED_C):
                logger.warning("Queue item %s: preheat bed command not sent, starting without preheat", item.id)
                return True
            airduct_before = None
            state = printer_manager.get_status(pid)
            if model in AIRDUCT_MODELS and getattr(state, "airduct_mode", None) != 1:
                if client.set_airduct_mode("heating"):
                    airduct_before = "cooling"
            chamber_on = model in CHAMBER_HEATER_MODELS and bool(client.set_chamber_temperature(target))
        except Exception as e:  # best effort, as upstream: the print starts without preheat
            logger.warning("Queue item %s: preheat commands failed (%s), starting without preheat", item.id, e)
            return True
        minutes = await self._int_setting(scheduler, db, "preheat_max_wait_minutes", DEFAULT_MAX_WAIT_MINUTES)
        entry = Preheat(item.id, target, time.monotonic() + minutes * 60, has_sensor, chamber_on, airduct_before)
        self.active[pid] = entry
        self._airduct_back.pop(pid, None)
        logger.info(
            "Queue item %s: preheating %s for a %d °C chamber (bed %d °C, heater=%s, sensor=%s, up to %d min)",
            item.id,
            printer.name,
            target,
            BED_C,
            chamber_on,
            has_sensor,
            minutes,
        )
        await self._show(db, item, entry, chamber)
        return False

    async def _target(self, scheduler, db, item):
        """(chamber target, printer); target 0 = no preheat for this item on this printer."""
        if not await scheduler._get_bool_setting(db, "preheat_enabled", default=False):
            return 0, None
        printer = await scheduler._get_printer(db, item.printer_id)
        if printer is None or normalize_provider(getattr(printer, "provider", None)) != "bambu":
            return 0, printer
        model = _model(printer.model)
        if model not in CHAMBER_TEMP_SUPPORTED_MODELS and model not in ENCLOSED_NO_SENSOR:
            return 0, printer  # open frame (A1): nothing to keep warm
        targets = parse_targets(await scheduler._get_setting(db, "preheat_targets") or DEFAULT_TARGETS)
        filaments = await scheduler._get_filament_requirements(db, item) or []
        return chamber_target([f.get("type") or "" for f in filaments], targets), printer

    @staticmethod
    async def _int_setting(scheduler, db, key: str, default: int) -> int:
        try:
            return max(1, int(await scheduler._get_setting(db, key) or default))
        except ValueError:
            return default

    @staticmethod
    def _chamber(printer_id: int) -> float | None:
        temps = getattr(printer_manager.get_status(printer_id), "temperatures", None) or {}
        value = temps.get("chamber")
        return float(value) if isinstance(value, (int, float)) else None

    @staticmethod
    async def _show(db, item, entry: Preheat, chamber: float | None) -> None:
        minutes = max(1, math.ceil((entry.deadline - time.monotonic()) / 60))
        if entry.has_sensor:
            now = f"{chamber:.0f}" if chamber is not None else "?"
            reason = f"{PREHEAT_MARK} chamber: {now}/{entry.target} °C, up to {minutes} min"
        else:
            reason = f"{PREHEAT_MARK} chamber (no sensor): bed at {BED_C} °C for {minutes} min more"
        if item.waiting_reason != reason:
            item.waiting_reason = reason
            await db.commit()

    def _stop(self, printer_id: int) -> None:
        entry = self.active.pop(printer_id, None)
        if entry is not None:
            self._heaters_off(printer_id, entry)

    @staticmethod
    def _heaters_off(printer_id: int, entry: Preheat) -> None:
        """Bed (and chamber, flap) back as before, unless the printer is printing: its print owns them now."""
        if getattr(printer_manager.get_status(printer_id), "state", None) in _ACTIVE_PRINT_STATES:
            return
        client = printer_manager.get_client(printer_id)
        if client is None:
            return
        try:
            client.set_bed_temperature(0)
            if entry.chamber_on:
                client.set_chamber_temperature(0)
            if entry.airduct_before:
                client.set_airduct_mode(entry.airduct_before)
        except Exception as e:
            logger.warning("Printer %s: turning the preheat off failed: %s", printer_id, e)

    def _put_airduct_back(self, printer_id: int) -> None:
        """A job without preheat after one that switched the flap to heating gets the flap as it was."""
        mode = self._airduct_back.pop(printer_id, None)
        client = printer_manager.get_client(printer_id) if mode else None
        if client is not None:
            try:
                client.set_airduct_mode(mode)
            except Exception as e:
                logger.warning("Printer %s: airduct back to %s failed: %s", printer_id, mode, e)
