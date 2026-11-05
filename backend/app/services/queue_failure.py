"""Fork: why a queue item failed or was stopped, kept on the item for the queue history.

``error_message`` is the reason a person reads, never empty for a failed item; ``failure_detail``
says where it happened and what the printer or the upload reported:

    {"phase": "dispatch" | "upload" | "start" | "printing",
     "code": "unknown" | "printer_error" | "cancelled_on_printer" | ...,
     "progress": 42.0, "layer": 120, "total_layers": 300,     # printing, when known
     "by": "name", "via": "panel" | "api",                    # stopped or cancelled by someone
     "technical": ["FTP 552 ...", "print_stats.state=error", ...]}
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
from datetime import datetime, timezone

UNKNOWN_REASON = "Reason not reported by the printer"
logger = logging.getLogger(__name__)
_TECHNICAL_MAX = 20  # lines kept from an upload's log

_captured: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("queue_failure_log", default=None)


class _CaptureHandler(logging.Handler):
    """Keeps the warnings and errors logged while an upload runs, in that upload's context only."""

    def emit(self, record: logging.LogRecord) -> None:
        lines = _captured.get()
        if lines is not None and len(lines) < _TECHNICAL_MAX:
            with contextlib.suppress(Exception):
                lines.append(f"{record.levelname}: {record.getMessage()}"[:500])


logging.getLogger("backend.app").addHandler(_CaptureHandler(level=logging.WARNING))


@contextlib.contextmanager
def capture_logged_errors():
    """The warnings and errors the code below logs (FTP codes, HTTP errors...), as the technical details.

    Threads see them when they run in this context (``asyncio.to_thread``, or ``copy_context().run``).
    """
    lines: list[str] = []
    token = _captured.set(lines)
    try:
        yield lines
    finally:
        _captured.reset(token)


def mark_failed(item, reason: str | None, phase: str, **detail) -> None:
    """The item failed: a readable reason (never empty), the phase and what is known about it."""
    item.status = "failed"
    item.error_message = reason or UNKNOWN_REASON
    item.completed_at = datetime.now(timezone.utc)
    item.failure_detail = {
        "phase": phase,
        **({} if reason else {"code": "unknown"}),
        **{k: v for k, v in detail.items() if v not in (None, [], {})},
    }
    from backend.app.farm import job_events  # fork: the queue page's timeline

    job_events.record(
        {"upload": "upload_failed", "start": "start_failed", "printing": "failed"}.get(phase, "dispatch_failed"),
        item.error_message,
        queue_item_id=getattr(item, "id", None),
        printer_id=getattr(item, "printer_id", None),
        archive_id=getattr(item, "archive_id", None),
        detail=item.failure_detail,
    )


def requester(user, request) -> dict:
    """Who stopped or cancelled an item, and from where: a signed-in user (the panel) or an API key (Seller Hub, MCP)."""
    headers = request.headers if request is not None else {}
    if headers.get("x-api-key") or (headers.get("authorization") or "").lower().startswith("bearer bb_"):
        return {"by": getattr(user, "username", None) or "API key", "via": "api"}
    return {"by": getattr(user, "username", None), "via": "panel"}


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bambu(data: dict, status: str) -> tuple[str | None, dict]:
    from backend.app.services.hms_errors import get_error_description

    technical, reasons = [], []
    for err in data.get("hms_errors") or []:  # print_error is among them too (bambu_mqtt)
        try:
            module = (int(err.get("attr", 0)) >> 16) & 0xFFFF
            short_code = f"{module:04X}_{int(str(err.get('code', '')).replace('0x', '') or 0, 16):04X}"
        except (TypeError, ValueError):
            continue
        description = get_error_description(short_code)
        reasons.append(f"[{short_code}] {description}" if description else f"[{short_code}]")
        technical.append(f"HMS {short_code} attr={err.get('attr')} severity={err.get('severity')}")
    print_error = (data.get("raw_data") or {}).get("print_error")
    if isinstance(print_error, int) and print_error:
        technical.append(f"print_error={print_error} (0x{print_error:08X})")
    detail = {
        "progress": _number(data.get("last_progress")),
        "layer": data.get("last_layer_num"),
        "technical": technical,
    }
    if status == "cancelled":
        return "Stopped on the printer", {"code": "cancelled_on_printer", **detail}
    return "; ".join(reasons) or None, {"code": "printer_error" if reasons else "unknown", **detail}


def _klipper(data: dict, status: str) -> tuple[str | None, dict]:
    raw = data.get("raw_data") or {}
    stats = raw.get("print_stats") or {}
    hooks = raw.get("webhooks") or {}
    info = stats.get("info") if isinstance(stats.get("info"), dict) else {}
    progress = _number((raw.get("virtual_sdcard") or {}).get("progress"))
    message = (stats.get("message") or "").strip()
    detail = {
        "progress": round(progress * 100, 1) if progress else None,  # print_stats resets it, the SD card keeps it
        "layer": info.get("current_layer"),
        "total_layers": info.get("total_layer"),
        "technical": [
            f"print_stats.state={stats.get('state')}",
            *([f"print_stats.message={message}"] if message else []),
            f"webhooks.state={hooks.get('state')}",
            *([f"webhooks.state_message={hooks['state_message']}"] if hooks.get("state_message") else []),
        ],
    }
    if hooks.get("state") in ("shutdown", "error"):  # Klipper itself stopped: its message says why
        why = (hooks.get("state_message") or message or "").strip()
        return f"Klipper {hooks['state']}: {why}" if why else f"Klipper {hooks['state']}", {
            "code": "printer_error",
            **detail,
        }
    if stats.get("state") == "cancelled" or status == "cancelled":
        return "Cancelled on the printer", {"code": "cancelled_on_printer", **detail}
    if message:
        return f"Printer error: {message}", {"code": "printer_error", **detail}
    return None, {"code": "unknown", **detail}


def printer_failure(status: str, data: dict) -> tuple[str, dict]:
    """The reason and details of a print the printer ended as failed or cancelled (``status`` normalised).

    ``data`` is ``on_print_complete``'s: Moonraker's carries its status objects in ``raw_data``.
    """
    klipper = isinstance(data.get("raw_data"), dict) and "print_stats" in data["raw_data"]
    reason, detail = (_klipper if klipper else _bambu)(data, status)
    return reason or UNKNOWN_REASON, {
        "phase": "printing",
        **{k: v for k, v in detail.items() if v not in (None, [], {})},
    }


def safe_printer_failure(status: str, data: dict) -> tuple[str, dict]:
    """``printer_failure`` that never raises: a payload of an unexpected shape gives the unknown reason.

    ``on_print_complete`` calls it before closing the queue item and the archive: a reason that can't be
    read must never leave them "printing".
    """
    try:
        return printer_failure(status, data)
    except Exception:  # best effort: any odd field in the printer's payload
        logger.debug("Failure reason of a %s print not readable from %r", status, data, exc_info=True)
        return UNKNOWN_REASON, {"phase": "printing", "code": "unknown"}
