#!/usr/bin/env python3
"""Build the 3MF Workspace's settings layout and icons from an OrcaSlicer source checkout.

OrcaSlicer (https://github.com/OrcaSlicer/OrcaSlicer) is AGPL-3.0, like Printbuddy. From a checkout of
one release tag this reads:

* ``src/slic3r/GUI/Tab.cpp``: the pages, option groups and option order of the process (TabPrint),
  filament (TabFilament) and printer (TabPrinter) settings, with their icon names (option lists the
  printer pages loop over come from ``src/libslic3r/PublishSettings.cpp``);
* ``src/libslic3r/PrintConfig.cpp``: per option its label, tooltip, unit (sidetext), min/max, enum
  values and labels, and mode (simple / advanced / develop; simple when an option sets none);
* ``localization/i18n/it/OrcaSlicer_it.po``: the Italian strings of those labels and tooltips;
* ``resources/images/*.svg``: the icons listed in ``ICONS`` (never logos), copied unchanged.

It writes ``frontend/src/farm/workspace/orca-assets/`` (``settings-layout.json`` and ``icons/``).
The layout only drives tabs, groups, order, labels and mode: which settings can change and how a value
is checked stays Printbuddy's (``backend/app/farm/workspace/settings.py``).

    git clone --depth 1 --branch v2.5.0 https://github.com/OrcaSlicer/OrcaSlicer /tmp/orca
    python tools/orca_settings/generate.py /tmp/orca v2.5.0

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

# Tab.cpp functions that build each preset's pages
SECTIONS = {
    "process": ["TabPrint::build"],
    "filament": ["TabFilament::build", "TabFilament::add_filament_overrides_page"],
    "printer": ["TabPrinter::build_fff", "TabPrinter::build_kinematics_page", "TabPrinter::build_unregular_pages"],
}
# Pages that aren't settings of the preset itself
SKIP_PAGES = {"Dependencies", "Notes"}
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
]

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
    r"|new_optgroup\(\s*L\((?P<group>" + _STR + r')\)(?:\s*,\s*L?"(?P<group_icon>[^"]*)")?'
    r'|append_single_option_line\(\s*"(?P<k1>[a-zA-Z0-9_]+)"'
    r'|get_option\(\s*"(?P<k2>[a-zA-Z0-9_]+)"'
    r'|append_option_line\(\s*\w+\s*,\s*"(?P<k3>[a-zA-Z0-9_]+)"'
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


def layout(tab_src: str, publish_src: str = "") -> dict[str, list[dict]]:
    """Per preset: its pages, each with its option groups and their keys, in the client's order."""
    src = _strip_comments(tab_src)
    # option lists defined elsewhere: PublishSettings.cpp helpers, std::vector literals in Tab.cpp
    lists = {
        name: [_unescape(s) for s in re.findall(r"\{\s*(" + _STR + r")", items)]
        for name, items in _OPTION_LIST_RE.findall(_strip_comments(publish_src))
    }
    lists |= {name: [_unescape(s) for s in re.findall(_STR, items)] for name, items in _VECTOR_RE.findall(src)}
    out: dict[str, list[dict]] = {}
    for section, functions in SECTIONS.items():
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
                if m["page"]:
                    title = _unescape(m["page"])
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
            {**p, "groups": [g for g in p["groups"] if g["keys"]]}
            for p in pages
            if p["title"] not in SKIP_PAGES and any(g["keys"] for g in p["groups"])
        ]
    return out


_DEF_RE = re.compile(r'def\s*=\s*this->add(?:_nullable)?\(\s*"([a-zA-Z0-9_]+)"\s*,\s*(co\w+)')


def _text(block: str, name: str) -> str | None:
    m = re.search(rf"def->{name}\s*=\s*(?:L\(|_L\(|_u8L\()?((?:\s*{_STR})+)", block)
    return _unescape(m.group(1)) if m else None


def _number(block: str, name: str) -> float | None:
    m = re.search(rf"def->{name}\s*=\s*(-?\d+(?:\.\d+)?)\s*;", block)
    return float(m.group(1)) if m else None


