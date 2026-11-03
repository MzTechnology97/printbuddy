"""Rebuild backend/app/farm/plan/filament_catalog.json: the filaments each farm printer model can use.

Reads, without changing anything, the OrcaSlicer and Bambu Studio profiles on this PC: the system ones
(OrcaSlicer 2.4 keeps them in binary ``system/*.opc`` packs, Bambu Studio in ``system/BBL/**.json``) and
the user ones (``user/<account>/filament|machine/*.json``). A filament belongs to a model when one of its
``compatible_printers`` (own or inherited) is a printer preset whose ``printer_model`` is that model.

    python scripts/farm_filament_catalog.py            # Windows, profiles under %APPDATA%
    python scripts/farm_filament_catalog.py --appdata /path/to/Roaming
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import struct
from collections import Counter
from pathlib import Path

# Farm printer models, as the slicers' printer_model (and the 3MF) name them.
MODELS = [
    "Bambu Lab X2D", "Bambu Lab X1 Carbon", "Bambu Lab H2D", "Bambu Lab P1S", "Bambu Lab A1", "Bambu Lab P2S",
    "Creality K1", "Creality K2 Pro", "Sovol SV08", "Sovol Zero",
]  # fmt: skip
ORCA_PACKS = ["BBL", "Creality", "Sovol", "OrcaFilamentLibrary"]
OUT = Path(__file__).resolve().parent.parent / "backend/app/farm/plan/filament_catalog.json"


# --- OrcaSlicer .opc packs ("ZCRO" v2), layout worked out from the files: little endian, strings are
# u64 length + UTF-8; a header with the key and enum-string tables, then records "name, path, fields".
class _Reader:
    def __init__(self, b: bytes, p: int = 0) -> None:
        self.b, self.p = b, p

    def u(self, fmt: str):
        v = struct.unpack_from(fmt, self.b, self.p)[0]
        self.p += struct.calcsize(fmt)
        return v

    def str(self) -> str:
        n = self.u("<Q")
        v = self.b[self.p : self.p + n]
        if len(v) != n:
            raise struct.error("string past the end")
        self.p += n
        return v.decode("utf-8", "replace")


_SCALAR = {1: "<d", 2: "<i", 4: "<d", 8: "<B"}  # float, int, percent, bool
_PATH = re.compile(rb"(filament|machine|process)/[^\x00-\x1f]{1,200}?\.json")
_TRAILER = (False, True, False, False, False, False, True)  # inherits, [parents], ?, instantiation, id, ?, [aliases]


def _value(r: _Reader, t: int):
    vec, base = t & 0x4000, t & 0xFF
    if base == 9:  # enums: u32 count, per item a u16 index into the enum strings; 0 = a raw i32 follows (nil)
        items = []
        for _ in range(r.u("<I")):
            idx = r.u("<H")
            items.append(-r.u("<i") - 1 if idx == 0 else idx)  # raw values kept apart from string indices
        return items if vec else items[0]

    def one():
        if base == 3:
            return r.str()
        if base == 5:  # float or percent
            v = r.u("<d")
            return f"{v:g}%" if r.u("<B") else v
        if base in (6, 7):  # point, point3
            return tuple(r.u("<d") for _ in range(base - 4))
        return r.u(_SCALAR[base])

    return [one() for _ in range(r.u("<Q"))] if vec else one()


def _trailer(r: _Reader) -> list:
    return [[r.str() for _ in range(r.u("<Q"))] if vec else r.str() for vec in _TRAILER]


def opc_presets(b: bytes) -> list[dict]:
    """Every preset of a pack, with its fields; records that don't decode (a few machine ones) are skipped."""
    r = _Reader(b, 0x18)
    r.str(), r.str()  # vendor, version
    keys = [r.str() for _ in range(r.u("<Q"))]
    if r.u("<Q") != len(keys):
        raise ValueError("unexpected .opc layout")
    types = [r.u("<H") for _ in keys]
    enums = [r.str() for _ in range(r.u("<Q"))]
    out = []
    for m in _PATH.finditer(b):
        start = m.start() - 8
        if start < 0 or struct.unpack_from("<Q", b, start)[0] != m.end() - m.start():
            continue
        # The preset's own name sits right before its path (file names drop the '/' of "PA/PET").
        name = next(
            (b[start - n : start].decode("utf-8", "replace") for n in range(1, 400)
             if start - n - 8 >= 0 and struct.unpack_from("<Q", b, start - n - 8)[0] == n),
            None,
        )  # fmt: skip
        r = _Reader(b, m.end())
        try:
            fields = {}
            for _ in range(r.u("<I")):
                k = r.u("<H")
                v = _value(r, types[k])
                if types[k] & 0xFF == 9:
                    v = [enums[i] if i > 0 else None for i in v] if isinstance(v, list) else enums[v]
                fields[keys[k]] = v
            trailer = _trailer(r)
        except (struct.error, IndexError, KeyError, OverflowError, MemoryError):
            continue
        out.append({"kind": m.group(1).decode(), "name": name or m.group().decode().split("/", 1)[1][:-5],
                    "inherits": trailer[0], "instantiation": trailer[3], "aliases": trailer[6], **fields})  # fmt: skip
    return out


