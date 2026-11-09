"""The project settings the workspace shows, which of them can change, and the check of each change (fork).

Labels, units, ranges, options and categories come from the preset field schemas already in the
repo (``backend/app/data/{process,filament,printer}_fields.json``, served by the cloud preset
editor). Their categories map onto the slicer's tabs; a key the schemas don't know keeps no
category and is shown read only, as it is in the project.

A setting can change only when its type has a reliable check (integer, decimal, boolean, list of
options), its category is one where a new value can't make the project incompatible with its
printer, and every value it holds in the project reads as that type. Identifiers, preset names,
free text and G-code stay read only.

Values are kept in the project's own format: strings, a list per filament or extruder, ``15%``
stays a percentage, ``1``/``0`` booleans stay so, ``nil`` (inherit) is never written by hand.
"""

from __future__ import annotations

import math
import re
from functools import cache

SECTIONS = ("process", "filament", "printer")
# The process tabs of OrcaSlicer / Bambu Studio, then the filament and printer presets.
TABS = ("quality", "strength", "speed", "support", "multimaterial", "others", "filament", "printer")

# Schema category -> slicer tab (process settings); filament and printer settings have their own tab.
_PROCESS_TABS = {
    "quality": "quality",
    "strength": "strength",
    "infill": "strength",
    "speed": "speed",
    "acceleration": "speed",
    "support": "support",
    "adhesion": "others",
    "multimaterial": "multimaterial",
    "advanced": "others",
    "special": "others",
}
# Categories whose values can change without making the project incompatible with its printer.
# Printer geometry, nozzle, limits, G-code and material identity (type, density, diameter) stay read only.
_EDITABLE_CATEGORIES = {
    "process": set(_PROCESS_TABS),
    "filament": {"temperature", "flow", "cooling", "chamber", "retraction"},
    "printer": {"retraction"},
}
# Schema entries typed "text" that hold one number per filament.
# shortcut: fixed here; move into filament_fields.json if the cloud preset editor wants them too.
_TYPE_FIXES = {
    "nozzle_temperature": {"type": "number", "unit": "°C", "min": 0, "max": 500},
    "nozzle_temperature_initial_layer": {"type": "number", "unit": "°C", "min": 0, "max": 500},
    "filament_max_volumetric_speed": {"type": "number", "unit": "mm³/s", "min": 0, "max": 200},
}
NIL = "nil"  # Orca/Bambu: "not set here, the preset's value applies"
_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")


@cache
def catalogue() -> dict[str, dict]:
    """Every schema field by key, with its section, tab and value type.

    The slicer's own layout, labels and Simple / Advanced mode come to the browser from OrcaSlicer
    (``frontend/src/farm/workspace/orca-assets``); these tabs are the fallback for keys it doesn't list.
    """
    from backend.app.api.routes.cloud import _load_fields

    fields: dict[str, dict] = {}
    for section in SECTIONS:
        for raw in _load_fields(section)["fields"]:
            field = {**raw, **_TYPE_FIXES.get(raw["key"], {})}
            kind = field["type"]
            if kind == "number":
                # Counts (walls, layers, loops) have no unit and no step in the schemas.
                kind = "int" if not field.get("step") and not field.get("unit") else "float"
            elif kind == "select":
                kind = "enum"
            elif kind != "boolean":
                kind = "string"
            fields[field["key"]] = {
                "key": field["key"],
                "label": field.get("label") or field["key"],
                "description": field.get("description"),
                "section": section,
                "group": field.get("category"),
                "tab": _PROCESS_TABS.get(field.get("category"), "others") if section == "process" else section,
                "type": "bool" if kind == "boolean" else kind,
                "unit": field.get("unit"),
                "min": field.get("min"),
                "max": field.get("max"),
                "step": field.get("step"),
                "options": field.get("options"),
            }
    return fields


def _elements(value) -> list | None:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return value
    return None


def _reads_as(field: dict, value: str) -> bool:
    if value == NIL:
        return True
    kind = field["type"]
    if kind == "bool":
        return value in ("0", "1", "true", "false")
    if kind == "int":
        return bool(_INT_RE.match(value))
    if kind == "float":
        return bool(_FLOAT_RE.match(value.removesuffix("%")))
    if kind == "enum":
        return value in {o["value"] for o in field["options"] or []}
    return False


def _readonly_reason(field: dict | None, value) -> str | None:
    """Why a project value can't change (None: it can)."""
    if field is None:
        return "unknown"
    if field["type"] == "string":
        return "text"
    if field["group"] not in _EDITABLE_CATEGORIES[field["section"]]:
        return "category"
    elements = _elements(value)
    if not elements or not all(_reads_as(field, v) for v in elements) or all(v == NIL for v in elements):
        return "format"
    return None


def _system_sections(settings: dict) -> list[str] | None:
    """``different_settings_to_system``: keys that differ from the system preset, [process, filaments..., printer]."""
    diff = settings.get("different_settings_to_system")
    filaments = _elements(settings.get("filament_settings_id")) or []
    if not isinstance(diff, list) or len(diff) != len(filaments) + 2 or not all(isinstance(d, str) for d in diff):
        return None
    return diff


