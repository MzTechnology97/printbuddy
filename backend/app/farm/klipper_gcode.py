"""G-code for Klipper printers out of a sliced .gcode.3mf: the preview and layers Moonraker reads (fork).

The plate's G-code inside a .gcode.3mf has no thumbnail blocks (the slicer keeps the picture apart,
as ``Metadata/plate_N.png``) and, with an empty layer-change G-code, no ``SET_PRINT_STATS_INFO``:
Mainsail, Fluidd and the printer's screen show no preview and no layers. Both are added when the
G-code has none; nothing else in it changes.

Printing a .gcode.3mf on any non-Bambu printer outside the farm (library print, archive reprint,
queue item) sends the plate's G-code the same way: ``plain_gcode_name`` and ``plain_gcode``.
"""

from __future__ import annotations

import asyncio
import base64
import io
import re
import tempfile
import zipfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from backend.app.services.printer_providers.factory import MOONRAKER_PROVIDERS

THUMBNAIL_SIZES = ((96, 96), (300, 300))  # the K2 profile's own; used when the G-code doesn't say
LINE = 76  # base64 characters per comment line
_SIZES_RE = re.compile(rb"^; thumbnails = (.+)$", re.M)
_LAYER = b"\n;LAYER_CHANGE\n"


def _sizes(gcode: bytes) -> list[tuple[int, int]]:
    """The thumbnail sizes the printer profile asks for (``; thumbnails = 96x96/PNG, 300x300/PNG``)."""
    m = _SIZES_RE.search(gcode, max(0, len(gcode) - 400_000))  # the settings block is at the end
    sizes = [(int(w), int(h)) for w, h in re.findall(rb"(\d+)x(\d+)", m.group(1))] if m else []
    return [s for s in sizes if 0 < s[0] <= 1000 and 0 < s[1] <= 1000] or list(THUMBNAIL_SIZES)


def thumbnail_blocks(png: bytes, sizes: list[tuple[int, int]]) -> bytes:
    """``; thumbnail begin WxH LEN`` … ``; thumbnail end`` for each size, the picture fitted and centred."""
    from PIL import Image

    picture = Image.open(io.BytesIO(png)).convert("RGBA")
    lines = []
    for w, h in sizes:
        fitted = picture.copy()
        fitted.thumbnail((w, h))
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(fitted, ((w - fitted.width) // 2, (h - fitted.height) // 2))
        buf = io.BytesIO()
        canvas.save(buf, "PNG")
        data = base64.b64encode(buf.getvalue()).decode()
        lines += [f"; thumbnail begin {w}x{h} {len(data)}"]
        lines += [f"; {data[i : i + LINE]}" for i in range(0, len(data), LINE)]
        lines += ["; thumbnail end", ";"]
    return ("\n".join(lines) + "\n").encode()


def with_thumbnails(gcode: bytes, png: bytes | None) -> bytes:
    """The plate's picture as thumbnail blocks right after the header, when the G-code has none."""
    if not png or b"; thumbnail begin" in gcode[:2_000_000]:
        return gcode
    end = gcode.find(b"; HEADER_BLOCK_END\n", 0, 8192)
    at = end + len(b"; HEADER_BLOCK_END\n") if end >= 0 else 0
    return gcode[:at] + b"\n" + thumbnail_blocks(png, _sizes(gcode)) + gcode[at:]


def with_layer_info(gcode: bytes) -> bytes:
    """``SET_PRINT_STATS_INFO`` at each ``;LAYER_CHANGE``, as Klipper's own profiles do, when there is none."""
    if b"SET_PRINT_STATS_INFO" in gcode:
        return gcode
    parts = gcode.split(_LAYER)
    if len(parts) < 2:
        return gcode
    total = len(parts) - 1
    out = [parts[0], b"\nSET_PRINT_STATS_INFO TOTAL_LAYER=%d" % total]
    for layer, part in enumerate(parts[1:], start=1):
        out += [_LAYER, b"SET_PRINT_STATS_INFO CURRENT_LAYER=%d\n" % layer, part]
    return b"".join(out)


def for_klipper(gcode: bytes, png: bytes | None) -> bytes:
    return with_layer_info(with_thumbnails(gcode, png))


_PLATE_GCODE = re.compile(r"Metadata/plate_(\d+)\.gcode")


class NoPlateGcode(RuntimeError):
    """A 3MF sent to a G-code printer has no G-code for the plate."""


def read_plate(path: Path, plate: int) -> tuple[bytes, bytes | None]:
    """The plate's G-code and picture in a sliced 3MF (raises KeyError, OSError, BadZipFile)."""
    with zipfile.ZipFile(path) as zf:
        png = f"Metadata/plate_{plate}.png"
        return zf.read(f"Metadata/plate_{plate}.gcode"), zf.read(png) if png in zf.namelist() else None


def plain_gcode_name(path: Path, filename: str, provider: str, plate_id: int | None) -> tuple[str, int] | None:
    """For a 3MF sent to a non-Bambu printer: the ``.gcode`` name to send it as and the plate it holds.

    ``<name>.gcode``, or ``<name>_plate_<N>.gcode`` when the file has several sliced plates; no plate asked
    means the lowest sliced one. None when the file goes as it is (Bambu, plain G-code). Raises
    ``NoPlateGcode`` when the 3MF has no G-code for the plate.
    """
    if provider == "bambu" or not filename.lower().endswith(".3mf"):
        return None
    try:
        with zipfile.ZipFile(path) as zf:
            plates = sorted(int(m[1]) for n in zf.namelist() if (m := _PLATE_GCODE.fullmatch(n)))
    except (OSError, zipfile.BadZipFile) as exc:
        raise NoPlateGcode(f"{filename} is not a readable 3MF file") from exc
    if not plates:
        raise NoPlateGcode(f"{filename} contains no sliced G-code. Slice it for this printer before printing.")
    plate = plate_id or plates[0]
    if plate not in plates:
        sliced = ", ".join(map(str, plates))
        raise NoPlateGcode(f"Plate {plate} of {filename} is not sliced (sliced plates: {sliced}).")
    base = re.sub(r"(\.gcode)?\.3mf$", "", filename, flags=re.IGNORECASE)
    return (f"{base}_plate_{plate}.gcode" if len(plates) > 1 else f"{base}.gcode"), plate


def _write_plain_gcode(path: Path, plate: int, provider: str, out: Path) -> None:
    gcode, png = read_plate(path, plate)
    # SET_PRINT_STATS_INFO is a Klipper command: other firmwares get only the preview (comment lines).
    out.write_bytes(for_klipper(gcode, png) if provider in MOONRAKER_PROVIDERS else with_thumbnails(gcode, png))


@asynccontextmanager
async def plain_gcode(path: Path, plan: tuple[str, int] | None, provider: str) -> AsyncIterator[Path]:
    """The file to upload: ``path`` itself, or (with a ``plain_gcode_name`` plan) the plate's G-code in a
    temporary file removed afterwards. The stored file is never changed."""
    if plan is None:
        yield path
        return
    name, plate = plan
    with tempfile.TemporaryDirectory(prefix="printbuddy-gcode-", ignore_cleanup_errors=True) as tmp:
        out = Path(tmp) / Path(name).name
        await asyncio.to_thread(_write_plain_gcode, path, plate, provider, out)
        yield out
