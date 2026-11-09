#!/usr/bin/env python3
"""Build the 3MF Workspace's settings data and icons from an OrcaSlicer (or Bambu Studio) source checkout.

OrcaSlicer (https://github.com/OrcaSlicer/OrcaSlicer) is AGPL-3.0, like Printbuddy. From a checkout of
one release tag this reads:

* ``src/slic3r/GUI/Tab.cpp``: the pages, option groups and option order of the process (TabPrint),
  filament (TabFilament) and printer (TabPrinter) settings, with their icon names (option lists the
  printer pages loop over come from ``src/libslic3r/PublishSettings.cpp``);
* ``src/libslic3r/PrintConfig.cpp``: per option its label, tooltip, unit (sidetext), min/max, enum
  values and labels, and mode (simple / advanced / develop; simple when an option sets none);
* ``localization/i18n/it/OrcaSlicer_it.po``: the Italian strings of those labels and tooltips;
* ``src/libslic3r/PrintConfig.hpp``: the keys of PrintObjectConfig and PrintRegionConfig, the settings an
  object (or a part) can have of its own (OrcaSlicer's object settings show only these);
* ``resources/images/*.svg``: the icons listed in ``ICONS`` (never logos) and the ``param_<value>`` icon of
  every enum value that has one (as OrcaSlicer's Field.cpp shows them in its lists), copied unchanged.

``UI_STRINGS`` (the 3D view's toolbars and menus) only add their Italian strings to the layout.
``src/slic3r/GUI/Shortcuts.cpp``, ``KeyChord.cpp`` and ``KBShortcutsDialog.cpp`` give the "Keyboard
Shortcuts" dialog: its pages, sections and rows with their default keys (``shortcuts`` in the layout).

It writes ``frontend/src/farm/workspace/orca-assets/`` (``settings-layout.json`` and ``icons/``).
The layout only drives tabs, groups, order, labels and mode: which settings can change and how a value
is checked stays Printbuddy's (``backend/app/farm/workspace/settings.py``).

    git clone --depth 1 --branch v2.5.0 https://github.com/OrcaSlicer/OrcaSlicer /tmp/orca
    python tools/orca_settings/generate.py /tmp/orca v2.5.0
    git clone --depth 1 --branch v02.09.01.70 https://github.com/bambulab/BambuStudio /tmp/bambu
    python tools/orca_settings/generate.py /tmp/bambu v02.09.01.70 --slicer bambu_studio

Every option PrintConfig.cpp defines is written twice: with its labels, tooltips and Italian into the
frontend's layout file, and as the backend's validation data (type, list or single value, range, enum
values, open enum, nullable, readonly, multiline text, preset section from Preset.cpp's option lists) in
``backend/app/data/slicer_options/<slicer>.json``. Bambu Studio projects use the Bambu Studio data; the
icons are always OrcaSlicer's (run the OrcaSlicer one first).

The checkout's commit is recorded next to the tag (a warning when HEAD is not the tag). For a copy of the
files that is not a git checkout, pass the tag's commit as a third argument.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "frontend" / "src" / "farm" / "workspace" / "orca-assets"
BACKEND_OUT = REPO / "backend" / "app" / "data" / "slicer_options"

# Tab.cpp functions that build each preset's pages
SECTIONS = {
    "process": ["TabPrint::build"],
    "filament": ["TabFilament::build", "TabFilament::add_filament_overrides_page"],
    "printer": ["TabPrinter::build_fff", "TabPrinter::build_kinematics_page", "TabPrinter::build_unregular_pages"],
}
# Per slicer: its repository, Italian catalogue, files with option lists, Tab.cpp functions, output names
SLICERS = {
    "orcaslicer": {
        "name": "OrcaSlicer",
        "repo": "OrcaSlicer/OrcaSlicer",
        "po": "localization/i18n/it/OrcaSlicer_it.po",
        "publish": "src/libslic3r/PublishSettings.cpp",
        "sections": SECTIONS,
        "layout": "settings-layout.json",
        "icons": True,
    },
    "bambu_studio": {
        "name": "Bambu Studio",
        "repo": "bambulab/BambuStudio",
        "po": "bbl/i18n/it/BambuStudio_it.po",
        "publish": None,
        "sections": {
            "process": ["TabPrint::build"],
            "filament": ["TabFilament::build", "TabFilament::add_filament_overrides_page"],
            "printer": [
                "TabPrinter::build_fff",
                "TabPrinter::build_kinematics_page",
                "TabPrinter::build_unregular_pages",
            ],
        },
        "layout": "settings-layout-bambu.json",
        "icons": False,
    },
}
# Preset.cpp's option lists -> the preset (section) a key belongs to
PRESET_LISTS = {
    "print": "process",
    "filament": "filament",
    "machine_limits": "printer",
    "printer": "printer",
}
# Icons copied from resources/images: OrcaSlicer's own icons (the _dark variants for a dark UI), no logos
ICONS = [
    # setting pages
    "custom-gcode_quality",
    "custom-gcode_strength",
    "custom-gcode_speed",
    "custom-gcode_support",
    "custom-gcode_multi_material",
    "custom-gcode_other",
    "custom-gcode_filament",
    "custom-gcode_cooling_fan",
    "custom-gcode_advanced",
    "custom-gcode_object-info",
    "custom-gcode_gcode",
    "custom-gcode_motion",
    "custom-gcode_extruder",
    "custom-gcode_setting_override",
    # object tools
    "toolbar_move_dark",
    "toolbar_rotate_dark",
    "toolbar_scale_dark",
    "toolbar_cut_dark",
    "toolbar_support_dark",
    "toolbar_seam_dark",
    # sidebar and plates
    "printer",
    "filament",
    "search",
    "edit",
    "plate_settings_dark",
    # 3D view: main toolbar (GLCanvas3D::_init_main_toolbar), gizmos (GLGizmosManager), assembly view
    "toolbar_open_dark",
    "toolbar_add_plate_dark",
    "toolbar_orient_dark",
    "toolbar_arrange_dark",
    "instance_add_dark",
    "instance_remove_dark",
    "split_objects_dark",
    "split_parts_dark",
    "toolbar_variable_layer_height_dark",
    "toolbar_flatten_dark",
    "toolbar_meshboolean_dark",
    "toolbar_fuzzy_skin_paint_dark",
    "toolbar_texture_displacement",
    "toolbar_text_dark",
    "toolbar_measure_dark",
    "toolbar_assembly_dark",
    "toolbar_brimears_dark",
    "toolbar_assemble_dark",
    # 3D view: canvas buttons (bottom left) and the plate's own buttons (PartPlate.cpp)
    "canvas_section_dark",
    "canvas_menu_dark",
    "canvas_zoom_dark",
    "plate_close_dark",
    "plate_orient_dark",
    "plate_arrange_dark",
    "plate_unlocked_dark",
    "plate_name_edit_dark",
    "plate_move_front_dark",
    # objects tree: a part's kind, own settings
    "cog",
    "menu_add_part",
    "menu_add_negative",
    "menu_add_modifier",
    "menu_support_blocker",
    "menu_support_enforcer",
    # objects tree columns (ObjectDataViewModel.cpp), mesh warning, the shortcuts dialog's note
    "obj_warning",
    "obj_variable_layer_height",
    "objlist_support_painting",
    "objlist_color_painting",
    "objlist_sinking",
    "help",
]
# Strings of the 3D view's toolbars, canvas buttons, plate buttons and right-click menus (GLCanvas3D.cpp,
# GLGizmosManager.cpp, PartPlate.cpp, GUI_Factories.cpp, Plater.cpp, MainFrame.cpp), for their Italian
UI_STRINGS = [
    "Add",
    "Add plate",
    "Auto orientation",
    "Arrange all objects",
    "Add instance",
    "Remove instance",
    "Split to objects",
    "Split to parts",
    "Variable layer height",
    "Move",
    "Rotate",
    "Scale",
    "Lay on Face",
    "Cut",
    "Mesh Boolean",
    "Support Painting",
    "Seam painting",
    "Paint-on fuzzy skin",
    "Texture displacement",
    "Emboss",
    "Measure",
    "Assemble",
    "Brim Ears",
    "Assembly View",
    "Section view",
    "Fit camera to scene or selected object.",
    "Gridlines",
    "Axes",
    "Default View",
    "Top View",
    "Bottom View",
    "Front View",
    "Rear View",
    "Left View",
    "Right View",
    "Remove current plate (if not last one)",
    "Auto orient objects on current plate",
    "Arrange objects on current plate",
    "Lock current plate",
    "Edit current plate name",
    "Move plate to the front",
    "Customize current plate",
    "Clone",
    "Instances",
    "Center",
    "Drop",
    "Transform",
    "Align/Distribute",
    "Edit",
    "Delete",
    "Add Part",
    "Add Negative Part",
    "Add Modifier",
    "Add Support Blocker",
    "Add Support Enforcer",
    "Add Precise Seam",
    "Height Range Modifier",
    "Printable",
    "Auto Drop",
    "Edit Process Settings",
    "Copy Process Settings",
    "Paste Process Settings",
    "Edit in Parameter Table",
    "More",
    "Select All",
    "Select All Plates",
    "Delete All",
    "Arrange",
    "Auto Rotate",
    "Edit Plate Name",
    "Lock",
    "Delete Plate",
    "Add Primitive",
    "Add Handy models",
    "Add Models",
    "Reload All",
    "Order By",
    "Replace all with 3D files",
    "Plate Settings",
    "Frequent",
    "Search plate, object and part.",
    "Object name",
    "Size",
    "Volume",
    "Triangles",
    "Plate",
    "Objects",
    "Object",
    "Zoom",
    "Info",
    "Select",
    "Global",
    "Close",
    # canvas menu, section view, more right-click menus, object info (batch B)
    "3D Navigator",
    "Zoom button",
    "Overhangs",
    "Outline",
    "Wireframe",
    "X-Ray",
    "Realistic View",
    "Perspective",
    "Focus mode",
    "Labels",
    "Center of mass",
    "Set viewing angle",
    "Reset",
    "Toggle section view",
    "Change Type",
    "Change Filament",
    "Set Filament for selected items",
    "Fix Model",
    "Precise Seam Type",
    "Show Labels",
    "Deselect All",
    "Errors",
    # objects tree: instances, columns, mesh errors (ObjectDataViewModel.cpp, GUI_ObjectList.cpp, GUI_Factories.cpp)
    "Instance %d",
    "[P%d]Instance %d",
    "Set as An Individual Object",
    "Set as Individual Objects",
    "Remaining errors",
    "%1$d non-manifold edge",
    "%1$d non-manifold edges",
    "Error: %1$d non-manifold edge.",
    "Error: %1$d non-manifold edges.",
    "Click the icon to repair model object",
    "Click the icon to reset all settings of the object",
    "Click the icon to toggle printable properties of the object",
    "Click the icon to edit support painting of the object",
    "Click the icon to edit color painting for the object",
    "Click the icon to shift this object to the bed",
    "Color Painting",
    # the object info box (Plater.cpp), with their placeholders and line ends as OrcaSlicer writes them
    "Number of currently selected parts: %1%\n",
    "Number of currently selected objects: %1%\n",
    "Part name: %1%\n",
    "Object name: %1%\n",
    "Size: %1% x %2% x %3% mm\n",
    "Volume: %1% mm³\n",
    "Triangles: %1%\n",
    "Tips:",
    'Use "Fix Model" to repair the mesh.',
    # section view panel (GLCanvas3D.cpp) and the shortcuts dialog
    "Mouse wheel on icon",
    "Right click on icon",
    "Middle click on icon",
    "Move section plane",
    "Keyboard Shortcuts",
    # the Preview's legend (GCodeViewer.cpp): view types, titles, columns, roles, options and statistics
    "Summary",
    "Line Type",
    "Filament",
    "Speed",
    "Actual Speed",
    "Acceleration",
    "Jerk",
    "Layer Height",
    "Line Width",
    "Flow",
    "Actual Flow",
    "Layer Time",
    "Layer Time (log)",
    "Fan Speed",
    "Temperature",
    "Pressure Advance",
    "Speed (mm/s)",
    "Acceleration (mm/s²)",
    "Jerk (mm/s)",
    "Layer height (mm)",
    "Line width (mm)",
    "Volumetric flow rate (mm³/s)",
    "Fan speed (%)",
    "Temperature (℃)",
    "Time",
    "Usage",
    "Options",
    "Model",
    "Support",
    "Flushed",
    "Tower",
    "Total",
    "Cost",
    "Total time",
    "Travel",
    "Retract",
    "Unretract",
    "Wipe",
    "Seams",
    "Undefined",
    "Inner wall",
    "Outer wall",
    "Overhang wall",
    "Sparse infill",
    "Internal solid infill",
    "Top surface",
    "Ironing",
    "Bridge",
    "Gap infill",
    "Skirt",
    "Support interface",
    "Prime tower",
    "Custom",
    "Bottom surface",
    "Internal Bridge",
    "Brim",
    "Support transition",
    "Multiple",
    "Total estimation",
    "Time Estimation",
    "Total Filament",
    "Model Filament",
    "Prepare time",
    "Model printing time",
    "Filament change times",
    "Tool changes",
    # the layer slider (IMSlider.cpp)
    "Jump to layer",
    "Please enter the layer number.",
    "OK",
    "Cancel",
    "Add Pause",
    "Add Custom G-code",
    "Change Filament",
    "Insert a pause command at the beginning of this layer.",
    "Insert custom G-code at the beginning of this layer.",
]
# A plate's own settings: the key in model_settings.config -> the option it sets (bbs_3mf.cpp)
PLATE_KEYS = {
    "bed_type": "curr_bed_type",
    "skirt_start_angle": "skirt_start_angle",
    "print_sequence": "print_sequence",
    "spiral_mode": "spiral_mode",
    "first_layer_print_sequence": "first_layer_print_sequence",
    "other_layers_print_sequence": "other_layers_print_sequence",
}

_STR = r'"(?:[^"\\]|\\.)*"'


def _strip_comments(text: str) -> str:
    """C++ source without // comments (a // inside a string literal is kept)."""
    out = []
    for line in text.splitlines():
        quote = False
        for i, ch in enumerate(line):
            if ch == '"' and (i == 0 or line[i - 1] != "\\"):
                quote = not quote
            elif not quote and line.startswith("//", i):
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _unescape(literals: str) -> str:
    text = "".join(p[1:-1] for p in re.findall(_STR, literals))
    text = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)  # "℃" -> "℃"
    return text.replace('\\"', '"').replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def _function_body(src: str, name: str) -> str:
    """The body of ``name`` (``Class::method``): from its definition's ``{`` to the matching ``}``."""
    start = re.search(rf"^[^\n;]*\b{re.escape(name)}\s*\([^)]*\)\s*\{{", src, re.M)
    if not start:
        raise SystemExit(f"Tab.cpp: {name} not found")
    depth = 0
    for i in range(start.end() - 1, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start.end() : i]
    raise SystemExit(f"Tab.cpp: {name} has no end")