def _section_indexes(section: str, sections: list[str], elements: list[int] | None = None) -> list[int]:
    if section == "process":
        return [0]
    if section == "printer":
        return [len(sections) - 1]
    return [1 + i for i in (elements if elements is not None else range(len(sections) - 2))]


def describe(settings: dict, overrides: dict[str, list[dict]]) -> list[dict]:
    """Every key of the project: schema info, project value, whether it can change, preset and object state.

    ``differs_from_system``: the project's value differs from the system preset it inherits (True), is
    the preset's (False), or the file doesn't say (None). The preset's own value is not in the file
    and is never guessed. ``overrides``: objects or parts that slice with their own value of the key.
    """
    known = catalogue()
    sections = _system_sections(settings)
    out = []
    for key, value in settings.items():
        field = known.get(key)
        reason = _readonly_reason(field, value)
        differs = None
        if sections is not None and field is not None:
            differs = any(
                key in sections[i].split(";") for i in _section_indexes(field["section"], sections) if i < len(sections)
            )
        entry = dict(field) if field else {"key": key, "label": key, "section": None, "tab": None, "type": "string"}
        entry.update(
            {
                "value": value,
                "editable": reason is None,
                "readonly_reason": reason,
                "differs_from_system": differs,
                "overrides": overrides.get(key, []),
            }
        )
        out.append(entry)
    return out


class InvalidChange(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


def _format_number(number: float) -> str:
    text = repr(number)
    return text[:-2] if text.endswith(".0") else text


def _normalize(field: dict, new, old: str) -> str:
    """``new`` (from the browser) in the format of ``old``; ValueError with the reason when it doesn't fit."""
    text = str(new).strip() if not isinstance(new, bool) else ("1" if new else "0")
    if text == old:
        return old
    if text == NIL:
        raise ValueError("the inherited value can't be set by hand")
    kind = field["type"]
    if kind == "bool":
        if text.lower() not in ("0", "1", "true", "false"):
            raise ValueError("not a yes/no value")
        on = text.lower() in ("1", "true")
        return ("true" if on else "false") if old in ("true", "false") else ("1" if on else "0")
    if kind == "enum":
        if text not in {o["value"] for o in field["options"] or []}:
            raise ValueError("not one of the options")
        return text
    percent = old.endswith("%")
    if text.endswith("%") and not percent:
        raise ValueError("this value is not a percentage")
    number_text = text.removesuffix("%")
    if kind == "int":
        if not _INT_RE.match(number_text):
            raise ValueError("not a whole number")
        number = int(number_text)
    else:
        try:
            number = float(number_text)
        except ValueError:
            raise ValueError("not a number") from None
        if not math.isfinite(number):
            raise ValueError("not a number")
    if field.get("min") is not None and number < field["min"]:
        raise ValueError(f"below the minimum {field['min']}")
    if field.get("max") is not None and number > field["max"]:
        raise ValueError(f"above the maximum {field['max']}")
    result = str(number) if kind == "int" else _format_number(number)
    return f"{result}%" if percent else result


def apply_changes(settings: dict, changes: dict) -> tuple[dict, list[dict]]:
    """``settings`` with ``changes`` applied (``InvalidChange`` lists every value refused).

    Returns the new settings and what actually changed: ``{key, before, after}``. A changed key is
    added to ``different_settings_to_system`` for its section, so the slicer shows it as a change
    against the system preset; nothing else is touched.
    """
    known = catalogue()
    errors: dict[str, str] = {}
    result = dict(settings)
    applied = []
    sections = _system_sections(settings)
    sections = list(sections) if sections is not None else None
    for key, new in changes.items():
        old = settings.get(key)
        field = known.get(key)
        if old is None:
            errors[key] = "not in this project"
            continue
        reason = _readonly_reason(field, old)
        if reason is not None:
            errors[key] = f"read only ({reason})"
            continue
        olds = _elements(old)
        news = new if isinstance(new, list) else [new]
        if isinstance(old, list) != isinstance(new, list) or len(news) != len(olds):
            errors[key] = f"expected {len(olds)} value(s)" if isinstance(old, list) else "expected one value"
            continue
        try:
            values = [_normalize(field, n, o) for n, o in zip(news, olds, strict=True)]
        except ValueError as exc:
            errors[key] = str(exc)
            continue
        value = values if isinstance(old, list) else values[0]
        if value == old:
            continue
        result[key] = value
        applied.append({"key": key, "before": old, "after": value})
        if sections is not None:
            changed = [i for i, (a, b) in enumerate(zip(olds, values, strict=True)) if a != b]
            for i in _section_indexes(field["section"], sections, changed):
                if i >= len(sections) - 1 and field["section"] == "filament":
                    continue  # more values than filaments: the file doesn't say whose they are
                listed = [k for k in sections[i].split(";") if k]
                if key not in listed:
                    sections[i] = ";".join([*listed, key])
    if errors:
        raise InvalidChange(errors)
    if sections is not None and applied:
        result["different_settings_to_system"] = sections
    return result, applied
