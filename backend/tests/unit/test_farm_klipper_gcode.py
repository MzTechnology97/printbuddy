"""Printfarm (fork): the preview and layer info added to the G-code sent to Klipper printers. Data is made up."""

import base64
import io
import re

from PIL import Image

from backend.app.farm.jobs.service import klipper_gcode_name
from backend.app.farm.klipper_gcode import for_klipper, with_layer_info, with_thumbnails

GCODE = (
    b"; HEADER_BLOCK_START\n; total layer number: 2\n; HEADER_BLOCK_END\n\nG28\n"
    b";LAYER_CHANGE\n;Z:0.2\nG1 X1\n;LAYER_CHANGE\n;Z:0.4\nG1 X2\n"
    b"; CONFIG_BLOCK_START\n; thumbnails = 96x96/PNG, 300x300/PNG\n; CONFIG_BLOCK_END\n"
)
HEADER_END = GCODE.index(b"; HEADER_BLOCK_END\n") + len(b"; HEADER_BLOCK_END\n")


def _png(w=512, h=384) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (w, h), (247, 84, 3, 255)).save(buf, "PNG")
    return buf.getvalue()


def _thumbnails(gcode: bytes) -> dict[str, Image.Image]:
    out = {}
    for size, length, body in re.findall(rb"; thumbnail begin (\d+x\d+) (\d+)\n(.*?); thumbnail end", gcode, re.S):
        lines = body.splitlines()
        assert all(line.startswith(b"; ") and len(line) <= 78 for line in lines)
        data = b"".join(line[2:] for line in lines)
        assert len(data) == int(length)
        out[size.decode()] = Image.open(io.BytesIO(base64.b64decode(data)))
    return out


def test_thumbnails_from_the_plate_picture_in_the_profile_sizes():
    out = with_thumbnails(GCODE, _png())
    assert {k: v.size for k, v in _thumbnails(out).items()} == {"96x96": (96, 96), "300x300": (300, 300)}
    # Right after the header, where Moonraker reads them; the rest of the G-code is the same.
    assert out[:HEADER_END] == GCODE[:HEADER_END] and out.endswith(GCODE[HEADER_END:])
    assert out[HEADER_END:].startswith(b"\n; thumbnail begin 96x96 ")


def test_gcode_with_thumbnails_or_without_a_picture_is_left_alone():
    once = with_thumbnails(GCODE, _png())
    assert with_thumbnails(once, _png()) == once
    assert with_thumbnails(GCODE, None) == GCODE


def test_layer_info_at_each_layer_change_once():
    out = with_layer_info(GCODE)
    assert b"SET_PRINT_STATS_INFO TOTAL_LAYER=2\n;LAYER_CHANGE\nSET_PRINT_STATS_INFO CURRENT_LAYER=1\n;Z:0.2" in out
    assert b";LAYER_CHANGE\nSET_PRINT_STATS_INFO CURRENT_LAYER=2\n;Z:0.4" in out
    assert with_layer_info(out) == out
    assert for_klipper(b"G28\n", None) == b"G28\n"


def test_readable_gcode_names():
    assert klipper_gcode_name("K2-Citroen AMI - plate 2.gcode.3mf", 2) == "K2-Citroen-AMI-plate-2.gcode"
    assert klipper_gcode_name("Maniglia - plate 12 - Nero.gcode.3mf", 12) == "Maniglia-plate-12-Nero.gcode"
    assert klipper_gcode_name("Sopra.gcode.3mf", 1) == "Sopra-plate-1.gcode"
    assert klipper_gcode_name("Sopra plate 12.gcode.3mf", 1) == "Sopra-plate-12-plate-1.gcode"