_EVENT_RE = re.compile(
    r"add_options_page\(\s*L\((?P<page>" + _STR + r')\)\s*,\s*"(?P<page_icon>[^"]*)"'
    # Bambu Studio's extruder page: add_options_page(page_name, "empty", true), page_name "Extruder"
    r"|add_options_page\(\s*(?P<extruder_page>page_name)\b"
    r"|new_optgroup\(\s*L\((?P<group>" + _STR + r')\)(?:\s*,\s*L?"(?P<group_icon>[^"]*)")?'
    r'|append_single_option_line\(\s*"(?P<k1>[a-zA-Z0-9_]+)"'
    r'|get_option\(\s*"(?P<k2>[a-zA-Z0-9_]+)"'
    # append_option_line(optgroup, "key") and local helpers like Bambu's append_nozzle_scaled_option(optgroup, "key")
    r'|\bappend_\w+\(\s*\w+\s*,\s*"(?P<k3>[a-zA-Z0-9_]+)"'
    # a loop appending one option per item: for (const std::string &axis : axes), : { "a", "b" }, : f()
    r"|for\s*\(\s*const\s+(?:std::string|PublishablePrinterOption)\s*&?\s*(?P<var>\w+)\s*:\s*"
    r"(?P<items>\{[^}]*\}|\w+\s*(?:\(\s*\))?)\s*\)"
)
_VECTOR_RE = re.compile(r"std::vector<std::string>\s+(\w+)\s*=?\s*\{([^}]*)\}")
_OPTION_LIST_RE = re.compile(
    r"&\s*(\w+)\s*\(\s*\)\s*\{\s*static\s+const\s+std::vector<\w+>\s+\w+\s*=\s*\{(.*?)\};", re.S
)


