"""A plate's own settings in an edited copy (fork): OrcaSlicer's Plate Settings dialog and tab.

OrcaSlicer keeps them in ``Metadata/model_settings.config`` as ``<metadata key value>`` lines of the ``<plate>``
whose ``plater_id`` they belong to, written in this order after ``plater_id``, ``plater_name`` and ``locked``
(bbs_3mf.cpp ``_add_model_config_file_to_archive``): ``bed_type``, ``print_sequence``,
``first_layer_print_sequence``, ``other_layers_print_sequence`` with ``other_layers_print_sequence_nums``, and
``spiral_mode``. A key there is the plate's own; without it the plate slices with the project's value
(PlateSettingsDialog's "Same as Global"). The name is always written, empty when the plate has none.

A copy sets, changes or removes such lines and nothing else, as the objects' settings do (object_settings.py):
every other byte stays, and the result is parsed back and compared with the original but for the edited keys.
"""

from __future__ import annotations

import re
from typing import Literal

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.workspace import settings as ws_settings
from backend.app.farm.workspace.object_settings import _escape, _line, _value_of
from backend.app.farm.workspace.placement import _KEY, _METADATA, _VALUE, _attr
from backend.app.utils.xml_prefixes import declared

MAX_EDITS = 200
NAME_MAX = 255
LAYER_MIN = 2  # PlateSettingsDialog MIN_LAYER_VALUE: the first layer has its own sequence
LAYER_END = 2**31 - 2  # MAX_LAYER_VALUE (INT_MAX - 1): the dialog's "End"
LAYER_MAX = 2**31 - 1  # INT_MAX: TabPrintPlate's first range ends there
# the plate's metadata in the order Orca writes it; the plate's own settings among them
ORDER = (
    "plater_id",
    "plater_name",
    "locked",
    "bed_type",
    "print_sequence",
    "first_layer_print_sequence",
    "other_layers_print_sequence",
    "other_layers_print_sequence_nums",
    "spiral_mode",
)
OWN = frozenset(ORDER) - {"plater_id"}
NUMS = "other_layers_print_sequence_nums"
_PLATE = re.compile(rb"<plate\b[^>]*>")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class InvalidPlateSetting(ValueError):
    """An edit the copy refuses (the message says which and why)."""


class PlateSetting(BaseModel):
    """One of a plate's own settings: its new value, or None to drop it (the project's value applies again).
    ``other_layers_print_sequence`` is each range's first and last layer and its filament order, one after the
    other (Orca's ``LayerPrintSequence`` list); its ``_nums`` follows from it."""

    plate: int = Field(ge=1, le=10000)
    key: Literal[
        "plater_name",
        "locked",
        "bed_type",
        "print_sequence",
        "first_layer_print_sequence",
        "other_layers_print_sequence",
        "spiral_mode",
    ]
    value: str | bool | None = None


def _order(numbers: list[str], filaments: int, what: str) -> list[int]:
    """A filament order: each of the project's filaments once."""
    try:
        order = [int(x) for x in numbers]
    except ValueError as exc:
        raise InvalidPlateSetting(f"{what}: {' '.join(numbers)!r} is not a filament order") from exc
    if sorted(order) != list(range(1, filaments + 1)):
        raise InvalidPlateSetting(f"{what}: expected each of the {filaments} filaments once")
    return order


