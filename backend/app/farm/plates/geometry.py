"""Geometric fingerprint of the objects in a project 3MF: the same piece is recognised whatever its name.

A fingerprint is ``[volume, area, I1, I2, I3, hand]``: volume (mm³) and surface (mm²)
of the printed parts, the principal moments of inertia (sorted) and the handedness
(+1/-1, 0 when mirror-symmetric). None of these change with position, rotation or
number of copies; the handedness tells a left piece from its mirrored right one.

Measured on the farm's 3MF files (2026-11): 689 object names were ~200 pieces;
re-exports of one piece differ by < 0.3%, distinct pieces from 0.5% up.
"""

from __future__ import annotations

import re
import zipfile

import defusedxml.ElementTree as ET
import numpy as np
import trimesh

MAIN_MODEL = "3D/3dmodel.model"
MODEL_SETTINGS = "Metadata/model_settings.config"
TOLERANCE = 0.003
# Below this normalised skewness an axis is symmetric and the handedness is unknown (0).
_SYMMETRIC_SKEW = 0.003
_MAX_DEPTH = 8


def _tag(e) -> str:
    return e.tag.rsplit("}", 1)[-1]


def _attr(e, name: str) -> str | None:
    for k, v in e.attrib.items():
        if k.rsplit("}", 1)[-1] == name:
            return v
    return None


def _transform(s: str | None) -> tuple[np.ndarray, np.ndarray]:
    if not s:
        return np.eye(3), np.zeros(3)
    m = np.array([float(x) for x in s.split()])
    return m[:9].reshape(3, 3), m[9:12]


def _read_models(zf: zipfile.ZipFile) -> dict[str, tuple[dict, object]]:
    """``{"/3D/x.model": ({object id: ("mesh", V, F) | ("comp", [(path, id, A, t)])}, root)}``"""
    models = {}
    for name in zf.namelist():
        if not name.lower().endswith(".model"):
            continue
        path = "/" + name
        root = ET.fromstring(zf.read(name))
        objs: dict = {}
        for res in root:
            if _tag(res) != "resources":
                continue
            for obj in res:
                if _tag(obj) != "object":
                    continue
                for child in obj:
                    if _tag(child) == "mesh":
                        V: list = []
                        F: list = []
                        for part in child:
                            if _tag(part) == "vertices":
                                V = [(float(v.get("x")), float(v.get("y")), float(v.get("z"))) for v in part]
                            elif _tag(part) == "triangles":
                                F = [(int(t.get("v1")), int(t.get("v2")), int(t.get("v3"))) for t in part]
                        objs[obj.get("id")] = ("mesh", np.array(V, dtype=float), np.array(F, dtype=np.int64))
                    elif _tag(child) == "components":
                        objs[obj.get("id")] = (
                            "comp",
                            [(_attr(c, "path") or path, c.get("objectid"), *_transform(c.get("transform"))) for c in child],
                        )
        models[path] = (objs, root)
    return models


def _collect(models: dict, path: str, oid: str, A, t, out: list, depth: int = 0) -> None:
    node = models.get(path, ({}, None))[0].get(oid)
    if node is None or depth > _MAX_DEPTH:
        return
    if node[0] == "mesh":
        if len(node[1]) and len(node[2]):
            out.append((node[1] @ A + t, node[2]))
        return
    for cpath, coid, cA, ct in node[1]:
        _collect(models, cpath, coid, cA @ A, ct @ A + t, out, depth + 1)


def _handedness(V: np.ndarray, F: np.ndarray) -> int:
    """Sign of the principal frame oriented by the third moments of the surface (mirror flips it)."""
    tri = V[F]
    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1) / 2
    if area.sum() <= 0:
        return 0
    w = area / area.sum()
    d = tri.mean(axis=1)
    d = d - (d * w[:, None]).sum(axis=0)
    cov = (d * w[:, None]).T @ d
    variances, axes = np.linalg.eigh(cov)
    skew = (w[:, None] * (d @ axes) ** 3).sum(axis=0) / np.sqrt(np.maximum(variances, 1e-12)) ** 3
    if np.min(np.abs(skew)) < _SYMMETRIC_SKEW:
        return 0
    return int(np.sign(np.linalg.det(axes * np.sign(skew))))


def fingerprint(V: np.ndarray, F: np.ndarray) -> list[float]:
    mesh = trimesh.Trimesh(V, F, process=False)
    volume = abs(float(mesh.volume))
    moments = sorted(abs(float(x)) for x in np.linalg.eigvalsh(mesh.moment_inertia)) if volume > 0 else [0.0] * 3
    return [volume, float(mesh.area), *moments, float(_handedness(V, F))]


def object_fingerprints(zf: zipfile.ZipFile, names: dict[str, str]) -> dict[str, list[float]]:
    """Fingerprint per object name (first object of each name), printed parts only.

    ``names`` maps the build's object ids to names (from model_settings). Modifiers,
    negative volumes and support blockers/enforcers are not printed, so they don't count.
    """
    models = _read_models(zf)
    if "/" + MAIN_MODEL not in models:
        return {}
    subtypes: dict[str, dict[str, str]] = {}
    if MODEL_SETTINGS in zf.namelist():
        root = ET.fromstring(re.sub(rb"<(/?)([A-Za-z_][\w.-]*):", rb"<\1\2_", zf.read(MODEL_SETTINGS)))
        for obj in root.findall("object"):
            subtypes[obj.get("id", "")] = {p.get("id"): p.get("subtype") or "normal_part" for p in obj.findall("part")}

    objs, main_root = models["/" + MAIN_MODEL]
    out: dict[str, list[float]] = {}
    for build in (e for e in main_root if _tag(e) == "build"):
        for item in build:
            oid = item.get("objectid")
            name = names.get(oid)
            node = objs.get(oid)
            if name is None or name in out or node is None:
                continue
            A, t = _transform(item.get("transform"))  # scale counts: a scaled piece is another piece
            meshes: list = []
            if node[0] == "comp":
                for cpath, coid, cA, ct in node[1]:
                    if subtypes.get(oid, {}).get(coid, "normal_part") == "normal_part":
                        _collect(models, cpath, coid, cA @ A, ct @ A + t, meshes)
            else:
                _collect(models, "/" + MAIN_MODEL, oid, A, t, meshes)
            if meshes:
                offsets = np.cumsum([0] + [len(V) for V, _ in meshes[:-1]])
                out[name] = fingerprint(np.vstack([V for V, _ in meshes]),
                                        np.vstack([F + off for (_, F), off in zip(meshes, offsets)]))
    return out


def group(fingerprints: list[list[float]], tolerance: float = TOLERANCE) -> list[list[int]]:
    """Indexes of the same piece together (chained matches), largest groups first."""
    parent = list(range(len(fingerprints)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(fingerprints)), key=lambda i: fingerprints[i][0])
    for n, i in enumerate(order):  # by volume: only neighbours within the tolerance can match
        for j in order[n + 1 :]:
            if fingerprints[j][0] > fingerprints[i][0] * (1 + tolerance) + 1e-9:
                break
            if same_piece(fingerprints[i], fingerprints[j], tolerance):
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(fingerprints)):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


def same_piece(a: list[float] | None, b: list[float] | None, tolerance: float = TOLERANCE) -> bool:
    """Same geometry within ``tolerance`` (relative, per measure) and not mirrored."""
    if not a or not b or len(a) != 6 or len(b) != 6:
        return False
    if a[5] and b[5] and a[5] != b[5]:
        return False
    return all(abs(x - y) <= tolerance * max(abs(x), abs(y), 1e-9) for x, y in zip(a[:5], b[:5]))