def _loop_keys(m: re.Match, body: str, lists: dict[str, list[str]]) -> list[str]:
    """The option keys a ``for`` loop appends ([] for a loop that appends none, e.g. in an on_change)."""
    after = body[m.end() : m.end() + 600]
    if not re.search(r"\bappend_\w+\(|create_line_with_near_label_widget", after):  # also local lambdas
        return []
    items = m["items"].strip()
    if items.startswith("{"):
        names = [_unescape(s) for s in re.findall(_STR, items)]
    else:
        names = lists.get(re.sub(r"\W", "", items), [])
    prefix = re.search(rf'"([a-zA-Z0-9_]+_)"\s*\+\s*{m["var"]}\b', after)  # "machine_max_jerk_" + axis
    return [prefix.group(1) + n if prefix else n for n in names]


def layout(tab_src: str, publish_src: str = "", sections: dict | None = None) -> dict[str, list[dict]]:
    """Per preset: its pages, each with its option groups and their keys, in the client's order."""
    src = _strip_comments(tab_src)
    # option lists defined elsewhere: PublishSettings.cpp helpers, std::vector literals in Tab.cpp
    lists = {
        name: [_unescape(s) for s in re.findall(r"\{\s*(" + _STR + r")", items)]
        for name, items in _OPTION_LIST_RE.findall(_strip_comments(publish_src))
    }
    lists |= {name: [_unescape(s) for s in re.findall(_STR, items)] for name, items in _VECTOR_RE.findall(src)}
    out: dict[str, list[dict]] = {}
    for section, functions in (sections or SECTIONS).items():
        pages: list[dict] = []
        seen: set[str] = set()
        for fn in functions:
            page = group = None
            body = _function_body(src, fn)
            for m in _EVENT_RE.finditer(body):
                if m["var"]:
                    for key in _loop_keys(m, body, lists):
                        if group is not None and key not in seen:
                            seen.add(key)
                            group["keys"].append(key)
                    continue
                if m["page"] or m["extruder_page"]:
                    title = _unescape(m["page"]) if m["page"] else "Extruder"
                    page = next((p for p in pages if p["title"] == title), None)
                    if page is None:
                        page = {"title": title, "icon": m["page_icon"], "groups": []}
                        pages.append(page)
                    group = None
                elif m["group"]:
                    if page is None:
                        continue
                    title = _unescape(m["group"])
                    group = next((g for g in page["groups"] if g["title"] == title), None)
                    if group is None:
                        group = {"title": title, "icon": m["group_icon"] or None, "keys": []}
                        page["groups"].append(group)
                else:
                    key = m["k1"] or m["k2"] or m["k3"]
                    if key.endswith("_"):  # "machine_max_acceleration_" + axis: the loop above added them
                        continue
                    if group is not None and key not in seen:
                        seen.add(key)
                        group["keys"].append(key)
        out[section] = [
            {**p, "groups": [g for g in p["groups"] if g["keys"]]} for p in pages if any(g["keys"] for g in p["groups"])
        ]
    return out


