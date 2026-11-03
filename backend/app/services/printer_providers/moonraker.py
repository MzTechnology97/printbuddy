from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse

import httpx

from backend.app.services.bambu_mqtt import FilaSwitchState, NozzleInfo, PrintOptions


@dataclass
class MoonrakerPrinterState:
    connected: bool = False
    state: str = "unknown"
    current_print: str | None = None
    subtask_name: str | None = None
    progress: float = 0.0
    remaining_time: int = 0
    layer_num: int = 0
    total_layers: int = 0
    temperatures: dict[str, Any] = field(default_factory=dict)
    raw_status: dict[str, Any] = field(default_factory=dict)
    raw_data: dict[str, Any] = field(default_factory=dict)
    gcode_file: str | None = None
    subtask_id: str | None = None
    hms_errors: list[Any] = field(default_factory=list)
    kprofiles: list[Any] = field(default_factory=list)
    sdcard: bool = False
    store_to_sdcard: bool = False
    timelapse: bool = False
    ipcam: bool = False
    wifi_signal: int | None = None
    wired_network: bool = False
    door_open: bool = False
    nozzles: list[Any] = field(default_factory=lambda: [NozzleInfo(), NozzleInfo()])
    nozzle_rack: list[dict[str, Any]] = field(default_factory=list)
    print_options: PrintOptions = field(default_factory=PrintOptions)
    stg_cur: int = -1
    stg: list[int] = field(default_factory=list)
    airduct_mode: int = 0
    speed_level: int = 2
    chamber_light: bool = False
    active_extruder: int = 0
    tray_now: int = 255
    ams_status_main: int = 0
    ams_status_sub: int = 0
    mc_print_sub_stage: int = 0
    ams_extruder_map: dict[str, int] = field(default_factory=dict)
    fila_switch: FilaSwitchState = field(default_factory=FilaSwitchState)
    last_ams_update: float = 0.0
    printable_objects: dict[int, dict[str, Any]] = field(default_factory=dict)
    printable_objects_bbox_all: list[float] | None = None
    skipped_objects: list[int] = field(default_factory=list)
    cooling_fan_speed: int | None = None
    big_fan1_speed: int | None = None
    big_fan2_speed: int | None = None
    heatbreak_fan_speed: int | None = None
    firmware_version: str | None = None
    developer_mode: bool | None = None


def _map_moonraker_state(raw_state: Any) -> str:
    """Map Klipper/Moonraker print states onto Printbuddy's existing status names."""
    state = str(raw_state or "").lower()
    return {
        "printing": "RUNNING",
        "paused": "PAUSE",
        "complete": "FINISH",
        "cancelled": "FAILED",
        "error": "FAILED",
        "standby": "IDLE",
        "ready": "IDLE",
    }.get(state, str(raw_state or "unknown"))


# fork: Moonraker's history status of a job that ended -> Printbuddy's print end.
_HISTORY_END = {
    "completed": "completed",
    "cancelled": "stopped",
    "error": "failed",
    "klippy_shutdown": "failed",
    "klippy_disconnect": "failed",
    "server_exit": "failed",
    "interrupted": "failed",
}


def _heater_temperature(values: dict[str, Any]) -> float:
    return float(values.get("temperature") or 0.0) if isinstance(values, dict) else 0.0


def _heater_target(values: dict[str, Any]) -> float:
    return float(values.get("target") or 0.0) if isinstance(values, dict) else 0.0


def _is_heating(values: dict[str, Any]) -> bool:
    current = _heater_temperature(values)
    target = _heater_target(values)
    return target > 0 and target - current > 1.0


def _moonraker_temperatures(status: dict[str, Any]) -> dict[str, Any]:
    """Normalize Moonraker heater objects to the temperature keys used by the UI."""
    extruder = status.get("extruder", {}) if isinstance(status, dict) else {}
    bed = status.get("heater_bed", {}) if isinstance(status, dict) else {}
    temps: dict[str, Any] = {
        "nozzle": _heater_temperature(extruder),
        "nozzle_target": _heater_target(extruder),
        "nozzle_heating": _is_heating(extruder),
        "bed": _heater_temperature(bed),
        "bed_target": _heater_target(bed),
        "bed_heating": _is_heating(bed),
    }

    for name, values in status.items():
        if isinstance(values, dict) and ("temperature" in values or "target" in values):
            temps[name] = values
    return temps


def _moonraker_fan_percent(values: Any) -> int | None:
    """Convert Moonraker fan speed values to a 0-100 percentage."""
    if not isinstance(values, dict) or "speed" not in values:
        return None
    try:
        speed = float(values.get("speed") or 0.0)
    except (TypeError, ValueError):
        return None
    percent = speed * 100 if speed <= 1.0 else speed
    return max(0, min(100, round(percent)))


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _list_value(values: Any, index: int, default: Any = None) -> Any:
    if isinstance(values, list) and 0 <= index < len(values):
        return values[index]
    return default


def _normalize_creality_cfs_color(value: Any) -> str | None:
    raw = str(value or "").strip()
    if raw in {"", "-1", "none", "None"}:
        return None
    normalized = raw.lstrip("#")
    if len(normalized) == 7 and normalized.startswith("0"):
        normalized = normalized[1:]
    if len(normalized) == 6 and all(char in "0123456789abcdefABCDEF" for char in normalized):
        return f"#{normalized}"
    return raw


def _creality_cfs_material_lookup(same_material: Any) -> dict[str, str]:
    lookup: dict[str, str] = {}
    if not isinstance(same_material, list):
        return lookup
    for entry in same_material:
        if not isinstance(entry, list) or len(entry) < 4:
            continue
        material_name = str(entry[3] or "").strip()
        slots = entry[2]
        if not material_name or not isinstance(slots, list):
            continue
        for slot in slots:
            slot_name = str(slot or "").strip()
            if slot_name:
                lookup[slot_name] = material_name
    return lookup


def _normalize_creality_active_letter(value: Any) -> str | None:
    raw = str(value or "").strip().upper()
    if raw in {"", "NONE", "-1", "NULL"}:
        return None
    return raw if raw in {"A", "B", "C", "D"} else None


def _normalize_snapmaker_u1_color(value: Any) -> str | None:
    """Normalize Snapmaker U1 filament colors from RGB integers/strings.

    Public U1 Klipper code parses RFID/tag payloads into fields such as
    ``RGB_1`` and ``ARGB_COLOR``. Device-local Moonraker status payloads may
    expose either those names or frontend-style string colors; accept both.
    """
    if value is None:
        return None
    if isinstance(value, int):
        rgb = value & 0xFFFFFF
        return f"#{rgb:06X}"
    raw = str(value).strip()
    if not raw or raw.lower() in {"none", "null", "unknown", "-1"}:
        return None
    if raw.startswith("0x"):
        try:
            return f"#{int(raw, 16) & 0xFFFFFF:06X}"
        except ValueError:
            return None
    normalized = raw.lstrip("#")
    if len(normalized) == 8 and all(char in "0123456789abcdefABCDEF" for char in normalized):
        # Snapmaker U1 print_task_config reports colors as RRGGBBAA strings
        # (for example E72F1DFF). Older anticipated RFID payloads may report
        # ARGB strings; prefer RRGGBB when the trailing byte looks like alpha.
        normalized = normalized[:6] if normalized[-2:].upper() in {"00", "FF"} else normalized[-6:]
    if len(normalized) == 6 and all(char in "0123456789abcdefABCDEF" for char in normalized):
        return f"#{normalized}"
    return raw