# --- profiles -> catalog
def _first(v):
    return (v[0] if isinstance(v, list) and v else v) or None


def _json_presets(pattern: str, kind: str, user: bool) -> dict:
    out = {}
    for f in glob.glob(pattern, recursive=True):
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        name = d.get("name") or Path(f).stem
        out[(kind, name)] = {**d, "name": name, "user": user}
    return out


def _resolve(idx: dict, kind: str, name: str, key: str):
    for _ in range(20):  # inherits chain
        p = idx.get((kind, name))
        if not p:
            return None
        if p.get(key) not in (None, [], ""):
            return p[key]
        name = p.get("inherits") or ""
    return None


def _user_sets(root: str, system: dict) -> list[dict]:
    sets = []
    for account in sorted(glob.glob(f"{root}/user/*/")):
        user = {}
        for kind in ("filament", "machine"):
            user.update(_json_presets(f"{account}{kind}/**/*.json", kind, True))
        sets.append({**system, **user})
    return sets or [system]


def build(appdata: str) -> dict[str, list[dict]]:
    orca = {}
    for pack in ORCA_PACKS:
        path = Path(appdata, "OrcaSlicer/system", f"{pack}.opc")
        for p in opc_presets(path.read_bytes()) if path.exists() else []:
            orca[(p["kind"], p["name"])] = {**p, "user": False}
            for alias in p["aliases"]:  # old names ("Creality Generic ABS") that user presets still inherit
                orca.setdefault((p["kind"], alias), orca[(p["kind"], p["name"])])
    bambu = {}
    for kind in ("filament", "machine"):
        bambu.update(_json_presets(f"{appdata}/BambuStudio/system/BBL/{kind}/**/*.json", kind, False))

    rows = {}
    for slicer, sets in (
        ("OrcaSlicer", _user_sets(f"{appdata}/OrcaSlicer", orca)),
        ("Bambu Studio", _user_sets(f"{appdata}/BambuStudio", bambu)),
    ):
        for idx in sets:
            model_of = {n: _first(_resolve(idx, "machine", n, "printer_model")) for k, n in idx if k == "machine"}
            for (kind, name), p in idx.items():
                if kind != "filament" or p["name"] != name:  # alias keys only serve inherits
                    continue
                if not p["user"] and str(p.get("instantiation")).lower() != "true":
                    continue  # @base / templates, not offered in the slicer
                compat = _resolve(idx, kind, name, "compatible_printers") or []
                compat = [compat] if isinstance(compat, str) else compat
                for model in MODELS:
                    hits = {c for c in compat if model_of.get(c) == model}
                    if not hits:
                        continue
                    vendor = (_first(_resolve(idx, kind, name, "filament_vendor")) or "").strip()
                    ftype = _first(_resolve(idx, kind, name, "filament_type")) or ""
                    e = rows.setdefault((model, vendor, name.split(" @")[0], ftype), {
                        "sources": set(), "slicers": set(), "presets": set(), "compatible_printers": set()})  # fmt: skip
                    e["sources"].add("user" if p["user"] else "system")
                    e["slicers"].add(slicer)
                    e["presets"].add(name)
                    e["compatible_printers"] |= hits
    return _catalog(rows)


