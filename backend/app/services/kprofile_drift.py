"""Put a slot's K-profile back when a restarted printer has lost it (upstream #3219).

A power-cycled Bambu printer can come back with every AMS slot's K-profile
selection cleared (``cali_idx`` -1) while spool, colour and tag are unchanged,
so queued jobs print on the default K without any warning.

One guard per MQTT client. It remembers the ``cali_idx`` each loaded slot was
seen with (that includes every profile Printbuddy selects, because the printer
reports it back) together with the slot's filament fingerprint and nozzle
size. Only in a short window after a new MQTT session -- a printer restart
always starts one -- a slot that comes back with the selection lost, the same
filament and the same nozzle gets its profile re-selected with the same
``extrusion_cali_sel`` command Printbuddy already uses. Everything else is left
alone: a lost selection outside that window is a deliberate change and is
forgotten, a changed filament or nozzle drops the memory, and a Default pick
made through Printbuddy is never undone.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# States in which re-selecting a profile cannot touch a print.
IDLE_STATES = frozenset({"IDLE", "FINISH", "FAILED"})
# How long after a new MQTT session a lost selection counts as restart damage.
RESTART_WINDOW_S = 300.0
# Time between two re-selections of one slot: the push right after a send can
# still show the old value.
RETRY_INTERVAL_S = 30.0
# Re-selections before giving up (e.g. the profile was deleted on the printer).
MAX_ATTEMPTS = 3

_FINGERPRINT_FIELDS = ("tray_type", "tray_info_idx", "tray_color", "tag_uid", "tray_uuid")

SlotKey = tuple[int, int]


@dataclass
class _Remembered:
    cali_idx: int
    fingerprint: tuple
    filament_id: str
    nozzle_diameter: str


@dataclass
class _Watch:
    next_try: float = 0.0
    attempts: int = 0


def _loaded_trays(raw: dict) -> list[tuple[int, int, dict]]:
    """``(ams_id, tray_id, tray)`` in the addressing extrusion_cali_sel takes.

    External spools are ams 255, tray 0/1 (vt ids 254/255).
    """
    ams_raw = raw.get("ams")
    units = ams_raw.get("ams", []) if isinstance(ams_raw, dict) else ams_raw if isinstance(ams_raw, list) else []
    trays: list[tuple[int, int, dict]] = []
    for unit in units:
        if not isinstance(unit, dict):
            continue
        try:
            ams_id = int(unit.get("id", 0))
        except (TypeError, ValueError):
            continue
        for tray in unit.get("tray") or []:
            if not isinstance(tray, dict):
                continue
            try:
                trays.append((ams_id, int(tray.get("id", 0)), tray))
            except (TypeError, ValueError):
                continue
    vt_raw = raw.get("vt_tray")
    for vt in [vt_raw] if isinstance(vt_raw, dict) else vt_raw if isinstance(vt_raw, list) else []:
        if not isinstance(vt, dict):
            continue
        try:
            vt_id = int(vt.get("id", 254))
        except (TypeError, ValueError):
            continue
        if vt_id in (254, 255):
            trays.append((255, vt_id - 254, vt))
    return trays


def _cali_idx(tray: dict) -> int | None:
    try:
        return int(tray["cali_idx"])
    except (KeyError, TypeError, ValueError):
        return None


def _slot_nozzle(state, ams_id: int, tray_id: int) -> str | None:
    """Nozzle diameter feeding a slot; None on a dual-nozzle printer whose mapping is unknown."""
    nozzles = getattr(state, "nozzles", None) or []
    if not nozzles:
        return None
    left = str(getattr(nozzles[0], "nozzle_diameter", "") or "")
    right = str(getattr(nozzles[1], "nozzle_diameter", "") or "") if len(nozzles) > 1 else ""
    if not right or right == left:
        return left or None
    # Dual nozzle with different sizes: same extruder mapping as _apply_pa_after_refresh.
    if ams_id == 255:
        extruder = 1 - tray_id
    else:
        extruder = (getattr(state, "ams_extruder_map", None) or {}).get(str(ams_id))
    if extruder not in (0, 1):
        return None
    return (left, right)[extruder] or None


class KProfileDriftGuard:
    def __init__(self, serial_number: str):
        self.serial_number = serial_number
        self._remembered: dict[SlotKey, _Remembered] = {}
        self._watches: dict[SlotKey, _Watch] = {}
        self._session_start: float | None = None

    def session_started(self) -> None:
        """A new MQTT session: a lost selection reported now may be restart damage."""
        self._session_start = time.monotonic()
        self._watches.clear()

    def forget_slot(self, ams_id: int, tray_id: int) -> None:
        """A Default pick sent through Printbuddy: never put the old profile back."""
        self._remembered.pop((ams_id, tray_id), None)
        self._watches.pop((ams_id, tray_id), None)

    def observe(self, state, select: Callable[..., bool]) -> None:
        """Run on every status push; ``select`` is the client's extrusion_cali_sel."""
        if not getattr(state, "connected", False):
            return
        now = time.monotonic()
        in_window = self._session_start is not None and now - self._session_start < RESTART_WINDOW_S
        idle = (getattr(state, "state", "") or "").upper() in IDLE_STATES
        for ams_id, tray_id, tray in _loaded_trays(getattr(state, "raw_data", None) or {}):
            key = (ams_id, tray_id)
            if not tray.get("tray_type"):
                self.forget_slot(ams_id, tray_id)
                continue
            cali_idx = _cali_idx(tray)
            if cali_idx is None:
                continue
            fingerprint = tuple(str(tray.get(f) or "") for f in _FINGERPRINT_FIELDS)
            if cali_idx >= 0:
                self._remembered[key] = _Remembered(
                    cali_idx,
                    fingerprint,
                    str(tray.get("tray_info_idx") or ""),
                    _slot_nozzle(state, ams_id, tray_id) or "",
                )
                self._watches.pop(key, None)
                continue
            mem = self._remembered.get(key)
            if mem is None:
                continue
            if mem.fingerprint != fingerprint:
                logger.info(
                    "[%s] AMS%d-T%d filament changed; not restoring its old K-profile", self.serial_number, *key
                )
                self.forget_slot(ams_id, tray_id)
                continue
            if not in_window and key not in self._watches:
                # Lost while the session was up: someone chose Default on purpose.
                self.forget_slot(ams_id, tray_id)
                continue
            if not idle:
                continue
            watch = self._watches.setdefault(key, _Watch())
            if now < watch.next_try:
                continue
            if watch.attempts >= MAX_ATTEMPTS:
                logger.warning(
                    "[%s] AMS%d-T%d still has no K-profile after %d re-selections of cali_idx=%d; giving up. "
                    "Check that the profile still exists on the printer.",
                    self.serial_number,
                    ams_id,
                    tray_id,
                    MAX_ATTEMPTS,
                    mem.cali_idx,
                )
                self.forget_slot(ams_id, tray_id)
                continue
            nozzle = _slot_nozzle(state, ams_id, tray_id)
            if not nozzle or nozzle != mem.nozzle_diameter:
                logger.info(
                    "[%s] AMS%d-T%d nozzle is %s, profile was seen on %s; not restoring",
                    self.serial_number,
                    ams_id,
                    tray_id,
                    nozzle or "unknown",
                    mem.nozzle_diameter or "unknown",
                )
                self.forget_slot(ams_id, tray_id)
                continue
            watch.attempts += 1
            watch.next_try = now + RETRY_INTERVAL_S
            logger.info(
                "[%s] AMS%d-T%d lost its K-profile after reconnect: re-selecting cali_idx=%d "
                "(filament_id=%s, nozzle=%s), attempt %d/%d",
                self.serial_number,
                ams_id,
                tray_id,
                mem.cali_idx,
                mem.filament_id,
                nozzle,
                watch.attempts,
                MAX_ATTEMPTS,
            )
            select(
                ams_id=ams_id,
                tray_id=tray_id,
                cali_idx=mem.cali_idx,
                filament_id=mem.filament_id,
                nozzle_diameter=nozzle,
            )
