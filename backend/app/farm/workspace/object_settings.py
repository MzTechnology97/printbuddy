"""An object's and a part's own settings in an edited copy (fork): OrcaSlicer's object and part tabs.

OrcaSlicer keeps them in ``Metadata/model_settings.config`` as ``<metadata key value>`` lines: an object's after
its name (``obj->config``), a part's after its matrix and source data (``volume->config``), each in key order
(bbs_3mf.cpp ``_add_model_config_file_to_archive``). A key there is the object's own: the slicer slices it with
that value instead of the project's. TabPrintObject offers the process keys of PrintObjectConfig and
PrintRegionConfig, TabPrintPart those of PrintRegionConfig, for a part or a modifier.

A copy sets, changes or removes such lines and nothing else: every other byte of the file stays, and the result
is parsed back and compared with the original, element by element, but for the edited keys. A value is checked
as a project value is (``settings._check``), in the project's format; an element list (a speed per extruder) has
one value or the project's count, comma separated as Orca writes it (``opt_serialize``).
"""

from __future__ import annotations

import json
import re
from functools import cache

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pydantic import BaseModel, Field

from backend.app.farm.workspace import settings as ws_settings
from backend.app.farm.workspace.placement import _KEY, _METADATA, _OBJECT, _PART, _VALUE, _attr, _element
from backend.app.utils.xml_prefixes import declared