def _catalog(rows: dict) -> dict[str, list[dict]]:
    # One spelling per vendor ("Bambulab", "BambuLAB" -> "Bambu Lab"): the most used one.
    def squash(v: str) -> str:
        k = re.sub(r"\W", "", v.casefold())
        return {"quidi": "qidi"}.get(k, k)

    best = {}
    for v, _ in Counter(v for (_, v, _, _) in rows).most_common():
        best.setdefault(squash(v), v)
    out: dict[str, dict] = {m: {} for m in MODELS}
    for (model, vendor, filament, ftype), e in rows.items():
        vendor = best[squash(vendor)]
        merged = out[model].setdefault((vendor.casefold(), filament.casefold(), ftype), {
            "vendor": vendor, "filament": filament, "type": ftype, **{k: set() for k in e}})  # fmt: skip
        for k, v in e.items():
            merged[k] |= v
    return {m: [{k: sorted(v) if isinstance(v, set) else v for k, v in e.items()} for _, e in sorted(entries.items())]
            for m, entries in out.items()}  # fmt: skip


def dump(catalog: dict[str, list[dict]]) -> str:
    """One filament per line, so a rebuild reads as a short diff."""
    blocks = []
    for model, entries in catalog.items():
        lines = ",\n".join("  " + json.dumps(e, ensure_ascii=False) for e in entries)
        blocks.append(f" {json.dumps(model)}: [\n{lines}\n ]")
    return "{\n" + ",\n".join(blocks) + "\n}\n"


def _self_check() -> None:
    """A made-up pack with one filament record round-trips through the decoder."""

    def s(t: str) -> bytes:
        return struct.pack("<Q", len(t.encode())) + t.encode()

    keys = ["compatible_printers", "filament_type", "nozzle_type"]
    pack = b"ZCRO" + bytes(20) + s("V") + s("1") + struct.pack("<Q", 3) + b"".join(map(s, keys))
    pack += struct.pack("<Q", 3) + struct.pack("<3H", 0x4003, 0x4003, 0x4009) + struct.pack("<Q", 2) + s("") + s("hs")
    pack += s("Generic PLA @X") + s("filament/Generic PLA @X.json") + struct.pack("<I", 3)
    pack += struct.pack("<HQ", 0, 1) + s("X 0.4 nozzle") + struct.pack("<HQ", 1, 1) + s("PLA")
    pack += struct.pack("<HIHHi", 2, 2, 1, 0, 0x7FFFFFFF)
    pack += s("Generic PLA @System") + struct.pack("<Q", 0) + s("") + s("true") + s("id") + s("") + struct.pack("<Q", 0)
    (p,) = opc_presets(pack)
    assert (p["name"], p["inherits"], p["compatible_printers"], p["filament_type"]) == (
        "Generic PLA @X", "Generic PLA @System", ["X 0.4 nozzle"], ["PLA"])  # fmt: skip
    assert p["nozzle_type"] == ["hs", None], p["nozzle_type"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--appdata", default=os.environ.get("APPDATA", ""), help="folder with OrcaSlicer/ and BambuStudio/")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()
    _self_check()
    catalog = build(args.appdata)
    args.out.write_text(dump(catalog), encoding="utf-8")
    print("\n".join(f"{m}: {len(e)} filaments" for m, e in catalog.items()))