_DEF_RE = re.compile(r'def\s*=\s*this->add(?:_nullable)?\(\s*"([a-zA-Z0-9_]+)"\s*,\s*(co\w+)')


def _text(block: str, name: str) -> str | None:
    m = re.search(rf"def->{name}\s*=\s*(?:L\(|_L\(|_u8L\()?((?:\s*(?:u8)?{_STR})+)", block)
    return _unescape(m.group(1)) if m else None


def _number(block: str, name: str) -> float | None:
    m = re.search(rf"def->{name}\s*=\s*(-?\d+(?:\.\d+)?)\s*;", block)
    return float(m.group(1)) if m else None


def _flag(block: str, name: str) -> bool:
    return bool(re.search(rf"def->{name}\s*=\s*true\s*;", block))


def options(cfg_src: str, keys: set[str] | None = None, wanted: set[str] = frozenset()) -> dict[str, dict]:
    """The definitions of ``keys`` in PrintConfig.cpp (every option it defines when None).

    ``wanted``: more keys to define when they are copies made in a loop (the filament overrides)."""
    src = _strip_comments(cfg_src)
    defs = list(_DEF_RE.finditer(src))
    out: dict[str, dict] = {}
    for i, m in enumerate(defs):
        key = m.group(1)
        if (keys is not None and key not in keys) or key in out:
            continue
        block = src[m.end() : defs[i + 1].start() if i + 1 < len(defs) else len(src)]
        mode = re.search(r"def->mode\s*=\s*com(\w+)", block)
        values = [
            _unescape(v)
            for v in re.findall(rf"def->enum_values\.(?:push|emplace)_back\(\s*(?:_?L\()?\s*({_STR})", block)
        ]
        labels = [
            _unescape(v)
            for v in re.findall(rf"def->enum_labels\.(?:push|emplace)_back\(\s*(?:_?L\()?\s*({_STR})", block)
        ]
        # a list written at once: def->enum_values = {"a", "b"}; def->enum_labels = {L("A"), L("B")};
        listed = re.search(r"def->enum_values\s*=\s*\{([^}]*)\}", block)
        if listed and not values:
            values = [_unescape(v) for v in re.findall(_STR, listed.group(1))]
            listed_labels = re.search(r"def->enum_labels\s*=\s*\{([^}]*)\}", block)
            labels = [_unescape(v) for v in re.findall(_STR, listed_labels.group(1))] if listed_labels else []
        # the values of another option: def->enum_values = def_top_fill_pattern->enum_values;
        shared = re.search(r"def->enum_values\s*=\s*(\w+)->enum_values", block)
        if shared and not values:
            source = re.search(r"\b" + shared.group(1) + r'\s*=\s*def\s*=\s*this->add(?:_nullable)?\(\s*"(\w+)"', src)
            if source and source.group(1) in out:
                values = [e["value"] for e in out[source.group(1)].get("enum", [])]
                labels = [e["label"] for e in out[source.group(1)].get("enum", [])]
        gui = re.search(r'def->gui_type\s*=\s*(?:ConfigOptionDef::GUIType::)?"?(\w+)', block)
        kind = m.group(2)
        entry = {
            "type": m.group(2),
            # full_label: the name out of its row ("Nozzle temperature", not "Other layers")
            "label": _text(block, "full_label") or _text(block, "label") or key,
            "tooltip": _text(block, "tooltip"),
            "unit": _text(block, "sidetext"),
            "min": _number(block, "min"),
            "max": _number(block, "max"),
            "mode": mode.group(1).lower() if mode else "simple",
            # one value per extruder / filament / variant ("coFloats"...); "nil" allowed in a nullable one
            "vector": kind.endswith("s") or None,
            "nullable": ("add_nullable" in m.group(0) or _flag(block, "nullable")) or None,
            "readonly": _flag(block, "readonly") or None,
            "multiline": _flag(block, "multiline") or _flag(block, "full_width") or None,
            # an open list: a value outside the enum is fine (f_enum_open, i_enum_open, select_open)
            "open": bool(gui and "open" in gui.group(1)) or None,
            "gui": gui.group(1) if gui else None,
        }
        if values:
            entry["enum"] = [{"value": v, "label": labels[j] if j < len(labels) else v} for j, v in enumerate(values)]
        out[key] = {k: v for k, v in entry.items() if v is not None}
    # The machine limits per axis, made in a loop: this->add("machine_max_speed_" + axis.name, coFloats)
    axis_defs = list(re.finditer(r'this->add\(\s*"(\w+_)"\s*\+\s*axis\.name\s*,\s*(co\w+)\s*\)', src))
    for i, m in enumerate(axis_defs):
        end = axis_defs[i + 1].start() if i + 1 < len(axis_defs) else src.find("set_default_value", m.end())
        block = src[m.end() : end + 200]
        mode = re.search(r"def->mode\s*=\s*com(\w+)", block)
        strings = [_unescape(v) for v in re.findall(rf"\(void\)\s*L\(\s*({_STR})\s*\)", block)]
        for axis in ("x", "y", "z", "e"):
            key = m.group(1) + axis
            if (keys is not None and key not in keys) or key in out:
                continue
            upper = axis.upper()
            entry = {
                "type": m.group(2),
                "label": next((s for s in strings if s.endswith(f" {upper}")), key),
                "tooltip": next((s for s in strings if s.endswith(f" {upper} axis")), None),
                "unit": _text(block, "sidetext"),
                "min": _number(block, "min"),
                "max": _number(block, "max"),
                "mode": mode.group(1).lower() if mode else "simple",
                "vector": m.group(2).endswith("s") or None,
            }
            out[key] = {k: v for k, v in entry.items() if v is not None}
    # The filament's overrides ("filament_retraction_length") are copies of the printer option, made in a loop.
    for key in sorted(((keys or set()) | wanted) - out.keys()):  # sorted: the same file on every run
        if key.startswith("filament_") and key[len("filament_") :] in out:
            out[key] = {**out[key[len("filament_") :]], "nullable": True}  # "nil": the printer value applies
    return out


