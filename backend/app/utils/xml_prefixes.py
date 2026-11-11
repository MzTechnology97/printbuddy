"""XML that uses a namespace prefix it never declares, made readable by a namespace-aware parser (fork).

OrcaSlicer writes an embossed-text or SVG part's settings into ``Metadata/model_settings.config`` as
``<slic3rpe:text …>`` / ``<slic3rpe:shape …>`` without declaring ``slic3rpe`` (bbs_3mf.cpp ``TEXT_TAG``,
``SHAPE_TAG``); its own reader ignores namespaces and opens the file. ElementTree refuses it ("unbound prefix").
``declared`` adds the missing declarations on the root element, nothing else, so it parses; the prefix is also
registered, so a file written back with ElementTree keeps ``slic3rpe:text`` as OrcaSlicer reads it.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

URN = "urn:printbuddy:undeclared:"  # the namespace an undeclared prefix gets: its name, never a real URI
_ROOT = re.compile(rb"<(?![?!])[A-Za-z_][\w.:-]*")
_TAG_PREFIX = re.compile(rb"</?([A-Za-z_][\w.-]*):[A-Za-z_]")
_ATTR_PREFIX = re.compile(rb"\s([A-Za-z_][\w.-]*):[A-Za-z_][\w.-]*\s*=")
_DECLARED = re.compile(rb"\bxmlns:([A-Za-z_][\w.-]*)\s*=")


def declared(data: bytes) -> bytes:
    """``data`` with every prefix it uses and doesn't declare declared on its root element (the same bytes when
    there is none)."""
    used = set(_TAG_PREFIX.findall(data)) | set(_ATTR_PREFIX.findall(data))
    missing = sorted(used - set(_DECLARED.findall(data)) - {b"xml", b"xmlns"})
    root = _ROOT.search(data)
    if not missing or root is None:
        return data
    for prefix in missing:
        ET.register_namespace(prefix.decode(), URN + prefix.decode())
    names = b"".join(b' xmlns:%s="%s%s"' % (p, URN.encode(), p) for p in missing)
    return data[: root.end()] + names + data[root.end() :]