def options(cfg_src: str, keys: set[str]) -> dict[str, dict]:
    """The definitions of ``keys`` in PrintConfig.cpp."""
    src = _strip_comments(cfg_src)
    defs = list(_DEF_RE.finditer(src))
    out: dict[str, dict] = {}
    for i, m in enumerate(defs):
        key = m.group(1)
        if key not in keys or key in out:
            continue
        block = src[m.end() : defs[i + 1].start() if i + 1 < len(defs) else len(src)]
        mode = re.search(r"def->mode\s*=\s*com(\w+)", block)
        values = [_unescape(v) for v in re.findall(rf"def->enum_values\.push_back\(\s*({_STR})", block)]
        labels = [_unescape(v) for v in re.findall(rf"def->enum_labels\.push_back\(\s*(?:L\()?\s*({_STR})", block)]
        entry = {
            "type": m.group(2),
            # full_label: the name out of its row ("Nozzle temperature", not "Other layers")
            "label": _text(block, "full_label") or _text(block, "label") or key,
            "tooltip": _text(block, "tooltip"),
            "unit": _text(block, "sidetext"),
            "min": _number(block, "min"),
            "max": _number(block, "max"),
            "mode": mode.group(1).lower() if mode else "simple",
        }
        if values:
            entry["enum"] = [{"value": v, "label": labels[j] if j < len(labels) else v} for j, v in enumerate(values)]
        out[key] = {k: v for k, v in entry.items() if v is not None}
    # The filament's overrides ("filament_retraction_length") are copies of the printer option, made in a loop.
    for key in sorted(keys - out.keys()):  # sorted: the same file on every run
        if key.startswith("filament_") and key[len("filament_") :] in out:
            out[key] = dict(out[key[len("filament_") :]])
    return out


def _po(text: str) -> dict[str, str]:
    """msgid -> msgstr of a .po file (fuzzy, contextual and empty translations left out)."""
    out: dict[str, str] = {}
    for entry in re.split(r"\n\s*\n", text):
        if "#, fuzzy" in entry or "\nmsgctxt" in entry or entry.startswith("msgctxt"):
            continue
        mid = re.search(r"^msgid\s+((?:" + _STR + r"\s*)+)", entry, re.M)
        mstr = re.search(r"^msgstr\s+((?:" + _STR + r"\s*)+)", entry, re.M)
        if mid and mstr:
            key, value = _unescape(mid.group(1)), _unescape(mstr.group(1))
            if key and value:
                out[key] = value
    return out


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


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise SystemExit(__doc__)
    src, tag = Path(sys.argv[1]), sys.argv[2]
    commit = _commit(src, tag, sys.argv[3] if len(sys.argv) == 4 else None)
    pages = layout(
        (src / "src/slic3r/GUI/Tab.cpp").read_text(encoding="utf-8"),
        (src / "src/libslic3r/PublishSettings.cpp").read_text(encoding="utf-8"),
    )
    keys = {k for section in pages.values() for p in section for g in p["groups"] for k in g["keys"]}
    opts = options((src / "src/libslic3r/PrintConfig.cpp").read_text(encoding="utf-8"), keys)
    po = _po((src / "localization/i18n/it/OrcaSlicer_it.po").read_text(encoding="utf-8"))
    wanted = {p["title"] for s in pages.values() for p in s}
    wanted |= {g["title"] for s in pages.values() for p in s for g in p["groups"]}
    for o in opts.values():
        wanted |= {o["label"], o.get("tooltip", ""), o.get("unit", "")} | {e["label"] for e in o.get("enum", [])}
    italian = {s: po[s] for s in sorted(wanted) if s in po}

    OUT.mkdir(parents=True, exist_ok=True)
    data = {
        "source": f"https://github.com/OrcaSlicer/OrcaSlicer/tree/{tag}",
        "license": "AGPL-3.0",
        "tag": tag,
        "commit": commit,
        "note": "Generated by tools/orca_settings/generate.py from OrcaSlicer: do not edit by hand.",
        "pages": pages,
        "options": opts,
        "it": italian,
    }
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    (OUT / "settings-layout.json").write_text(text, encoding="utf-8", newline="\n")
    icons = OUT / "icons"
    icons.mkdir(exist_ok=True)
    for name in ICONS:
        shutil.copyfile(src / "resources" / "images" / f"{name}.svg", icons / f"{name}.svg")
    print(
        f"{tag}: {sum(len(s) for s in pages.values())} pages, {len(keys)} keys, {len(opts)} definitions, "
        f"{len(italian)} Italian strings, {len(ICONS)} icons -> {OUT.relative_to(REPO)}"
    )


if __name__ == "__main__":
    main()