def object_keys(hpp_src: str) -> list[str]:
    """The keys of PrintObjectConfig and PrintRegionConfig (PrintConfig.hpp): what an object or part can override."""
    keys: list[str] = []
    for name in ("PrintObjectConfig", "PrintRegionConfig"):
        m = re.search(rf"PRINT_CONFIG_CLASS_DEFINE\(\s*{name},(.*?)\n\)", hpp_src, re.S)
        if not m:
            raise SystemExit(f"{name} not found in PrintConfig.hpp")
        keys += re.findall(r"\(\(ConfigOption[\w<>:]+,\s*(\w+)\)\)", _strip_comments(m.group(1)))
    return sorted(set(keys))


CTX = "\u0004"  # gettext's separator: a contextual msgid is keyed "context\u0004msgid"


def _po(text: str) -> dict[str, str]:
    """msgid -> msgstr of a .po file (fuzzy and empty translations left out); a contextual one under
    ``context + CTX + msgid``; a plural one under its singular (msgstr[0]) and plural (msgstr[1]) msgid."""
    out: dict[str, str] = {}
    lit = r"((?:" + _STR + r"\s*)+)"
    for entry in re.split(r"\n\s*\n", text):
        if "#, fuzzy" in entry:
            continue
        ctx = re.search(r"^msgctxt\s+" + lit, entry, re.M)
        prefix = _unescape(ctx.group(1)) + CTX if ctx else ""
        mid = re.search(r"^msgid\s+" + lit, entry, re.M)
        plural = re.search(r"^msgid_plural\s+" + lit, entry, re.M)
        if mid and plural:
            for key, n in ((mid, 0), (plural, 1)):
                mstr = re.search(rf"^msgstr\[{n}\]\s+" + lit, entry, re.M)
                if mstr and _unescape(key.group(1)) and _unescape(mstr.group(1)):
                    out[prefix + _unescape(key.group(1))] = _unescape(mstr.group(1))
            continue
        mstr = re.search(r"^msgstr\s+" + lit, entry, re.M)
        if mid and mstr:
            key, value = _unescape(mid.group(1)), _unescape(mstr.group(1))
            if key and value:
                out[prefix + key] = value
    return out


