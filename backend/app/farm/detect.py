"""Read-only inspection of project 3MF files: integrity, saving slicer, printer model."""

from __future__ import annotations

import json
import re
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from backend.app.utils.printer_models import normalize_printer_model

MODEL_ENTRY = "3D/3dmodel.model"
PROJECT_SETTINGS_ENTRY = "Metadata/project_settings.config"

# Values match the upstream ``preferred_slicer`` setting where a sidecar exists.
SLICER_ORCA = "orcaslicer"
SLICER_BAMBU = "bambu_studio"
SLICER_CREALITY = "creality_print"  # no longer used: excluded from production
SLICER_CAD = "cad"  # CAD export, no slicer settings
SLICER_UNKNOWN = "unknown"
SLICEABLE = (SLICER_ORCA, SLICER_BAMBU)

# Profiles whose printer_model doesn't name the real machine.
# shortcut: hard-coded, becomes the phase-2 "3MF printer -> Printbuddy model" table.
PRINTER_MODEL_ALIASES = {"Voron Voron 2.4 350": "Sovol SV08"}

_METADATA_RE = re.compile(rb'<metadata\s+name="([^"]+)"[^>]*?(?:/>|>([^<]*)</metadata>)')
# The model header is tiny; this cap only bounds pathological files.
_HEADER_LIMIT = 1024 * 1024


class CorruptFileError(Exception):
    """The file is not a readable 3MF (bad zip or missing model)."""


@dataclass(slots=True)
class ProjectInfo:
    application: str | None
    slicer: str
    printer_model_raw: str | None
    printer_model: str | None


def _read_header_metadata(zf: zipfile.ZipFile) -> dict[str, str]:
    """``<metadata>`` elements before ``<resources>``, without reading the whole mesh."""
    buf = b""
    with zf.open(MODEL_ENTRY) as fh:
        while len(buf) < _HEADER_LIMIT:
            chunk = fh.read(64 * 1024)
            if not chunk:
                break
            buf += chunk
            if b"<resources" in buf:
                break
    buf = buf.split(b"<resources", 1)[0]
    # The first of a repeated name wins: the Orca CLI writes its own ``OrcaSlicer`` tag and keeps the project's after it.
    return {
        name.decode("utf-8", "replace"): (value or b"").decode("utf-8", "replace").strip()
        for name, value in reversed(_METADATA_RE.findall(buf))
    }


def read_project_settings(fh) -> dict:
    """``Metadata/project_settings.config`` as a dict ({} when the 3MF has none, e.g. CAD exports)."""
    with zipfile.ZipFile(fh) as zf:
        if PROJECT_SETTINGS_ENTRY not in zf.namelist():
            return {}
        try:
            cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
    return cfg if isinstance(cfg, dict) else {}


def detect_slicer(metadata: dict[str, str]) -> str:
    """Which slicer saved the project. Rule verified on the farm's NAS files.

    Orca 2.4+ writes ``Application=BambuStudio-02.xx`` for compatibility, so the
    ``OrcaSlicer`` tag wins over the Application value.
    """
    app = metadata.get("Application", "")
    if app.startswith("Creality_Print"):
        return SLICER_CREALITY
    if "OrcaSlicer" in metadata or app.startswith("OrcaSlicer"):
        return SLICER_ORCA
    if re.match(r"BambuStudio-[1-9]\d*\.", app):  # Orca 2.3: "BambuStudio-2.3.0"
        return SLICER_ORCA
    if app.startswith("BambuStudio-0"):  # Bambu Studio: "BambuStudio-02.06.00.51"
        return SLICER_BAMBU
    if not app:
        return SLICER_CAD
    return SLICER_UNKNOWN


def slicer_version(metadata: dict[str, str], slicer: str | None = None) -> str | None:
    """The version of the slicer that wrote the 3MF: Orca's own ``OrcaSlicer`` tag (Orca 2.4+ writes a Bambu Studio
    Application for compatibility), else the Application's: "BambuStudio-02.08.02.61" -> "02.08.02.61". With
    ``slicer`` Bambu Studio, an Orca tag kept from the project is not its own."""
    if slicer != SLICER_BAMBU and metadata.get("OrcaSlicer"):
        return metadata["OrcaSlicer"]
    found = re.match(r"(?:OrcaSlicer|BambuStudio)[-_ ]?v?(\d\S*)", metadata.get("Application", ""))
    return found.group(1) if found else None


def printer_model_from_settings(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    return normalize_printer_model(PRINTER_MODEL_ALIASES.get(raw, raw))


def inspect_3mf(path: Path) -> ProjectInfo:
    """Integrity check plus slicer / printer detection. Raises CorruptFileError."""
    try:
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            if MODEL_ENTRY not in names:
                raise CorruptFileError(f"missing {MODEL_ENTRY}")
            metadata = _read_header_metadata(zf)
            printer_raw = None
            if PROJECT_SETTINGS_ENTRY in names:
                try:
                    cfg = json.loads(zf.read(PROJECT_SETTINGS_ENTRY).decode("utf-8"))
                    if isinstance(cfg, dict) and cfg.get("printer_model"):
                        printer_raw = str(cfg["printer_model"]).strip()
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass  # CAD exports / partial settings: no printer, file still usable
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError, KeyError, zlib.error) as exc:
        raise CorruptFileError(f"invalid zip: {exc}") from exc
    orca = metadata.get("OrcaSlicer")  # Orca's own version: the Application of Orca 2.4+ names a Bambu Studio release
    return ProjectInfo(
        application=f"OrcaSlicer-{orca}" if orca else metadata.get("Application") or None,
        slicer=detect_slicer(metadata),
        printer_model_raw=printer_raw,
        printer_model=printer_model_from_settings(printer_raw),
    )