def check(e: PlateSetting, cfg: dict, slicer: str) -> dict[str, str | None]:
    """The lines an edit writes (key -> value; None: removed), its value checked; ``InvalidPlateSetting`` when it
    can't be one."""
    if e.value is None:
        if e.key in ("plater_name", "locked"):
            raise InvalidPlateSetting(f"{e.key}: a plate always has it, it can't be removed")
        return {e.key: None, **({NUMS: None} if e.key == "other_layers_print_sequence" else {})}
    if e.key in ("spiral_mode", "locked"):
        if e.value not in (True, False, "true", "false"):
            raise InvalidPlateSetting(f"{e.key}: {e.value!r} is not true or false")
        return {e.key: "true" if e.value in (True, "true") else "false"}  # as Orca writes it (std::boolalpha)
    if isinstance(e.value, bool):
        raise InvalidPlateSetting(f"{e.key}: {e.value!r} is not a value it takes")
    text = e.value
    if e.key == "plater_name":
        if len(text) > NAME_MAX or _CONTROL.search(text):
            raise InvalidPlateSetting(f"plater_name: at most {NAME_MAX} characters, no control characters")
        return {e.key: text}
    if e.key in ("bed_type", "print_sequence"):
        option = ws_settings.options(slicer).get("curr_bed_type" if e.key == "bed_type" else e.key) or {}
        if text not in option.get("enum", []):
            raise InvalidPlateSetting(f"{e.key}: {text!r} is not one of {option.get('enum', [])}")
        return {e.key: text}
    filaments = len(cfg.get("filament_colour") or [None])
    numbers = text.split()
    if e.key == "first_layer_print_sequence":
        return {e.key: " ".join(map(str, _order(numbers, filaments, e.key)))}
    width = filaments + 2  # first layer, last layer, the filaments' order
    if not numbers or len(numbers) % width:
        raise InvalidPlateSetting(f"{e.key}: expected ranges of {width} numbers (first layer, last layer, the order)")
    out: list[int] = []
    for i in range(0, len(numbers), width):
        try:
            first, last = int(numbers[i]), int(numbers[i + 1])
        except ValueError as exc:
            raise InvalidPlateSetting(f"{e.key}: a range's layers are not numbers") from exc
        if not LAYER_MIN <= first <= last <= LAYER_MAX:
            raise InvalidPlateSetting(f"{e.key}: a range goes from layer {LAYER_MIN} up, and ends after it begins")
        out += [first, last, *_order(numbers[i + 2 : i + width], filaments, e.key)]
    return {e.key: " ".join(map(str, out)), NUMS: str(len(numbers) // width)}


def _plates(settings: bytes) -> tuple[list, dict[str, dict[str, str]]]:
    """``(everything but the plates' own settings, those settings by plater_id)``."""
    try:
        root = SafeET.fromstring(declared(settings))
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidPlateSetting(f"model_settings.config is not valid XML: {exc}") from exc
    rest: list = []
    own: dict[str, dict[str, str]] = {}
    for el in root:
        dump = lambda node: [(x.tag, tuple(sorted(x.attrib.items())), (x.text or "").strip()) for x in node.iter()]  # noqa: E731
        if el.tag != "plate":
            rest.append(dump(el))
            continue
        pid = next((m.get("value") for m in el if m.tag == "metadata" and m.get("key") == "plater_id"), "")
        mine = own.setdefault(pid, {})
        kept: list = [("plate", tuple(sorted(el.attrib.items())))]
        for child in el:
            if child.tag == "metadata" and child.get("key") in OWN:
                mine[child.get("key")] = child.get("value")
            else:
                kept += dump(child)
        rest.append(kept)
    return rest, own


def _plate_span(settings: bytes, plate: int) -> tuple[int, int]:
    """Where the plate with this ``plater_id`` starts (its opening tag) and ends (its closing tag)."""
    for m in _PLATE.finditer(settings):
        end = settings.find(b"</plate>", m.end())
        if end < 0:
            break
        for meta in _METADATA.finditer(settings, m.end(), end):
            if _attr(meta.group(0), _KEY) == "plater_id":
                if _value_of(meta.group(0)) == str(plate):
                    return m.start(), end
                break
    raise InvalidPlateSetting(f"the project has no plate {plate}")


def _set(settings: bytes, plate: int, key: str, value: str | None, newline: bytes) -> tuple[bytes, str | None]:
    """``settings`` with one of the plate's lines set (None: removed), and the value it had."""
    start, end = _plate_span(settings, plate)
    metas = list(_METADATA.finditer(settings, start, end))
    mine = [m for m in metas if _attr(m.group(0), _KEY) == key]
    if len(mine) > 1:
        raise InvalidPlateSetting(f"{key} is written twice on plate {plate}")
    before = _value_of(mine[0].group(0)) if mine else None
    if value is None:
        if mine:
            a, b = _line(settings, mine[0].start(), mine[0].end())
            settings = settings[:a] + settings[b:]
        return settings, before
    attr = f' value="{_escape(value)}"'.encode()
    if mine:
        tag = mine[0].group(0)
        v = _VALUE.search(tag)
        return settings[: mine[0].start()] + tag[: v.start()] + attr + tag[v.end() :] + settings[
            mine[0].end() :
        ], before
    # after the last line Orca writes before it, on a line of its own with the same indentation
    earlier = [m for m in metas if (_attr(m.group(0), _KEY) or "") in ORDER[: ORDER.index(key)]]
    if not earlier:
        raise InvalidPlateSetting(f"plate {plate} has no plater_id line to write after")
    last = earlier[-1]
    a, b = _line(settings, last.start(), last.end())
    indent = settings[a : last.start()] if a < last.start() else b""
    tag = b'<metadata key="' + key.encode("ascii") + b'"' + attr + b"/>"
    if b > last.end():  # the line ends there
        return settings[:b] + indent + tag + newline + settings[b:], before
    return settings[: last.end()] + newline + indent + tag + settings[last.end() :], before


def apply(settings: bytes, cfg: dict, edits: list[PlateSetting], slicer: str) -> tuple[bytes, list[dict]]:
    """``model_settings.config`` with the plates' edits made (each checked), and what changed: ``{key:
    "plate_setting", plate, setting, before, after}`` (None: not the plate's own)."""
    if len(edits) > MAX_EDITS:
        raise InvalidPlateSetting(f"too many plate settings at once (at most {MAX_EDITS})")
    rest_before, own_before = _plates(settings)
    expected = {k: dict(v) for k, v in own_before.items()}
    newline = b"\r\n" if b"\r\n" in settings else b"\n"
    seen: set[tuple[int, str]] = set()
    applied = []
    for e in edits:
        if (e.plate, e.key) in seen:
            raise InvalidPlateSetting(f"{e.key} of plate {e.plate} is set twice")
        seen.add((e.plate, e.key))
        for key, value in check(e, cfg, slicer).items():
            settings, before = _set(settings, e.plate, key, value, newline)
            if before == value:
                continue
            applied.append({"key": "plate_setting", "plate": e.plate, "setting": key, "before": before, "after": value})
            mine = expected.setdefault(str(e.plate), {})
            if value is None:
                mine.pop(key, None)
            else:
                mine[key] = value
    rest_after, own_after = _plates(settings)
    if rest_after != rest_before or own_after != expected:
        raise InvalidPlateSetting("model_settings.config changed beyond the plate settings edited")
    return settings, applied