def _call_args(src: str, start: int) -> tuple[str, int]:
    """The text between the ``(`` at ``start`` and its matching ``)`` (string literals skipped), and the end."""
    depth, i, quote = 0, start, False
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == '"':
                quote = False
        elif ch == '"':
            quote = True
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
            if depth == 0:
                return src[start + 1 : i], i
        i += 1
    raise SystemExit("unbalanced call")


def _split_top(args: str) -> list[str]:
    """``args`` split at its top-level commas."""
    parts, depth, quote, cur = [], 0, False, ""
    for i, ch in enumerate(args):
        if quote:
            quote = not (ch == '"' and args[i - 1] != "\\")
        elif ch == '"':
            quote = True
        elif ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
            continue
        cur += ch
    return [p for p in parts + [cur.strip()] if p]


KEY_CTX = "Keyboard Shortcut"
# AppConfig.cpp's defaults: left drag rotates (2), middle and right pan (1); KBShortcutsDialog's mouse_action
MOUSE_DEFAULTS = {
    "left_mouse_drag_action": "Rotate View",
    "middle_mouse_drag_action": "Pan View",
    "right_mouse_drag_action": "Pan View",
}


def shortcuts(table_src: str, hpp_src: str, chord_src: str, dialog_src: str) -> tuple[list[dict], set[str]]:
    """OrcaSlicer's "Keyboard Shortcuts" dialog (non-Apple keys): its pages, each with its caption and rows
    (section, key parts, description, the Shortcut id of a table row), and the strings to translate. A key
    part translated with the "Keyboard Shortcut" context is written ``KEY_CTX + CTX + part``."""
    table_src = re.sub(r"#ifdef SLIC3R_CAD.*?#endif", "", _strip_comments(table_src), flags=re.S)
    # #ifdef __APPLE__ ... #else ...: the later (non-Apple) definition wins
    chords = {m.group(1): m.group(2) for m in re.finditer(r"constexpr KeyChord (\w+)\{([^}]*)\}", table_src)}
    contexts: dict[str, set[str]] = {}
    for m in re.finditer(r"constexpr uint8_t (\w+)\s*=\s*([^;]+);", table_src):
        bits = re.findall(r"ShortcutContext::(\w+)|\b([A-Z_]+)\b", m.group(2))
        contexts[m.group(1)] = {b for c, name in bits for b in ({c} if c else contexts.get(name, set()))}
    mods = {"CTRL": ["Ctrl"], "SHIFT": ["Shift"], "CTRL_SHIFT": ["Ctrl", "Shift"], "wxMOD_RAW_CONTROL": ["Ctrl"]}
    names = {}
    for m in re.finditer(
        r'\{\s*(WXK_\w+),\s*(?:L_CONTEXT\("([^"]+)"[^)]*\)|"([^"]+)"),\s*(?:"[^"]*"|nullptr),\s*'
        r'(?:nullptr|L_CONTEXT\("([^"]+)"[^)]*\))\s*\}',
        _strip_comments(chord_src),
    ):
        names[m.group(1)] = m.group(4) or m.group(2) or m.group(3)
    strings: set[str] = set()

    def chord(text: str) -> list[str]:
        text = chords.get(text.strip(), text).strip().strip("{}").strip()
        if not text:
            return []  # no default key
        key, *modifiers = [p.strip() for p in text.split(",")]
        parts = [n for mod in modifiers for part in mod.split("|") for n in mods.get(part.strip(), [])]
        if key.startswith("'"):
            return parts + [key[1:-1]]
        label = names.get(key) or (f"F{key[5:]}" if key.startswith("WXK_F") else key)
        strings.add(KEY_CTX + CTX + label)
        return parts + [KEY_CTX + CTX + label]

    rows = []
    for m in re.finditer(
        r"\b(?:SHORTCUT|REPEATING|STEPPING)\(\s*(\w+),\s*\"\w+\",\s*L\(("
        + _STR
        + r")\),\s*([\w| ]+?),\s*(\{[^}]*\}|\w+)\s*\)",
        table_src,
    ):
        ctx = {c for name in m.group(3).split("|") for c in contexts.get(name.strip(), set())}
        rows.append({"id": m.group(1), "label": _unescape(m.group(2)), "contexts": ctx, "keys": chord(m.group(4))})
    enum = re.search(r"enum class ShortcutSection[^{]*\{([^}]*)\}", hpp_src).group(1)
    section_ids = [s.strip() for s in enum.split(",") if s.strip() and s.strip() != "Count"]
    firsts = [
        (m.group(1), _unescape(m.group(2)))
        for m in re.finditer(r"\{\s*Shortcut::(\w+),\s*L\((" + _STR + r")\)\s*\}", table_src)
    ]
    order = [r["id"] for r in rows]
    starts = [order.index(first) for first, _ in firsts]  # section_table: one per ShortcutSection, in order
    for i, r in enumerate(rows):
        r["section"] = max(n for n, st in enumerate(starts) if st <= i)
    section_names = [name for _, name in firsts]

    body = _function_body(_strip_comments(dialog_src), "KBShortcutsDialog::fill_pages")
    # its only #ifndef __APPLE__ keeps a row for the other systems: drop the directive lines, keep the row
    body = re.sub(r"^\s*#.*$", "", body, flags=re.M)
    variables: dict[str, list[str]] = {}

    def value(expr: str) -> list[str]:
        expr = expr.strip()
        if "+" in expr and not expr.startswith('"'):
            return ["".join(p for e in expr.split("+") for p in value(e))]
        if m := re.fullmatch(r"(?:from_u8\()?KeyChord::modifier_name\(wxMOD_(\w+)\)\)?", expr):
            return [{"CONTROL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift"}[m.group(1)]]
        if m := re.fullmatch(r"key\(L_CONTEXT\((" + _STR + r")[^)]*\)\)", expr):
            strings.add(KEY_CTX + CTX + _unescape(m.group(1)))
            return [KEY_CTX + CTX + _unescape(m.group(1))]
        if m := re.fullmatch(r"_?L\((" + _STR + r")\)", expr):
            strings.add(_unescape(m.group(1)))
            return [_unescape(m.group(1))]
        if expr.startswith('"'):
            return [_unescape(expr)]
        return variables[expr]

    for m in re.finditer(r"const wxString\s+(\w+)\s*=\s*([^;]+);", body):
        variables[m.group(1)] = value(m.group(2))
    pages = []
    for m in re.finditer(r"\bpage\(_L\(", body):
        args, _ = _call_args(body, m.end() - 4)
        title, caption, context, fixed = _split_top(args)
        out = [dict(r) for r in rows if context.split("::")[1] in r["contexts"]]
        for call in _split_top(fixed.strip()[1:-1]):
            kind, inner = call.split("(", 1)
            parts = _split_top(inner.rsplit(")", 1)[0])
            section = section_ids.index(parts[0].split("::")[1])
            if kind.strip() == "fixed":
                keys = [p for k in _split_top(parts[1].strip()[1:-1]) for p in value(k)]
                label = _unescape(re.search(r"L\((" + _STR + r")\)", parts[2]).group(1))
            else:  # mouse(section, button, preference)
                keys = value(parts[1])
                label = MOUSE_DEFAULTS[_unescape(parts[2])]
            out.append({"id": None, "label": label, "keys": keys, "section": section})
        out.sort(key=lambda r: r["section"])  # stable: table rows first, as the dialog's stable_sort
        pages.append(
            {
                "title": value(title)[0],
                "caption": value(caption)[0],
                "context": context.split("::")[1],
                "rows": [
                    {"id": r["id"], "section": section_names[r["section"]], "keys": r["keys"], "label": r["label"]}
                    for r in out
                ],
            }
        )
        strings |= {pages[-1]["title"], pages[-1]["caption"]} | {r["label"] for r in out}
    strings |= set(section_names) | {"Shift+"}
    return pages, strings


