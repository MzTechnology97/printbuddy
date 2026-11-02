"""A small picture of one piece from its mesh: isometric view, flat shading, in the piece's colour."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw

MAX_TRIANGLES = 120_000  # beyond this every n-th triangle is drawn: enough for a thumbnail


def _rgb(color_hex: str | None) -> np.ndarray:
    try:
        rgb = np.array([int(color_hex[i : i + 2], 16) for i in (0, 2, 4)], dtype=float)
    except (TypeError, ValueError, IndexError):
        rgb = np.array([150.0, 160.0, 170.0])
    # Very dark colours (black) would vanish: lift them towards grey, keeping the hue.
    return rgb + (np.array([70.0, 70.0, 70.0]) - rgb).clip(0) * 0.8


def render_png(V: np.ndarray, F: np.ndarray, color_hex: str | None = None, size: int = 256) -> bytes:
    """PNG with a transparent background; the piece fills the picture whatever its size."""
    if len(F) > MAX_TRIANGLES:
        F = F[:: -(-len(F) // MAX_TRIANGLES)]
    # Isometric camera: 45° around Z, then 35° down.
    a, b = np.radians(45), np.radians(35.264)
    rz = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
    rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    P = (V - V.mean(axis=0)) @ rz.T @ rx.T  # x right, y depth, z up
    tri = P[F]
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    length = np.linalg.norm(normals, axis=1)
    keep = length > 0
    tri, normals = tri[keep], normals[keep] / length[keep, None]
    light = np.array([-0.4, -0.6, 0.7])
    light /= np.linalg.norm(light)
    shade = 0.35 + 0.65 * np.abs(normals @ light)  # two-sided: meshes aren't always well oriented
    order = np.argsort(-tri[:, :, 1].mean(axis=1))  # far first (painter's algorithm)

    xy = tri[:, :, [0, 2]] * [1, -1]
    lo, hi = xy.reshape(-1, 2).min(axis=0), xy.reshape(-1, 2).max(axis=0)
    scale = (size * 0.9) / max(float((hi - lo).max()), 1e-9)
    xy = (xy - lo) * scale + (size - (hi - lo) * scale) / 2

    base = _rgb(color_hex)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in order:
        r, g, bl = (base * shade[i]).clip(0, 255).astype(int)
        draw.polygon([tuple(p) for p in xy[i]], fill=(r, g, bl, 255))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