def _snapmaker_first_value(values: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in values and values.get(key) not in (None, ""):
            return values.get(key)
    return None


def _snapmaker_clean_text(value: Any) -> str:
    raw = str(value or "").strip()
    return "" if raw.lower() in {"", "none", "null", "unknown", "-1"} else raw


def _snapmaker_list_value(config: dict[str, Any], key: str, index: int) -> Any:
    value = config.get(key) if isinstance(config, dict) else None
    if isinstance(value, list) and 0 <= index < len(value):
        return value[index]
    return None


def _snapmaker_u1_task_filament_metadata(config: dict[str, Any], index: int) -> dict[str, Any]:
    """Extract real U1 slot metadata from print_task_config arrays.

    Real U1 exports show per-slot material data in ``print_task_config`` rather
    than ``filament_parameters``. ``filament_edit`` is false for RFID/official
    Snapmaker rolls and true for manually edited/default slots. Slots with
    filament_type NONE are physically present but unset, so do not surface the
    default white color as a real spool color.
    """
    if not isinstance(config, dict):
        return {}

    material = _snapmaker_clean_text(_snapmaker_list_value(config, "filament_type", index))
    subtype = _snapmaker_clean_text(_snapmaker_list_value(config, "filament_sub_type", index))
    vendor = _snapmaker_clean_text(_snapmaker_list_value(config, "filament_vendor", index))
    exists = bool(_snapmaker_list_value(config, "filament_exist", index))
    official = bool(_snapmaker_list_value(config, "filament_official", index))
    edited = bool(_snapmaker_list_value(config, "filament_edit", index))
    sku = _safe_int(_snapmaker_list_value(config, "filament_sku", index))
    color = None
    if material:
        color = _normalize_snapmaker_u1_color(_snapmaker_list_value(config, "filament_color_rgba", index))
        if color is None:
            color = _normalize_snapmaker_u1_color(_snapmaker_list_value(config, "filament_color", index))

    source = "unset"
    if material:
        source = "manual" if edited else "rfid"

    return {
        "material": material,
        "subtype": subtype,
        "vendor": vendor,
        "color": color,
        "exists": exists,
        "official": official,
        "edited": edited,
        "sku": sku,
        "source": source,
    }


def _snapmaker_u1_filament_metadata(values: dict[str, Any]) -> dict[str, Any]:
    """Extract material/color/weight from known and anticipated U1 spool payloads."""
    if not isinstance(values, dict):
        return {}
    source = values
    for key in ("filament", "filament_info", "spool", "tray", "rfid", "tag"):
        nested = values.get(key)
        if isinstance(nested, dict):
            source = {**values, **nested}
            break

    material = _snapmaker_first_value(
        source,
        (
            "MAIN_TYPE",
            "main_type",
            "material",
            "tray_type",
            "type",
            "filament_type",
            "filament_material",
        ),
    )
    subtype = _snapmaker_first_value(source, ("SUB_TYPE", "sub_type", "subtype", "tray_sub_brands"))
    vendor = _snapmaker_first_value(source, ("VENDOR", "vendor", "MANUFACTURER", "manufacturer", "brand"))
    color = _normalize_snapmaker_u1_color(
        _snapmaker_first_value(
            source,
            ("RGB_1", "rgb_1", "ARGB_COLOR", "argb_color", "color", "tray_color", "rgba", "filament_color"),
        )
    )
    weight = _safe_float(_snapmaker_first_value(source, ("WEIGHT", "weight", "label_weight", "initial_weight")))
    remaining_weight = _safe_float(
        _snapmaker_first_value(
            source,
            (
                "remaining_weight",
                "remain_weight",
                "weight_remaining",
                "remaining_weight_g",
                "remain_weight_g",
                "remaining_g",
            ),
        )
    )
    remain = _safe_int(_snapmaker_first_value(source, ("remain", "remaining", "remain_percent", "remaining_percent")))
    if remain is None and remaining_weight is not None and weight and weight > 0:
        remain = max(0, min(100, round((remaining_weight / weight) * 100)))
    return {
        "material": str(material).strip() if material not in (None, "") else "",
        "subtype": str(subtype).strip() if subtype not in (None, "") else "",
        "vendor": str(vendor).strip() if vendor not in (None, "") else "",
        "color": color,
        "weight": weight,
        "remaining_weight": remaining_weight,
        "remain": remain,
        "raw": source,
    }


# Bambu speed modes (silent, standard, sport, ludicrous) as M220 percentages, as shown in the UI.
_SPEED_MODE_PERCENT = {1: 50, 2: 100, 3: 124, 4: 166}


def _moonraker_light_object(available: list[str]) -> str | None:
    """Pick the printer's main light among ``output_pin``/``led``/``neopixel`` objects."""
    candidates = []
    for name in available:
        kind, _, pin = name.partition(" ")
        pin = pin.lower()
        if kind not in {"output_pin", "led", "neopixel"} or not pin:
            continue
        if any(marker in pin for marker in ("nozzle", "bed", "heater", "wiper", "twist")):
            continue
        if kind == "output_pin" and not any(marker in pin for marker in ("led", "light")):
            continue
        candidates.append(name)
    for marker in ("main", "chamber", "case"):
        for name in candidates:
            if marker in name.partition(" ")[2].lower():
                return name
    return candidates[0] if candidates else None


def _moonraker_light_is_on(values: Any) -> bool:
    if not isinstance(values, dict):
        return False
    if "value" in values:
        return (_safe_float(values.get("value")) or 0) > 0
    colors = values.get("color_data")
    return isinstance(colors, list) and any(
        (_safe_float(channel) or 0) > 0 for led in colors if isinstance(led, list) for channel in led
    )


def _moonraker_speed_level(gcode_move: Any) -> int | None:
    factor = _safe_float(gcode_move.get("speed_factor")) if isinstance(gcode_move, dict) else None
    if factor is None:
        return None
    return min(_SPEED_MODE_PERCENT, key=lambda mode: abs(_SPEED_MODE_PERCENT[mode] - factor * 100))


def _apply_moonraker_exclude_object(state: MoonrakerPrinterState, exclude_object: Any) -> None:
    """Klipper `exclude_object` -> the skip-objects state used for Bambu (ids are the list positions)."""
    # shortcut: the modal places markers on a top-down plate view, but Klipper's cover is the angled slicer
    # thumbnail, so markers only show the layout; draw the polygons instead if that proves confusing.
    objects = exclude_object.get("objects") if isinstance(exclude_object, dict) else None
    objects = [obj for obj in objects or [] if isinstance(obj, dict) and obj.get("name")]
    excluded = set(exclude_object.get("excluded_objects") or []) if objects else set()
    state.printable_objects = {}
    points: list[list[float]] = []
    for index, obj in enumerate(objects):
        center = obj.get("center")
        center = center if isinstance(center, list) and len(center) >= 2 else [None, None]
        state.printable_objects[index] = {
            "name": str(obj["name"]),
            "x": _safe_float(center[0]),
            "y": _safe_float(center[1]),
        }
        points.extend(p for p in obj.get("polygon") or [] if isinstance(p, list) and len(p) >= 2)
    state.skipped_objects = [index for index, obj in enumerate(objects) if obj["name"] in excluded]
    xs, ys = [float(p[0]) for p in points], [float(p[1]) for p in points]
    state.printable_objects_bbox_all = [min(xs), min(ys), max(xs), max(ys)] if points else None


def _apply_moonraker_fans(state: MoonrakerPrinterState, status: dict[str, Any]) -> None:
    """Map reported Klipper/Moonraker fan objects onto Printbuddy fan badges.

    Moonraker object names are printer-config dependent. Keep unsupported fans as
    None so the frontend can hide missing capabilities, while preserving 0% for
    fans that exist but are currently off.
    """
    state.cooling_fan_speed = None
    state.big_fan1_speed = None
    state.big_fan2_speed = None
    state.heatbreak_fan_speed = None

    for name, values in status.items():
        percent = _moonraker_fan_percent(values)
        if percent is None:
            continue

        normalized = name.lower()
        if normalized == "fan":
            state.cooling_fan_speed = percent
        elif normalized.startswith("heater_fan "):
            fan_name = normalized.removeprefix("heater_fan ")
            if state.heatbreak_fan_speed is None or any(
                marker in fan_name for marker in ("hotend", "heatbreak", "extruder", "nozzle")
            ):
                state.heatbreak_fan_speed = percent
        elif normalized.startswith("fan_generic "):
            fan_name = normalized.removeprefix("fan_generic ")
            if any(marker in fan_name for marker in ("chamber", "cavity", "exhaust", "filter", "enclosure")):
                state.big_fan2_speed = percent
            elif any(marker in fan_name for marker in ("aux", "auxiliary", "side", "boost")):
                state.big_fan1_speed = percent


def _remaining_minutes(print_stats: dict[str, Any], progress: float) -> int:
    """Estimate remaining print time from Moonraker duration fields.

    Prefer Moonraker's slicer estimate when available. Fall back to elapsed
    duration/progress for older or sparse firmware payloads.
    """
    print_duration = float(print_stats.get("print_duration") or 0.0)
    estimated_time = _safe_float(print_stats.get("estimated_time"))
    if estimated_time and estimated_time > 0:
        return int(max(estimated_time - print_duration, 0.0) // 60)
    if progress <= 0 or print_duration <= 0:
        return 0
    total_seconds = print_duration / min(progress / 100.0, 1.0)
    remaining_seconds = max(total_seconds - print_duration, 0.0)
    return int(remaining_seconds // 60)


def _moonraker_progress_percent(virtual_sdcard: dict[str, Any], display_status: dict[str, Any]) -> float:
    """Return the best available Moonraker print progress percentage.

    Some K2 Plus firmware samples keep ``virtual_sdcard.progress`` / ``display_status.progress`` stale during short prints. When Moonraker also reports file byte position and size, prefer the furthest trustworthy value.
    """
    candidates: list[float] = []
    for progress in (virtual_sdcard.get("progress"), display_status.get("progress")):
        fractional = _safe_float(progress)
        if fractional is not None:
            candidates.append(max(0.0, min(fractional * 100.0, 100.0)))

    file_position = _safe_float(virtual_sdcard.get("file_position"))
    file_size = _safe_float(virtual_sdcard.get("file_size") or virtual_sdcard.get("total_size"))
    if file_position is not None and file_size and file_size > 0:
        candidates.append(max(0.0, min((file_position / file_size) * 100.0, 100.0)))

    return max(candidates) if candidates else 0.0


def _moonraker_url_candidates(base_url: str) -> list[str]:
    """Return Moonraker probe URLs, including :7125 fallback for Fluidd UI URLs."""
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.hostname:
        return [base_url.rstrip("/") + "/"]

    primary = base_url.rstrip("/") + "/"
    candidates = [primary]

    # Users often paste the Fluidd/Mainsail UI URL (http://printer/) instead
    # of the Moonraker API URL. Fluidd talks to Moonraker on 7125, so try that
    # as a second candidate when the supplied URL is not already explicit 7125.
    # Some Elegoo/Fluidd images do the inverse: port 7125 is not reachable from
    # the add-on/container network, but the UI host proxies Moonraker at :80.
    # Therefore an explicit :7125 URL also gets a no-port fallback.
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if parsed.port != 7125:
        fallback = urlunparse((parsed.scheme or "http", f"{host}:7125", "", "", "", "")).rstrip("/") + "/"
        if fallback not in candidates:
            candidates.append(fallback)
    else:
        fallback = urlunparse((parsed.scheme or "http", host, "", "", "", "")).rstrip("/") + "/"
        if fallback not in candidates:
            candidates.append(fallback)
    return candidates


def _creality_cfs_slot_name(tray_id: int) -> str | None:
    if tray_id < 0 or tray_id > 15:
        return None
    unit = tray_id // 4 + 1
    letter = "ABCD"[tray_id % 4]
    return f"T{unit}{letter}"


def _creality_cfs_active_tray_id(value: Any) -> int | None:
    raw = str(value or "").strip().upper()
    if raw in {"", "NONE", "-1"}:
        return None
    if raw in "ABCD":
        return "ABCD".index(raw)
    if raw.startswith("T") and len(raw) >= 3 and raw[1].isdigit() and raw[2] in "ABCD":
        unit = int(raw[1]) - 1
        return unit * 4 + "ABCD".index(raw[2])
    return None


def _macro_object_to_command_name(object_name: str) -> str | None:
    prefix = "gcode_macro "
    if not object_name.startswith(prefix):
        return None
    macro = object_name[len(prefix) :].strip()
    return macro or None


def _moonraker_camera_origin(base_url: str) -> str:
    """Origin used to absolutize relative Moonraker webcam paths.

    Webcam streams are almost always served by the Fluidd/Mainsail/CosmOS
    reverse-proxy on port 80 (or the UI host), not by Moonraker's API port
    7125. Relative paths like ``/webcam/?action=stream`` must therefore resolve
    against the UI host — otherwise we produce broken URLs such as
    ``http://printer:7125/webcam/?action=stream``.
    """
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.hostname:
        return base_url.rstrip("/") + "/"

    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    # Moonraker API port → prefer default HTTP origin for relative camera paths.
    if parsed.port == 7125:
        return urlunparse((parsed.scheme or "http", host, "", "", "", "")).rstrip("/") + "/"

    netloc = parsed.netloc or host
    return urlunparse((parsed.scheme or "http", netloc, "", "", "", "")).rstrip("/") + "/"


def _absolute_moonraker_camera_url(base_url: str, value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme and parsed.netloc:
        return raw
    return urljoin(_moonraker_camera_origin(base_url), raw.lstrip("/"))


def _normalize_moonraker_webcam(base_url: str, webcam: dict[str, Any]) -> dict[str, Any] | None:
    stream_url = _absolute_moonraker_camera_url(
        base_url,
        webcam.get("stream_url")
        or webcam.get("streamUrl")
        or webcam.get("urlStream")
        or webcam.get("url_stream")
        or webcam.get("url"),
    )
    snapshot_url = _absolute_moonraker_camera_url(
        base_url,
        webcam.get("snapshot_url")
        or webcam.get("snapshotUrl")
        or webcam.get("urlSnapshot")
        or webcam.get("url_snapshot"),
    )
    if not stream_url and snapshot_url:
        stream_url = snapshot_url
    if not stream_url:
        return None
    camera_type = "snapshot" if snapshot_url and stream_url == snapshot_url else "mjpeg"
    return {
        "name": str(webcam.get("name") or webcam.get("id") or "Moonraker camera"),
        "stream_url": stream_url,
        "snapshot_url": snapshot_url,
        "camera_type": camera_type,
        "enabled": bool(webcam.get("enabled", True)),
        "raw": webcam,
    }


class MoonrakerPrinterClient:
    """Minimal Klipper/Mainsail provider backed by Moonraker.

    Mainsail is a UI; the printer control API is normally Moonraker. This first
    scaffold validates connectivity and exposes a state shape that can be mapped
    into the existing status pipeline in follow-up work. Bambu-specific commands
    intentionally return False instead of pretending to be supported.
    """

    def __init__(
        self,
        base_url: str,
        auth_token: str | None = None,
        timeout: float = 5.0,
        printer_model: str | None = None,
        on_state_change: Any | None = None,
        on_print_start: Any | None = None,
        on_print_complete: Any | None = None,
        on_bed_temp_update: Any | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("Moonraker base URL is required for Klipper/Mainsail printers")
        self.base_url = base_url.rstrip("/") + "/"
        self.base_url_candidates = _moonraker_url_candidates(base_url)
        self.auth_token = auth_token
        self.timeout = timeout
        self.printer_model = printer_model or ""
        self._gcodes_path_prefixes: list[str] | None = None
        self.state = MoonrakerPrinterState()
        self.on_state_change = on_state_change
        self.on_print_start = on_print_start
        self.on_print_complete = on_print_complete
        self.on_bed_temp_update = on_bed_temp_update
        self._last_state: str | None = None
        self._has_status_sample = False
        self._last_bed_temp: float | None = None
        self._current_print_started_at: float | None = None
        self.webcams: list[dict[str, Any]] = []
        self._file_metadata: dict[str, dict[str, Any]] = {}  # fork: per printed file, read once

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.auth_token}"} if self.auth_token else {}

    def _get(self, path: str, *, base_url: str | None = None) -> dict[str, Any]:
        response = httpx.get(
            urljoin(base_url or self.base_url, path.lstrip("/")), headers=self._headers, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            return data["result"]
        return data if isinstance(data, dict) else {"value": data}

    def _ended_job_status(self) -> str | None:
        """How Moonraker says the last print ended (``klippy_disconnect``...): its history's newest job."""
        try:
            jobs = self._get("server/history/list?limit=1&order=desc").get("jobs") or []
        except Exception:  # noqa: BLE001 - optional: the polled state decides without it
            return None
        return _HISTORY_END.get(jobs[0].get("status")) if jobs and isinstance(jobs[0], dict) else None

    def _print_file_metadata(self, filename: str) -> dict[str, Any]:
        """Moonraker's metadata of the file being printed (slicer estimate, layer count), read once per file."""
        if filename not in self._file_metadata:
            try:
                self._file_metadata[filename] = self._get(f"server/files/metadata?filename={quote(filename)}")
            except Exception:  # noqa: BLE001 - optional: the print's own status still applies
                self._file_metadata[filename] = {}
        return self._file_metadata[filename]

    def _query_objects(self, object_names: list[str]) -> dict[str, Any]:
        query = "printer/objects/query?" + "&".join(quote(name, safe="") for name in object_names)
        objects = self._get(query)
        return objects.get("status", objects) if isinstance(objects, dict) else {}

    def _is_elegoo_printer(self) -> bool:
        return "elegoo" in self.printer_model.lower()

    def _configured_gcodes_path_prefixes(self) -> list[str]:
        """Return absolute Klipper gcode storage paths for Elegoo/Moonraker aliases.

        Elegoo firmware variants commonly use either ``~/gcode_files``
        (``/home/mks/gcode_files``) or the newer Moonraker default
        ``~/printer_data/gcodes``. Query Klipper's virtual_sdcard config when
        available, then fall back to both known MKS paths so paths captured by
        old jobs or UIs still resolve.
        """
        if self._gcodes_path_prefixes is not None:
            return self._gcodes_path_prefixes

        prefixes: list[str] = []
        try:
            config = self._query_objects(["configfile"]).get("configfile", {})
            settings = config.get("settings", {}) if isinstance(config, dict) else {}
            virtual_sdcard = settings.get("virtual_sdcard", {}) if isinstance(settings, dict) else {}
            raw_path = str(virtual_sdcard.get("path") or "").strip()
            if raw_path:
                if raw_path == "~":
                    raw_path = "/home/mks"
                elif raw_path.startswith("~/"):
                    raw_path = f"/home/mks/{raw_path[2:]}"
                prefixes.append(raw_path.rstrip("/"))
        except Exception:  # noqa: BLE001 - path aliasing must not break file operations
            pass

        for fallback in ("/home/mks/gcode_files", "/home/mks/printer_data/gcodes"):
            if fallback not in prefixes:
                prefixes.append(fallback)
        self._gcodes_path_prefixes = prefixes
        return prefixes

    def _normalize_gcodes_path(self, remote_path: str) -> str:
        """Normalize user/provider paths into Moonraker's gcodes root."""
        normalized = remote_path.strip()
        if self._is_elegoo_printer():
            # Bambu-style paths can leak into generic queue/file flows. On Elegoo
            # machines, /model means the Klipper virtual_sdcard gcode directory.
            if normalized == "/model" or normalized == "model":
                return ""
            if normalized.startswith("/model/"):
                normalized = normalized[len("/model/") :]
            elif normalized.startswith("model/"):
                normalized = normalized[len("model/") :]

            for prefix in self._configured_gcodes_path_prefixes():
                if normalized == prefix:
                    return ""
                if normalized.startswith(f"{prefix}/"):
                    normalized = normalized[len(prefix) + 1 :]
                    break

        normalized = normalized.strip("/")
        if normalized.startswith("gcodes/"):
            normalized = normalized[len("gcodes/") :]
        return normalized

    def _available_objects(self) -> list[str]:
        objects = self._get("printer/objects/list")
        available = objects.get("objects", []) if isinstance(objects, dict) else []
        return [name for name in available if isinstance(name, str)] if isinstance(available, list) else []

    def _available_fan_objects(self, available: list[str] | None = None) -> list[str]:
        if available is None:
            available = self._available_objects()
        return [
            str(name)
            for name in available
            if name == "fan" or name.startswith("fan_generic ") or name.startswith("heater_fan ")
        ]

    def _query_fan_status(self, available: list[str] | None = None) -> dict[str, Any]:
        try:
            fan_objects = self._available_fan_objects(available)
            if not fan_objects:
                return {}
            return self._query_objects(fan_objects)
        except Exception:  # noqa: BLE001 - fans are optional; keep core status healthy if discovery fails
            return {}

    def _target_fan_object(self, fan: str) -> str | None:
        """Return the Klipper fan object to control for a Printbuddy fan role."""
        normalized_fan = fan.strip().lower().replace("-", "_")
        available = [name.lower() for name in self._available_fan_objects()]
        if normalized_fan in {"part", "model", "model_fan", "cooling"}:
            return "fan" if "fan" in available else None

        marker_sets = {
            "aux": ("aux", "auxiliary", "side", "boost"),
            "auxiliary": ("aux", "auxiliary", "side", "boost"),
            "auxiliary_fan": ("aux", "auxiliary", "side", "boost"),
            "chamber": ("chamber", "cavity", "exhaust", "filter", "enclosure"),
            "box": ("chamber", "cavity", "exhaust", "filter", "enclosure"),
            "box_fan": ("chamber", "cavity", "exhaust", "filter", "enclosure"),
        }
        markers = marker_sets.get(normalized_fan)
        if not markers:
            return None
        for object_name in available:
            if not object_name.startswith("fan_generic "):
                continue
            fan_name = object_name.removeprefix("fan_generic ")
            if any(marker in fan_name for marker in markers):
                return object_name
        return None

    def _available_cfs_objects(self, available: list[str] | None = None) -> list[str]:
        if available is None:
            available = self._available_objects()
        wanted = ["box", "box_cfs_runtime", "filament_rack", "filament_switch_sensor filament_sensor"]
        return [name for name in wanted if name in available]

    def _query_cfs_status(self, available: list[str] | None = None) -> dict[str, Any]:
        try:
            cfs_objects = self._available_cfs_objects(available)
            if not cfs_objects:
                return {}
            return self._query_objects(cfs_objects)
        except Exception:  # noqa: BLE001 - CFS is optional; keep core Moonraker status healthy
            return {}

    def _available_snapmaker_u1_objects(self, available: list[str] | None = None) -> list[str]:
        """Return Snapmaker U1 specific objects plus discovered extruders/sensors.

        U1's public Klipper code exposes four extruder objects, two
        ``filament_feed`` modules, and ``temperature_sensor cavity`` for chamber
        temperature. Object discovery keeps this harmless for normal Moonraker
        printers and allows newer device-local spool metadata objects to be used
        when present.
        """
        if available is None:
            available = self._available_objects()
        available_set = set(available)
        wanted = [
            "temperature_sensor cavity",
            "filament_feed left",
            "filament_feed right",
            "filament_parameters",
            "print_task_config",
            "toolhead",
        ]
        wanted.extend(["extruder", "extruder1", "extruder2", "extruder3"])
        wanted.extend(
            name
            for name in available_set
            if any(token in name.lower() for token in ("snapmaker", "spool", "rfid", "nfc"))
        )
        return [name for name in dict.fromkeys(wanted) if name in available_set]

    def _query_snapmaker_u1_status(self, available: list[str] | None = None) -> dict[str, Any]:
        try:
            u1_objects = self._available_snapmaker_u1_objects(available)
            if not u1_objects:
                return {}
            return self._query_objects(u1_objects)
        except Exception:  # noqa: BLE001 - U1 objects are optional; do not break generic Moonraker
            return {}

    def _available_macro_names(self) -> set[str]:
        objects = self._get("printer/objects/list")
        available = objects.get("objects", []) if isinstance(objects, dict) else []
        return {macro for name in available if isinstance(name, str) and (macro := _macro_object_to_command_name(name))}

    def _send_cfs_slot_macro(self, prefix: str, tray_id: int | None = None) -> bool:
        effective_tray_id = tray_id if tray_id is not None else self.state.tray_now
        slot = _creality_cfs_slot_name(int(effective_tray_id)) if effective_tray_id is not None else None
        if slot is None:
            return False
        macro = f"{prefix}{slot}"
        if macro not in self._available_macro_names():
            return False
        return self.send_gcode(macro)

    def _send_macro_if_available(self, macro: str) -> bool:
        if macro not in self._available_macro_names():
            return False
        return self.send_gcode(macro)

    def _is_k2_openhost_cfs(self) -> bool:
        # K2 OpenHost maps tools to slots itself and has no M8200 commands.
        cfs = self.state.raw_data.get("cfs")
        return isinstance(cfs, dict) and cfs.get("type") == "k2_openhost_cfs"

    def _has_creality_cfs(self) -> bool:
        if isinstance(self.state.raw_data.get("cfs"), dict):
            return not self._is_k2_openhost_cfs()
        for ams_unit in self.state.raw_data.get("ams") or []:
            if isinstance(ams_unit, dict) and ams_unit.get("module_type") == "cfs":
                return True
        return False

    def _creality_cfs_tray_ids(self) -> set[int]:
        tray_ids: set[int] = set()
        for ams_unit in self.state.raw_data.get("ams") or []:
            if not isinstance(ams_unit, dict) or ams_unit.get("module_type") != "cfs":
                continue
            try:
                ams_id = int(ams_unit.get("id", 0))
            except (TypeError, ValueError):
                continue
            for tray in ams_unit.get("tray") or []:
                if not isinstance(tray, dict):
                    continue
                try:
                    tray_id = int(tray.get("id", 0))
                except (TypeError, ValueError):
                    continue
                tray_ids.add(ams_id * 4 + tray_id)
        return tray_ids

    def _target_cfs_tray_from_mapping(self, ams_mapping: list[int] | None) -> int | None:
        if not ams_mapping or not self._has_creality_cfs():
            return None
        cfs_tray_ids = self._creality_cfs_tray_ids()
        for mapped in ams_mapping:
            try:
                tray_id = int(mapped)
            except (TypeError, ValueError):
                continue
            if tray_id < 0:
                continue
            if not cfs_tray_ids or tray_id in cfs_tray_ids:
                return tray_id
        return None

    def _current_cfs_tray_id(self) -> int | None:
        if self.state.tray_now is not None and self.state.tray_now != 255:
            try:
                return int(self.state.tray_now)
            except (TypeError, ValueError):
                pass
        box = self.state.raw_data.get("box")
        if isinstance(box, dict):
            for unit_index, unit_name in enumerate(("T1", "T2", "T3", "T4")):
                unit = box.get(unit_name)
                if not isinstance(unit, dict) or str(unit.get("state") or "").lower() != "connect":
                    continue
                active = _creality_cfs_active_tray_id(unit.get("filament"))
                if active is not None:
                    return unit_index * 4 + active
        return None

    def _refresh_and_verify_cfs_slot(self, tray_id: int) -> bool:
        try:
            self.request_status_update()
        except Exception:
            return False
        return self._current_cfs_tray_id() == tray_id

    def _prepare_creality_cfs_slot_for_print(self, ams_mapping: list[int] | None) -> bool:
        target_tray_id = self._target_cfs_tray_from_mapping(ams_mapping)
        if target_tray_id is None:
            return True
        if _creality_cfs_slot_name(target_tray_id) is None:
            return False

        current_tray_id = self._current_cfs_tray_id()
        if current_tray_id == target_tray_id:
            return True
        if current_tray_id is not None and current_tray_id != 255:
            if not self.ams_unload_filament(current_tray_id):
                return False
            try:
                self.request_status_update()
            except Exception:
                return False
        if not self.ams_load_filament(target_tray_id):
            return False
        return self._refresh_and_verify_cfs_slot(target_tray_id)

    def _normalize_file_entries(self, entries: Any, normalized: str) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        seen_dirs: set[str] = set()
        prefix = normalized.strip("/")
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            raw_path = str(entry.get("path") or entry.get("filename") or entry.get("name") or "").lstrip("/")
            if not raw_path:
                continue

            if prefix:
                if raw_path == prefix:
                    continue
                if raw_path.startswith(f"{prefix}/"):
                    relative_path = raw_path[len(prefix) + 1 :]
                    display_path = raw_path
                else:
                    continue
            else:
                relative_path = raw_path
                display_path = raw_path

            if not relative_path:
                continue

            if "/" in relative_path:
                directory_name = relative_path.split("/", 1)[0]
                directory_path = f"{prefix}/{directory_name}" if prefix else directory_name
                if directory_path in seen_dirs:
                    continue
                seen_dirs.add(directory_path)
                files.append(
                    {
                        "name": directory_name,
                        "type": "directory",
                        "size": None,
                        "modified": None,
                        "path": f"/{directory_path}",
                    }
                )
                continue

            name = relative_path
            modified_raw = entry.get("modified")
            modified = None
            if isinstance(modified_raw, int | float):
                modified = datetime.fromtimestamp(float(modified_raw), tz=timezone.utc).isoformat()
            elif modified_raw is not None:
                modified = str(modified_raw)
            file_type = "directory" if entry.get("type") == "directory" or entry.get("dirname") else "file"
            files.append(
                {
                    "name": name,
                    "type": file_type,
                    "size": entry.get("size"),
                    "modified": modified,
                    "path": f"/{display_path}",
                }
            )
        return files

    def discover_webcams(self) -> list[dict[str, Any]]:
        """Return normalized Moonraker webcam entries, if the server exposes any."""
        # Prefer a confirmed working API URL (e.g. CosmOS on :80 when the form
        # still has the default :7125) before resolving relative camera paths.
        if not self.state.connected:
            try:
                self.connect()
            except Exception:
                pass
        result = self._get("server/webcams/list")
        webcams = result.get("webcams") if isinstance(result, dict) else None
        if webcams is None and isinstance(result, dict):
            webcams = result.get("result")
        normalized: list[dict[str, Any]] = []
        for webcam in webcams if isinstance(webcams, list) else []:
            if not isinstance(webcam, dict):
                continue
            candidate = _normalize_moonraker_webcam(self.base_url, webcam)
            if candidate:
                normalized.append(candidate)
        return normalized

    def _apply_k2_openhost_box(self, status: dict[str, Any]) -> None:
        """Map the K2 OpenHost (Kalico) CFS: a flat ``box.slots`` list grouped by ``box.boxes`` units."""
        box = status["box"]
        slots: dict[int, dict[str, Any]] = {}
        external_slot: dict[str, Any] | None = None
        for slot in box.get("slots") or []:
            index = _safe_int(slot.get("index")) if isinstance(slot, dict) else None
            if index is None:
                continue
            # The external spool holder has no CFS position: it becomes vt_tray, like a Bambu external spool.
            if slot.get("external"):
                external_slot = slot
            elif _creality_cfs_slot_name(index):
                slots[index] = slot
        units = [unit for unit in box.get("boxes") or [] if isinstance(unit, dict) and unit.get("online", True)]
        if not units:
            units = [
                {"address": group + 1, "slots": [index for index in slots if index // 4 == group]}
                for group in sorted({index // 4 for index in slots})
            ]

        loaded_slot = _safe_int(box.get("loaded_slot"))
        self.state.tray_now = loaded_slot if loaded_slot in slots else 255
        if external_slot is not None:
            if loaded_slot == _safe_int(external_slot.get("index")):
                self.state.tray_now = 254
            remain = _safe_int(external_slot.get("rfid_percent"))
            self.state.raw_data["vt_tray"] = [
                {
                    "id": 254,
                    "tray_type": str(external_slot.get("material") or ""),
                    "tray_sub_brands": str(external_slot.get("name") or ""),
                    "tray_color": _normalize_creality_cfs_color(external_slot.get("color")),
                    "remain": -1 if remain is None else remain,
                    "tray_uuid": "",
                    "tag_uid": "",
                }
            ]
        ams_units: list[dict[str, Any]] = []
        active_slots: list[str] = []
        for unit in units:
            ams_id = (_safe_int(unit.get("address")) or 1) - 1
            trays: list[dict[str, Any]] = []
            for raw_index in unit.get("slots") or []:
                index = _safe_int(raw_index)
                slot = slots.get(index) if index is not None else None
                if slot is None:
                    continue
                name = _creality_cfs_slot_name(index)
                is_active = index == loaded_slot
                # -1 = unknown level, as on Bambu; AMSTray.remain must be an int.
                remain = _safe_int(slot.get("rfid_percent"))
                if is_active:
                    active_slots.append(name)
                trays.append(
                    {
                        "id": index % 4,
                        "slot": name,
                        "tray_type": str(slot.get("material") or ""),
                        "tray_sub_brands": str(slot.get("name") or ""),
                        "tray_color": _normalize_creality_cfs_color(slot.get("color")),
                        "remain": -1 if remain is None else remain,
                        "active": is_active,
                        "state": 11 if slot.get("present") else 9,
                        "tray_uuid": name,
                        "tag_uid": "",
                        "vendor": str(slot.get("brand") or ""),
                    }
                )
            if trays:
                ams_units.append(
                    {
                        "id": ams_id,
                        "name": f"CFS T{ams_id + 1}",
                        "humidity": _safe_int(unit.get("humidity_pct", box.get("humidity_pct"))),
                        "temp": _safe_int(unit.get("temp_c", box.get("temp_c"))),
                        "state": box.get("state"),
                        "tray": trays,
                    }
                )

        if not ams_units:
            return
        self.state.raw_data["ams"] = ams_units
        self.state.raw_data["cfs"] = {
            "type": "k2_openhost_cfs",
            "api_version": box.get("api_version"),
            "state": box.get("state"),
            "loaded_slot": loaded_slot,
            "active_slots": active_slots,
            "print_mapping": box.get("print_mapping"),
            "print_info": box.get("print_info"),
            "filament_detected": box.get("filament_detected"),
        }
        self.state.raw_data["box"] = box

    def _apply_creality_cfs_box(self, status: dict[str, Any]) -> None:
        box = status.get("box") if isinstance(status, dict) else None
        if not isinstance(box, dict):
            return
        runtime = status.get("box_cfs_runtime")
        if isinstance(box.get("slots"), list) or (
            isinstance(runtime, dict) and runtime.get("firmware_family") == "k2oh"
        ):
            self._apply_k2_openhost_box(status)
            return

        filament_rack = status.get("filament_rack") if isinstance(status, dict) else None
        sensor = status.get("filament_switch_sensor filament_sensor") if isinstance(status, dict) else None
        material_by_slot = _creality_cfs_material_lookup(box.get("same_material"))
        ams_units: list[dict[str, Any]] = []
        active_slots: list[str] = []
        self.state.tray_now = 255

        for ams_id, unit_name in enumerate(("T1", "T2", "T3", "T4")):
            unit = box.get(unit_name)
            if not isinstance(unit, dict) or str(unit.get("state") or "").lower() != "connect":
                continue

            active_letter = _normalize_creality_active_letter(unit.get("filament"))
            humidity = _safe_int(unit.get("dry_and_humidity"))
            temperature = _safe_int(unit.get("temperature"))
            trays: list[dict[str, Any]] = []

            for tray_id, letter in enumerate(("A", "B", "C", "D")):
                slot = f"{unit_name}{letter}"
                remain = _safe_int(_list_value(unit.get("remain_len"), tray_id))
                material_code = str(_list_value(unit.get("material_type"), tray_id, "") or "").strip()
                color = _normalize_creality_cfs_color(_list_value(unit.get("color_value"), tray_id))
                vendor = _list_value(unit.get("vender"), tray_id, "")
                material_name = material_by_slot.get(slot) or (material_code if material_code not in {"", "-1"} else "")
                is_active = active_letter == letter
                if is_active:
                    active_slots.append(slot)
                    self.state.tray_now = ams_id * 4 + tray_id
                trays.append(
                    {
                        "id": tray_id,
                        "slot": slot,
                        "tray_type": material_name,
                        "material_code": material_code,
                        "tray_color": color,
                        "remain": remain,
                        "active": is_active,
                        "state": 11,
                        "tray_uuid": slot,
                        "tag_uid": "",
                        "vendor": vendor,
                    }
                )

            ams_units.append(
                {
                    "id": ams_id,
                    "name": f"CFS {unit_name}",
                    "humidity": humidity,
                    "temp": temperature,
                    "state": unit.get("state"),
                    "mode": unit.get("mode"),
                    "version": unit.get("version"),
                    "sn": unit.get("sn"),
                    "tray": trays,
                }
            )

        if not ams_units:
            return

        self.state.raw_data["ams"] = ams_units
        self.state.raw_data["cfs"] = {
            "type": "creality_cfs",
            "state": box.get("state"),
            "enabled": box.get("enable"),
            "auto_refill": box.get("auto_refill"),
            "active_slots": active_slots,
            "filament_detected": sensor.get("filament_detected") if isinstance(sensor, dict) else None,
        }
        self.state.raw_data["box"] = box
        if isinstance(filament_rack, dict):
            self.state.raw_data["filament_rack"] = filament_rack

    def _apply_snapmaker_u1_status(self, status: dict[str, Any]) -> None:
        if not isinstance(status, dict):
            return

        cavity = status.get("temperature_sensor cavity")
        if isinstance(cavity, dict):
            self.state.temperatures["chamber"] = _heater_temperature(cavity)
            self.state.temperatures["chamber_target"] = _heater_target(cavity)
            self.state.temperatures["temperature_sensor cavity"] = cavity

        extruder_names = ["extruder", "extruder1", "extruder2", "extruder3"]
        nozzles: list[NozzleInfo] = []
        for index, name in enumerate(extruder_names):
            extruder = status.get(name)
            if not isinstance(extruder, dict):
                continue
            nozzle = NozzleInfo(nozzle_diameter=str(extruder.get("nozzle_diameter") or ""))
            nozzles.append(nozzle)
            suffix = "" if index == 0 else f"_{index + 1}"
            self.state.temperatures[f"nozzle{suffix}"] = _heater_temperature(extruder)
            self.state.temperatures[f"nozzle{suffix}_target"] = _heater_target(extruder)
            self.state.temperatures[f"nozzle{suffix}_heating"] = _is_heating(extruder)
        if nozzles:
            self.state.nozzles = nozzles

        toolhead = status.get("toolhead")
        active_extruder_name = toolhead.get("extruder") if isinstance(toolhead, dict) else None
        active_index: int | None = None
        if isinstance(active_extruder_name, str) and active_extruder_name in extruder_names:
            active_index = extruder_names.index(active_extruder_name)
        self.state.active_extruder = active_index if active_index is not None else -1

        task_config = status.get("print_task_config") if isinstance(status.get("print_task_config"), dict) else {}
        feed_slots: dict[int, dict[str, Any]] = {}
        for module_name in ("filament_feed left", "filament_feed right"):
            module = status.get(module_name)
            if not isinstance(module, dict):
                continue
            for key, values in module.items():
                if not isinstance(values, dict) or not key.startswith("extruder"):
                    continue
                raw_index = key.removeprefix("extruder")
                extruder_index = _safe_int(raw_index) if raw_index else 0
                if extruder_index is None:
                    continue
                metadata = _snapmaker_u1_filament_metadata(values)
                task_metadata = _snapmaker_u1_task_filament_metadata(task_config, extruder_index)
                detected = bool(values.get("filament_detected"))
                channel_state = str(values.get("channel_state") or "").strip().lower()
                channel_action_state = str(values.get("channel_action_state") or "").strip().lower()
                loaded_to_extruder = channel_state == "load_finish" or channel_action_state == "load_finish"
                material = task_metadata.get("material") or metadata["material"] or ("Unknown" if detected else "")
                feed_slots[extruder_index] = {
                    "id": extruder_index,
                    "slot": f"U1-E{extruder_index}",
                    "tray_type": material,
                    "tray_sub_brands": task_metadata.get("subtype") or metadata["subtype"],
                    "tray_color": task_metadata.get("color") or metadata["color"],
                    "remain": metadata["remain"],
                    "remaining_weight": metadata["remaining_weight"],
                    "weight": metadata["weight"],
                    "active": active_index is not None and extruder_index == active_index,
                    "state": 11 if detected else 9,
                    "tray_uuid": f"snapmaker-u1-e{extruder_index}",
                    "tag_uid": str(values.get("CARD_UID") or values.get("card_uid") or ""),
                    "vendor": task_metadata.get("vendor") or metadata["vendor"],
                    "filament_detected": detected,
                    "loaded_to_feeder": detected,
                    "loaded_to_extruder": loaded_to_extruder,
                    "module": module_name.removeprefix("filament_feed "),
                    "channel_state": values.get("channel_state"),
                    "channel_action_state": values.get("channel_action_state"),
                    "channel_error": values.get("channel_error"),
                    "filament_source": task_metadata.get("source") or "unknown",
                    "filament_official": task_metadata.get("official"),
                    "filament_edit": task_metadata.get("edited"),
                    "filament_sku": task_metadata.get("sku"),
                    "filament_exist": task_metadata.get("exists"),
                    "raw": values,
                }

        for name, values in status.items():
            if not isinstance(values, dict) or name in {"filament_feed left", "filament_feed right"}:
                continue
            lowered = name.lower()
            if not any(token in lowered for token in ("snapmaker", "spool", "rfid", "nfc")):
                continue
            metadata = _snapmaker_u1_filament_metadata(values)
            extruder_index = _safe_int(
                _snapmaker_first_value(values, ("extruder", "extruder_index", "tool", "tool_index", "slot", "tray"))
            )
            if extruder_index is None:
                continue
            slot = feed_slots.setdefault(
                extruder_index,
                {
                    "id": extruder_index,
                    "slot": f"U1-E{extruder_index}",
                    "active": active_index is not None and extruder_index == active_index,
                    "state": 11,
                    "tray_uuid": f"snapmaker-u1-e{extruder_index}",
                    "tag_uid": "",
                },
            )
            if metadata["material"]:
                slot["tray_type"] = metadata["material"]
            if metadata["subtype"]:
                slot["tray_sub_brands"] = metadata["subtype"]
            if metadata["color"]:
                slot["tray_color"] = metadata["color"]
            if metadata["remain"] is not None:
                slot["remain"] = metadata["remain"]
            if metadata["remaining_weight"] is not None:
                slot["remaining_weight"] = metadata["remaining_weight"]
            if metadata["weight"] is not None:
                slot["weight"] = metadata["weight"]
            if metadata["vendor"]:
                slot["vendor"] = metadata["vendor"]
            slot["raw_spool"] = values

        if feed_slots:
            trays = [feed_slots[index] for index in sorted(feed_slots)]
            active_slots = [tray["slot"] for tray in trays if tray.get("active")]
            loaded_to_extruder_slots = [tray["slot"] for tray in trays if tray.get("loaded_to_extruder")]
            loaded_to_feeder_slots = [tray["slot"] for tray in trays if tray.get("loaded_to_feeder")]
            self.state.tray_now = active_index if active_index is not None else 255
            self.state.raw_data["ams"] = [
                {
                    "id": 0,
                    "name": "Snapmaker U1 Feeders",
                    "tray": trays,
                    "module_type": "snapmaker_u1",
                }
            ]
            self.state.raw_data["snapmaker_u1"] = {
                "type": "snapmaker_u1",
                "active_extruder": active_index,
                "active_slots": active_slots,
                "loaded_to_feeder_slots": loaded_to_feeder_slots,
                "loaded_to_extruder_slots": loaded_to_extruder_slots,
                "print_task_config": task_config,
                "feed_modules": {
                    key: status.get(key)
                    for key in ("filament_feed left", "filament_feed right")
                    if isinstance(status.get(key), dict)
                },
            }

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = httpx.post(
            urljoin(self.base_url, path.lstrip("/")), json=payload, headers=self._headers, timeout=self.timeout
        )
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("result"), dict):
            return data["result"]
        return data if isinstance(data, dict) else {"value": data}

    def list_files(self, path: str = "/", *, storage: str | None = None) -> list[dict[str, Any]]:
        roots = [storage] if storage else ["gcodes", "local", "sdcard"]
        normalized = self._normalize_gcodes_path(path)
        last_error: Exception | None = None
        saw_successful_root = False
        for root_candidate in roots:
            if not root_candidate:
                continue
            root = str(root_candidate).strip().strip("/") or "gcodes"
            query = f"server/files/list?root={quote(root)}"
            if normalized:
                query += f"&path={quote(normalized)}"
            try:
                result = self._get(query)
            except Exception as exc:  # noqa: BLE001 - try alternate Moonraker roots below
                last_error = exc
                continue
            entries = result.get("result", result) if isinstance(result, dict) else result
            saw_successful_root = True
            if isinstance(entries, dict):
                entries = entries.get("files") or entries.get("children") or []
            files = self._normalize_file_entries(entries, normalized)
            if files or storage:
                return files
        if last_error and not saw_successful_root:
            raise last_error
        return []

    def upload_file(
        self, local_path: Path, remote_path: str, *, overwrite: bool = False, storage: str | None = None
    ) -> bool:  # noqa: ARG002
        root = str(storage or "gcodes").strip().strip("/") or "gcodes"
        target = self._normalize_gcodes_path(remote_path) or local_path.name
        with open(local_path, "rb") as fh:
            response = httpx.post(
                urljoin(self.base_url, "server/files/upload"),
                data={"root": root, "path": target.rsplit("/", 1)[0] if "/" in target else ""},
                files={"file": (target.rsplit("/", 1)[-1], fh, "application/octet-stream")},
                headers=self._headers,
                timeout=max(self.timeout, 60.0),
            )
        response.raise_for_status()
        return True

    def connect(self) -> None:
        last_exc: Exception | None = None
        for candidate in self.base_url_candidates:
            try:
                info = self._get("server/info", base_url=candidate)
                self.base_url = candidate
                break
            except Exception as exc:  # noqa: BLE001 - retry with fallback candidate, then re-raise final cause
                last_exc = exc
        else:
            assert last_exc is not None
            raise last_exc
        self.state.connected = True
        self.state.state = _map_moonraker_state(info.get("klippy_state") or info.get("state") or "connected")
        self.state.raw_status = info
        self.state.raw_data = info
        try:
            self.request_status_update()
        except Exception:
            # ``server/info`` proved Moonraker is reachable. A transient object
            # query failure should not make the saved printer appear offline;
            # the next status poll will retry and update the details.
            self.state.connected = True
        # Printers added without the UI discovery step have no camera persisted;
        # get_effective_camera_source() falls back to these.
        try:
            self.webcams = self.discover_webcams()
        except Exception:  # noqa: BLE001 - webcams are optional
            self.webcams = []

    def disconnect(self, timeout: float = 0) -> None:  # noqa: ARG002 - kept for Bambu client compatibility
        self.state.connected = False

    def check_staleness(self) -> bool:
        try:
            self.request_status_update()
        except Exception:
            self.state.connected = False
        return self.state.connected

    def _build_lifecycle_payload(self) -> dict[str, Any]:
        filename = self.state.subtask_name or self.state.current_print or self.state.gcode_file or "Unknown"
        return {
            "filename": filename,
            "subtask_name": filename,
            "progress": self.state.progress,
            "remaining_time": self.state.remaining_time * 60 if self.state.remaining_time else None,
            "status": self.state.state,
            "raw_data": self.state.raw_data,
        }

    def _emit_status_callbacks(self, previous_state: str | None) -> None:
        if self.on_state_change:
            self.on_state_change(self.state)

        bed_temp = self.state.temperatures.get("bed") if self.state.temperatures else None
        if isinstance(bed_temp, (int, float)) and bed_temp != self._last_bed_temp:
            self._last_bed_temp = float(bed_temp)
            if self.on_bed_temp_update:
                self.on_bed_temp_update(float(bed_temp))

        current_state = self.state.state
        previous_running = previous_state in {"RUNNING", "PAUSE"}
        current_running = current_state in {"RUNNING", "PAUSE"}

        # Do not fire a synthetic start on the very first poll. If Printbuddy
        # starts while a printer is already running, the restart-recovery path
        # owns that case; this edge detector is for new Moonraker transitions.
        if previous_state is not None and not previous_running and current_running:
            self._current_print_started_at = time.monotonic()
            if self.on_print_start:
                self.on_print_start(self._build_lifecycle_payload())
        elif previous_running and not current_running:
            payload = self._build_lifecycle_payload()
            if self._current_print_started_at is not None:
                payload["actual_time_seconds"] = max(1, int(time.monotonic() - self._current_print_started_at))
                self._current_print_started_at = None
            if current_state == "FAILED":
                payload["status"] = "failed"
            elif current_state == "FINISH" or (current_state == "IDLE" and self.state.progress >= 99):
                payload["status"] = "completed"
            elif current_state == "IDLE":
                # fork: after a Klipper crash or restart the print reads "standby", as if stopped by hand:
                # Moonraker's history tells a failure (klippy_disconnect, error...) from a cancel.
                payload["status"] = self._ended_job_status() or "stopped"
            else:
                payload["status"] = "stopped"
            if self.on_print_complete:
                self.on_print_complete(payload)

    def request_status_update(self) -> bool:
        previous_state = self._last_state if self._has_status_sample else None
        status = self._query_objects(
            ["webhooks", "print_stats", "virtual_sdcard", "display_status", "extruder", "heater_bed", "gcode_move"]
        )
        try:
            available_objects = self._available_objects()
        except Exception:  # noqa: BLE001 - optional object discovery must not break core status polling
            available_objects = []
        fan_status = self._query_fan_status(available_objects)
        if fan_status:
            status.update(fan_status)
        light_object = _moonraker_light_object(available_objects)
        if light_object:
            try:
                status.update(self._query_objects([light_object]))
            except Exception:  # noqa: BLE001 - the light is optional
                pass
            self.state.chamber_light = _moonraker_light_is_on(status.get(light_object))
        if "exclude_object" in available_objects:
            try:
                status.update(self._query_objects(["exclude_object"]))
            except Exception:  # noqa: BLE001 - skipping objects is optional
                pass
            _apply_moonraker_exclude_object(self.state, status.get("exclude_object"))
        cfs_status = self._query_cfs_status(available_objects)
        if cfs_status:
            status.update(cfs_status)
        snapmaker_u1_status = self._query_snapmaker_u1_status(available_objects)
        if snapmaker_u1_status:
            status.update(snapmaker_u1_status)
        self.state.connected = True
        self.state.raw_status = status
        self.state.raw_data = status
        self._apply_creality_cfs_box(status)

        print_stats = status.get("print_stats", {}) if isinstance(status, dict) else {}
        virtual_sdcard = status.get("virtual_sdcard", {}) if isinstance(status, dict) else {}
        display_status = status.get("display_status", {}) if isinstance(status, dict) else {}

        raw_state = print_stats.get("state") or self.state.state
        self.state.state = _map_moonraker_state(raw_state)
        reported_file = print_stats.get("filename") or virtual_sdcard.get("file_path")
        if reported_file:
            self.state.gcode_file = str(reported_file)
            self.state.current_print = self.state.gcode_file
            self.state.subtask_name = self.state.gcode_file

        print_info = print_stats.get("info")
        if isinstance(print_info, dict):
            current_layer = _safe_int(print_info.get("current_layer"))
            total_layers = _safe_int(print_info.get("total_layer") or print_info.get("total_layers"))
            if current_layer is not None:
                self.state.layer_num = current_layer
            if total_layers is not None:
                self.state.total_layers = total_layers

        raw_progress = _moonraker_progress_percent(virtual_sdcard, display_status)
        if self.state.state == "FINISH":
            self.state.progress = 100.0
            self.state.remaining_time = 0
        elif self.state.state in {"IDLE", "FAILED"}:
            self.state.progress = 0.0
            self.state.remaining_time = 0
        else:
            self.state.progress = raw_progress
            # fork: a G-code without SET_PRINT_STATS_INFO (Orca's K2 profile) reports no layers, and early in
            # a print duration/progress gives no time left: the file's metadata fills both.
            meta = self._print_file_metadata(self.state.gcode_file) if reported_file else {}
            if not self.state.total_layers and _safe_int(meta.get("layer_count")):
                self.state.total_layers = _safe_int(meta.get("layer_count"))
            estimate = print_stats.get("estimated_time") or meta.get("estimated_time")
            self.state.remaining_time = _remaining_minutes(
                {**print_stats, "estimated_time": estimate}, self.state.progress
            )
        self.state.temperatures = _moonraker_temperatures(status)
        speed_level = _moonraker_speed_level(status.get("gcode_move"))
        if speed_level is not None:
            self.state.speed_level = speed_level
        self._apply_snapmaker_u1_status(status)
        _apply_moonraker_fans(self.state, status)
        self._emit_status_callbacks(previous_state)
        self._last_state = self.state.state
        self._has_status_sample = True
        return True

    def download_file(self, remote_path: str) -> bytes | None:
        normalized = self._normalize_gcodes_path(remote_path)
        response = httpx.get(
            urljoin(self.base_url, f"server/files/gcodes/{normalized}"),
            headers=self._headers,
            timeout=max(self.timeout, 60.0),
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def get_thumbnail(self, remote_path: str, max_size: int = 300) -> bytes | None:
        """Return the largest slicer thumbnail (<= max_size px) Moonraker extracted for a gcode file."""
        normalized = self._normalize_gcodes_path(remote_path)
        metadata = self._get(f"server/files/metadata?filename={quote(normalized)}")
        thumbnails = [
            thumb
            for thumb in metadata.get("thumbnails") or []
            if isinstance(thumb, dict)
            and thumb.get("relative_path")
            and (_safe_int(thumb.get("width")) or 0) <= max_size
        ]
        if not thumbnails:
            return None
        best = max(thumbnails, key=lambda thumb: _safe_int(thumb.get("width")) or 0)
        # relative_path is relative to the gcode file's own directory.
        directory = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
        thumb_path = f"{directory}/{best['relative_path']}" if directory else str(best["relative_path"])
        response = httpx.get(
            urljoin(self.base_url, f"server/files/gcodes/{quote(thumb_path)}"),
            headers=self._headers,
            timeout=self.timeout,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def delete_file(self, remote_path: str, *, storage: str | None = None) -> bool:
        root = str(storage or "gcodes").strip().strip("/") or "gcodes"
        normalized = self._normalize_gcodes_path(remote_path)
        self._post("server/files/delete_file", {"path": f"{root}/{normalized}"})
        return True

    def send_gcode(self, script: str) -> bool:
        if not script.strip():
            return False
        try:
            self._post("printer/gcode/script", {"script": script})
        except httpx.ReadTimeout:
            # K2 Plus / Creality Moonraker can execute long-running commands such
            # as G28 and still miss the short HTTP response window. Refresh status
            # once; if Moonraker is reachable afterwards, treat the command as
            # accepted rather than surfacing a false UI failure.
            self.request_status_update()
        return True

    def set_nozzle_temperature(self, target: int | float) -> bool:
        return self.send_gcode(f"M104 S{int(target)}")

    def set_bed_temperature(self, target: int | float) -> bool:
        return self.send_gcode(f"M140 S{int(target)}")

    def set_fan_speed(self, fan: str, speed: int) -> bool:
        normalized_fan = fan.strip().lower().replace("-", "_")
        clamped_speed = max(0, min(100, int(speed)))
        target_object = self._target_fan_object(normalized_fan)
        if target_object is None:
            raise ValueError(f"Fan '{fan}' is not available for this Moonraker printer")
        if target_object == "fan":
            return self.send_gcode(f"M106 S{round(clamped_speed * 255 / 100)}")
        if target_object.startswith("fan_generic "):
            klipper_fan_name = target_object.removeprefix("fan_generic ")
            return self.send_gcode(f"SET_FAN_SPEED FAN={klipper_fan_name} SPEED={clamped_speed / 100:.2f}")
        raise ValueError(f"Fan '{fan}' is not controllable through Moonraker")

    def set_chamber_light(self, on: bool) -> bool:
        light_object = _moonraker_light_object(self._available_objects())
        if light_object is None:
            return False
        kind, _, name = light_object.partition(" ")
        value = 1 if on else 0
        if kind == "output_pin":
            script = f"SET_PIN PIN={name} VALUE={value}"
        else:
            script = f"SET_LED LED={name} RED={value} GREEN={value} BLUE={value} WHITE={value}"
        if not self.send_gcode(script):
            return False
        self.state.chamber_light = bool(on)
        return True

    def set_print_speed(self, mode: int) -> bool:
        percent = _SPEED_MODE_PERCENT.get(int(mode))
        if percent is None or not self.send_gcode(f"M220 S{percent}"):
            return False
        self.state.speed_level = int(mode)
        return True

    def skip_objects(self, object_ids: list[int]) -> bool:
        names = [self.state.printable_objects[oid]["name"] for oid in object_ids if oid in self.state.printable_objects]
        if not names or not self.send_gcode("\n".join(f"EXCLUDE_OBJECT NAME={name}" for name in names)):
            return False
        self.state.skipped_objects.extend(oid for oid in object_ids if oid not in self.state.skipped_objects)
        return True

    def home_axes(self, axes: list[str] | None = None) -> bool:
        script = ("G28 " + " ".join(a.upper() for a in axes)) if axes else "G28"
        return self.send_gcode(script)

    def extrude(self, length: float, speed: int = 300) -> bool:
        return self.send_gcode(f"M83\nG1 E{length:.2f} F{speed}\nM82")

    def ams_load_filament(self, tray_id: int, extruder_id: int | None = None) -> bool:  # noqa: ARG002
        """Load/select a Creality CFS slot using the hardware-verified M8200 path."""
        if _creality_cfs_slot_name(tray_id) is None or self._is_k2_openhost_cfs():
            return False
        return self.send_gcode(f"M8200 P\nM8200 L I={int(tray_id)}\nM8200 O")

    def ams_unload_filament(self, tray_id: int | None = None) -> bool:
        """Unload the currently loaded Creality CFS filament using M8200."""
        if self._is_k2_openhost_cfs():
            return False
        if tray_id is not None and _creality_cfs_slot_name(int(tray_id)) is None:
            return False
        return self.send_gcode("M8200 P\nM8200 C\nM8200 R\nM8200 O")

    def ams_refresh_tray(self, ams_id: int, slot_id: int) -> tuple[bool, str]:  # noqa: ARG002
        """CFS RFID refresh is disabled after real K2 firmware crash reports."""
        return False, "CFS RFID refresh is disabled for Creality K2 printers"

    def adjust_z_offset(self, amount: float) -> bool:
        return self.send_gcode(f"SET_GCODE_OFFSET Z_ADJUST={amount} MOVE=1")

    def save_config(self) -> bool:
        return self.send_gcode("SAVE_CONFIG")

    def start_print(self, filename: str, plate_id: int = 1, **kwargs: Any) -> bool:  # noqa: ARG002
        normalized = self._normalize_gcodes_path(filename)
        if not self._prepare_creality_cfs_slot_for_print(kwargs.get("ams_mapping")):
            return False
        try:
            self._post("printer/print/start", {"filename": normalized})
        except httpx.ReadTimeout:
            # Some Moonraker/Elegoo stacks accept the start command and begin
            # printing, but do not return headers before the short control
            # timeout expires. Verify printer state before surfacing a false
            # background-dispatch failure.
            try:
                self.request_status_update()
            except Exception:
                raise
            current = self.state.current_print or self.state.gcode_file or ""
            if self.state.state == "RUNNING" or current.endswith(normalized):
                return True
            raise
        return True

    def stop_print(self) -> bool:
        self._post("printer/print/cancel", {})
        return True

    def pause_print(self) -> bool:
        self._post("printer/print/pause", {})
        return True

    def resume_print(self) -> bool:
        self._post("printer/print/resume", {})
        return True


def create_moonraker_client(printer: Any, **callbacks: Any) -> MoonrakerPrinterClient:  # noqa: ARG001
    base_url = printer.api_url or f"http://{printer.ip_address}:7125"
    return MoonrakerPrinterClient(
        base_url=base_url,
        auth_token=getattr(printer, "auth_token", None),
        printer_model=getattr(printer, "model", None),
        on_state_change=callbacks.get("on_state_change"),
        on_print_start=callbacks.get("on_print_start"),
        on_print_complete=callbacks.get("on_print_complete"),
        on_bed_temp_update=callbacks.get("on_bed_temp_update"),
    )