def _git(src: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _commit(src: Path, tag: str, given: str | None) -> str | None:
    """The commit the files come from: the checkout's HEAD, checked against the tag."""
    head = _git(src, "rev-parse", "HEAD")
    if head is None:
        print(f"warning: {src} is not a git checkout; commit {'given: ' + given if given else 'not recorded'}")
        return given
    tagged = _git(src, "rev-parse", f"{tag}^{{commit}}")
    if tagged != head:
        print(f"warning: HEAD {head[:12]} is not tag {tag} ({(tagged or 'unknown')[:12]}): regenerate from the tag")
    return head


def preset_sections(preset_src: str) -> dict[str, str]:
    """key -> process / filament / printer, from Preset.cpp's ``s_Preset_<kind>_options`` lists."""
    src = _strip_comments(preset_src)
    out: dict[str, str] = {}
    for kind, section in PRESET_LISTS.items():
        m = re.search(rf"s_Preset_{kind}_options\s*\{{(.*?)\}};", src, re.S)
        if not m:
            raise SystemExit(f"Preset.cpp: s_Preset_{kind}_options not found")
        for key in re.findall(r'"([a-zA-Z0-9_]+)"', m.group(1)):
            out.setdefault(key, section)
    return out


def backend_options(opts: dict[str, dict], sections: dict[str, str]) -> dict[str, dict]:
    """What the backend checks a value against, per key."""
    out = {}
    for key, o in sorted(opts.items()):
        entry = {
            "type": o["type"],
            "section": sections.get(key),
            "vector": o.get("vector"),
            "min": o.get("min"),
            "max": o.get("max"),
            "enum": [e["value"] for e in o["enum"]] if o.get("enum") else None,
            "open": o.get("open"),
            "nullable": o.get("nullable"),
            "readonly": o.get("readonly"),
            "multiline": o.get("multiline"),
        }
        out[key] = {k: v for k, v in entry.items() if v is not None}
    return out


def main() -> None:
    argv = sys.argv[1:]
    slicer = "orcaslicer"
    if "--slicer" in argv:
        i = argv.index("--slicer")
        slicer = argv[i + 1] if i + 1 < len(argv) else ""
        argv = argv[:i] + argv[i + 2 :]
    if len(argv) not in (2, 3) or slicer not in SLICERS:
        raise SystemExit(__doc__)
    conf = SLICERS[slicer]
    src, tag = Path(argv[0]), argv[1]
    commit = _commit(src, tag, argv[2] if len(argv) == 3 else None)
    pages = layout(
        (src / "src/slic3r/GUI/Tab.cpp").read_text(encoding="utf-8"),
        (src / conf["publish"]).read_text(encoding="utf-8") if conf["publish"] else "",
        conf["sections"],
    )
    per_object = object_keys((src / "src/libslic3r/PrintConfig.hpp").read_text(encoding="utf-8"))
    sections = preset_sections((src / "src/libslic3r/Preset.cpp").read_text(encoding="utf-8"))
    for section, section_pages in pages.items():  # what Tab.cpp places on a preset's pages is that preset's
        for k in (k for p in section_pages for g in p["groups"] for k in g["keys"]):
            sections.setdefault(k, section)
    keys = {k for section in pages.values() for p in section for g in p["groups"] for k in g["keys"]}
    keys |= set(PLATE_KEYS.values())
    opts = options((src / "src/libslic3r/PrintConfig.cpp").read_text(encoding="utf-8"), None, keys | set(sections))
    # an enum value's own icon, as Field.cpp shows it (param_<value>.svg when there is one); always
    # OrcaSlicer's icons, which the OrcaSlicer run copies
    images = src / "resources" / "images" if conf["icons"] else OUT / "icons"
    enum_icons = set()
    for o in opts.values():
        for e in o.get("enum", []):
            if (images / f"param_{e['value']}.svg").is_file():
                e["icon"] = f"param_{e['value']}"
                enum_icons.add(e["icon"])
    po = _po((src / conf["po"]).read_text(encoding="utf-8"))
    gui = src / "src" / "slic3r" / "GUI"
    keyboard, keyboard_strings = (
        shortcuts(
            *(
                (gui / f).read_text(encoding="utf-8")
                for f in ("Shortcuts.cpp", "Shortcuts.hpp", "KeyChord.cpp", "KBShortcutsDialog.cpp")
            )
        )
        if (gui / "KBShortcutsDialog.cpp").is_file() and (gui / "KeyChord.cpp").is_file()
        else ([], set())
    )
    wanted = {p["title"] for s in pages.values() for p in s} | set(UI_STRINGS) | keyboard_strings
    wanted |= {g["title"] for s in pages.values() for p in s for g in p["groups"]}
    for o in opts.values():
        wanted |= {o["label"], o.get("tooltip", ""), o.get("unit", "")} | {e["label"] for e in o.get("enum", [])}
    italian = {s: po[s] for s in sorted(wanted) if s in po}
    if "Shift+" in po:  # the modifier's own name, as KeyChord::modifier_name strips the catalogue's "Shift+"
        italian[KEY_CTX + CTX + "Shift"] = po["Shift+"].rstrip("+ ")

    OUT.mkdir(parents=True, exist_ok=True)
    source = {
        "source": f"https://github.com/{conf['repo']}/tree/{tag}",
        "license": "AGPL-3.0",
        "slicer": slicer,
        "tag": tag,
        "commit": commit,
        "note": f"Generated by tools/orca_settings/generate.py from {conf['name']}: do not edit by hand.",
    }
    data = {
        **source,
        "pages": pages,
        "options": {k: {kk: vv for kk, vv in o.items() if kk != "gui"} for k, o in opts.items()},
        "sections": {k: v for k, v in sorted(sections.items()) if k in opts},
        "object_keys": per_object,
        "plate_keys": PLATE_KEYS,
        "shortcuts": keyboard,
        "it": italian,
    }
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    (OUT / conf["layout"]).write_text(text, encoding="utf-8", newline="\n")
    BACKEND_OUT.mkdir(parents=True, exist_ok=True)
    backend = {**source, "options": backend_options(opts, sections)}
    (BACKEND_OUT / f"{slicer}.json").write_text(
        json.dumps(backend, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if not conf["icons"]:
        print(f"{tag}: {sum(len(s) for s in pages.values())} pages, {len(keys)} keys, {len(opts)} definitions")
        return
    icons = OUT / "icons"
    icons.mkdir(exist_ok=True)
    # the listed icons, and the option groups' own ones ("param_layer_height"...)
    group_icons = {g["icon"] for s in pages.values() for p in s for g in p["groups"] if g["icon"]}
    names = list(ICONS) + sorted(n for n in group_icons | enum_icons if (images / f"{n}.svg").is_file())
    for name in names:
        shutil.copyfile(src / "resources" / "images" / f"{name}.svg", icons / f"{name}.svg")
    print(
        f"{tag}: {sum(len(s) for s in pages.values())} pages, {len(keys)} keys, {len(opts)} definitions, "
        f"{len(italian)} Italian strings, {len(names)} icons -> {OUT.relative_to(REPO)}"
    )


if __name__ == "__main__":
    main()