MAX_EDITS = 2000
PART_TYPES = ("normal_part", "modifier_part")  # ParamsPanel shows TabPrintPart for these; the others have no tab
_SUBTYPE = re.compile(rb"""\ssubtype\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_PART_OPEN = re.compile(rb"<part\b")


class InvalidObjectSetting(ValueError):
    """An edit the copy refuses (the message says which and why)."""


class ObjectSetting(BaseModel):
    """One key of an object (``part_id``: of one of its parts): its new value, or None to drop it (the project's
    value applies again: Orca's reset arrow)."""

    object_id: str = Field(pattern=r"^[0-9]{1,9}$")
    part_id: str | None = Field(default=None, pattern=r"^[0-9]{1,9}$")
    key: str = Field(pattern=r"^[a-z0-9_]{1,80}$")
    value: str | int | float | bool | None = None


@cache
def model_keys() -> tuple[frozenset[str], frozenset[str]]:
    """(object keys, part keys), from OrcaSlicer's data.

    shortcut: Bambu Studio projects are checked against OrcaSlicer's lists (its data predates them), so a key only
    Bambu Studio has is refused; regenerate its data with generate.py when one is needed."""
    data = json.loads((ws_settings.DATA / "orcaslicer.json").read_text(encoding="utf-8"))
    return frozenset(data["object_keys"]), frozenset(data["region_keys"])


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _value_of(tag: bytes) -> str | None:
    """A ``<metadata>`` tag's value as a parser reads it (entities resolved)."""
    try:
        return SafeET.fromstring(tag if tag.endswith(b"/>") else tag[:-1] + b"/>").get("value")
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidObjectSetting(f"a setting's line is not valid XML: {exc}") from exc


def check_filament(new, part: bool, filaments: int) -> str:
    """An object's (part's) ``extruder``: the list's Fila. column and keys 1-9 (ObjectList ``update_filament_in_config``,
    ``set_extruder_for_selected_items``): one of the project's filaments, 1-based; a part or modifier may hold 0,
    "default" (a part prints with its object's filament, a modifier changes none)."""
    text = str(new).strip()
    if isinstance(new, bool) or not text.isdigit():
        raise InvalidObjectSetting(f"extruder: {text!r} is not a filament number")
    if not (0 if part else 1) <= int(text) <= filaments:
        raise InvalidObjectSetting(f"extruder: the project has no filament {int(text)} (1 to {filaments})")
    return str(int(text))


def check_value(key: str, new, own: str | None, project, part: bool, slicer: str) -> str:
    """``new`` as the copy writes it for an object (``part``: a part), in the format of its own value ``own`` or
    else the project's; ``InvalidObjectSetting`` with the reason when it can't be one."""
    objects, regions = model_keys()
    option = ws_settings.options(slicer).get(key)
    if option is None or option.get("section") != "process" or key not in (regions if part else objects):
        raise InvalidObjectSetting(f"{key} is not a setting {'a part' if part else 'an object'} can have of its own")
    if key in ws_settings.IDENTITY or key in ws_settings.HARDWARE or option.get("readonly"):
        raise InvalidObjectSetting(f"{key} is read only")
    vector = bool(option.get("vector")) and ws_settings.kind(option) != "string"
    if isinstance(new, bool):
        new = "1" if new else "0"
    news = [e.strip() for e in str(new).split(",")] if vector else [str(new)]
    project_elements = ws_settings._elements(project) or []
    if vector and project_elements and len(news) not in (1, len(project_elements)):
        raise InvalidObjectSetting(f"{key}: expected 1 or {len(project_elements)} values")
    refs = (own.split(",") if vector else [own]) if own is not None else project_elements or [""]
    out = []
    for i, n in enumerate(news):
        old = refs[i] if i < len(refs) else refs[0]
        if vector and n == ws_settings.NIL and option.get("nullable"):
            out.append(n)  # an element left to the project's value, as Orca writes an unset extruder
            continue
        try:
            out.append(ws_settings._normalize(option, n, "" if old == ws_settings.NIL else old))
        except ValueError as exc:
            raise InvalidObjectSetting(f"{key}: {exc}") from exc
    if all(v == ws_settings.NIL for v in out):
        raise InvalidObjectSetting(f"{key}: the inherited value can't be set by hand")
    return ",".join(out)


def _structure(settings: bytes) -> tuple[list, dict]:
    """``(everything but the objects' and parts' metadata, that metadata by (object, part))``."""
    try:
        root = SafeET.fromstring(declared(settings))  # Orca's <slic3rpe:text> declares no prefix
    except (SafeET.ParseError, DefusedXmlException) as exc:
        raise InvalidObjectSetting(f"model_settings.config is not valid XML: {exc}") from exc
    rest: list = []
    own: dict[tuple, dict[str, str]] = {}
    for el in root:
        if el.tag != "object":
            rest.append([(e.tag, tuple(sorted(e.attrib.items())), (e.text or "").strip()) for e in el.iter()])
            continue
        oid = el.get("id")
        rest.append(("object", tuple(sorted(el.attrib.items()))))
        for child in el:
            if child.tag == "metadata":
                own.setdefault((oid, None), {})[child.get("key")] = child.get("value")
            elif child.tag == "part":
                rest.append(("part", oid, tuple(sorted(child.attrib.items()))))
                for sub in child:
                    if sub.tag == "metadata":
                        own.setdefault((oid, child.get("id")), {})[sub.get("key")] = sub.get("value")
                    else:
                        rest.append([(x.tag, tuple(sorted(x.attrib.items()))) for x in sub.iter()])
            else:
                rest.append([(x.tag, tuple(sorted(x.attrib.items()))) for x in child.iter()])
    return rest, own


def _line(settings: bytes, start: int, end: int) -> tuple[int, int]:
    """The tag's whole line, its indentation and line break included (just the tag when it shares its line)."""
    line_start = settings.rfind(b"\n", 0, start) + 1
    if settings[line_start:start].strip():
        return start, end
    if settings[end : end + 2] == b"\r\n":
        return line_start, end + 2
    return line_start, end + 1 if settings[end : end + 1] == b"\n" else end


def _scope(settings: bytes, e: ObjectSetting) -> tuple[int, int, int]:
    """(where the element's opening tag starts, where its children start, where its own metadata ends)."""
    try:
        o_start, o_end = _element(settings, _OBJECT, b"</object>", e.object_id, 0, len(settings))
        if e.part_id is None:
            first_part = _PART_OPEN.search(settings, o_start + 1, o_end)
            return o_start, settings.find(b">", o_start) + 1, first_part.start() if first_part else o_end
        p_start, p_end = _element(settings, _PART, b"</part>", e.part_id, o_start, o_end)
    except InvalidObjectSetting:
        raise
    except ValueError as exc:  # placement's InvalidPlacement: no such object or part
        raise InvalidObjectSetting(str(exc)) from exc
    open_end = settings.find(b">", p_start) + 1
    subtype = _attr(settings[p_start:open_end], _SUBTYPE) or "normal_part"
    if subtype not in PART_TYPES:
        raise InvalidObjectSetting(f"part {e.part_id} ({subtype}) has no settings of its own")
    if b'key="precise_seam_type"' in settings[open_end:p_end]:
        # a Precise Seam keeps its settings dormant (bbs_3mf ``precise_seam_config:``): it has no tab
        raise InvalidObjectSetting(f"part {e.part_id} is a Precise Seam: it has no settings of its own")
    return p_start, open_end, p_end


def apply(settings: bytes, cfg: dict, edits: list[ObjectSetting], slicer: str) -> tuple[bytes, list[dict]]:
    """``model_settings.config`` with the edits made (each checked), and what changed: ``{key: "object_setting",
    object_id, part_id, setting, before, after}`` (None: not its own)."""
    if len(edits) > MAX_EDITS:
        raise InvalidObjectSetting(f"too many object settings at once (at most {MAX_EDITS})")
    known = ws_settings.options(slicer)
    rest_before, own_before = _structure(settings)
    expected = {k: dict(v) for k, v in own_before.items()}
    newline = b"\r\n" if b"\r\n" in settings else b"\n"
    seen: set[tuple] = set()
    applied = []
    for e in edits:
        target = (e.object_id, e.part_id)
        if (*target, e.key) in seen:
            raise InvalidObjectSetting(f"{e.key} of object {e.object_id} is set twice")
        seen.add((*target, e.key))
        open_start, start, end = _scope(settings, e)
        metas = list(_METADATA.finditer(settings, start, end))
        mine = [m for m in metas if _attr(m.group(0), _KEY) == e.key]
        if len(mine) > 1:
            raise InvalidObjectSetting(f"{e.key} is written twice on object {e.object_id}")
        own = _value_of(mine[0].group(0)) if mine else None
        if e.value is None:
            if not mine:
                continue
            a, b = _line(settings, mine[0].start(), mine[0].end())
            settings, after = settings[:a] + settings[b:], None
        else:
            after = (
                check_filament(e.value, e.part_id is not None, len(cfg.get("filament_colour") or [None]))
                if e.key == "extruder"
                else check_value(e.key, e.value, own, cfg.get(e.key), e.part_id is not None, slicer)
            )
            if after == own:
                continue
            attr = f' value="{_escape(after)}"'.encode()
            if mine:
                tag = mine[0].group(0)
                v = _VALUE.search(tag)
                settings = (
                    settings[: mine[0].start()] + tag[: v.start()] + attr + tag[v.end() :] + settings[mine[0].end() :]
                )
            else:
                tag = b'<metadata key="' + e.key.encode("ascii") + b'"' + attr + b"/>"
                # in key order among the config keys there, as Orca writes them; else after the element's last line
                later = [
                    m
                    for m in metas
                    if (_attr(m.group(0), _KEY) or "") in known and (_attr(m.group(0), _KEY) or "") > e.key
                ]
                if later:
                    a, _ = _line(settings, later[0].start(), later[0].end())
                    settings = settings[:a] + settings[a : later[0].start()] + tag + newline + settings[a:]
                elif metas:
                    last = metas[-1]
                    a, b = _line(settings, last.start(), last.end())
                    indent = settings[a : last.start()] if a < last.start() else b""
                    if b > last.end():  # the line ends there: a line of its own after it
                        settings = settings[:b] + indent + tag + newline + settings[b:]
                    else:
                        settings = settings[: last.end()] + newline + indent + tag + settings[last.end() :]
                else:
                    indent = settings[settings.rfind(b"\n", 0, open_start) + 1 : open_start] + b"  "
                    settings = settings[:start] + newline + indent + tag + settings[start:]
        applied.append(
            {
                "key": "object_setting",
                "object_id": e.object_id,
                "part_id": e.part_id,
                "setting": e.key,
                "before": own,
                "after": after,
            }
        )
        if after is None:
            expected.setdefault(target, {}).pop(e.key, None)
        else:
            expected.setdefault(target, {})[e.key] = after
    rest_after, own_after = _structure(settings)
    if rest_after != rest_before or {k: v for k, v in own_after.items() if v} != {
        k: v for k, v in expected.items() if v
    }:
        raise InvalidObjectSetting("model_settings.config changed beyond the settings edited")
    return settings, applied
