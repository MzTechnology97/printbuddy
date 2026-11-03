"""What to print, per component, in Seller Hub's order of urgency.

Seller Hub (``GET {url}/api/print-plan``) says how many pieces of each component to print.
Printbuddy adds the confirmed plates of the component's shape and, for each plate, the
printers of its model: free or not, and whether the plate's material and the component's
colour are loaded. The last good answer from Seller Hub is kept, so the page still works
when Seller Hub is down.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.api.routes.settings import get_setting, set_setting
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.plan.models import FarmPrinterSpool
from backend.app.farm.plates.models import FarmPlate, FarmPlateYield
from backend.app.farm.plates.printer_map import PrinterModelResolver
from backend.app.farm.products.folders import excluded_files
from backend.app.farm.products.models import (
    FarmComponent,
    FarmProduct,
    FarmProductPart,
    FarmShape,
    component_shapes,
    component_yield,
)
from backend.app.models.printer import Printer
from backend.app.models.project import Project
from backend.app.services.print_scheduler import _canonical_filament_type, scheduler
from backend.app.services.printer_manager import printer_manager

# Reference colour of each colour name used for components; "Nero" matches a black spool.
DEFAULT_COLORS = {
    "nero": "111111",
    "bianco": "f5f5f5",
    "grigio": "8a8a8a",
    "marrone scuro": "4a2c1a",
    "marrone": "6b4226",
    "arancio": "f28c28",
    "giallo": "f5d000",
    "verde": "2e9e44",
    "rosso": "d32f2f",
    "blu": "1e5bd8",
    "beige": "d8c3a5",
}
# Largest RGB distance between a loaded spool and the reference colour that still counts as that colour.
DEFAULT_TOLERANCE = 80
URGENCY = {"esaurito": 0, "urgente": 1, "da_stampare": 2}
COMPONENT_FIELDS = ("code", "name", "status", "to_print", "stock", "need", "days_left")
TIMEOUT_SECONDS = 15.0


def _key(name: str) -> str:
    return f"farm_plan_{name}"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def clean_url(raw: str) -> str:
    """``http(s)://host[:port]`` without a trailing slash; ValueError otherwise."""
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if url and (parts.scheme not in ("http", "https") or not parts.hostname or parts.query or parts.fragment):
        raise ValueError("Seller Hub URL must be http(s)://host[:port]")
    return url


def clean_hex(raw: str) -> str:
    value = raw.strip().lstrip("#").lower()[:6]
    if len(value) != 6 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"Invalid colour {raw!r}")
    return value


async def load_config(db: AsyncSession) -> dict:
    colors = dict(DEFAULT_COLORS)
    raw_colors = await get_setting(db, _key("colors"))
    if raw_colors:
        colors.update(json.loads(raw_colors))
    tolerance = await get_setting(db, _key("tolerance"))
    return {
        "url": await get_setting(db, _key("url")) or "",
        "tolerance": int(tolerance) if tolerance else DEFAULT_TOLERANCE,
        "colors": colors,
    }


async def save_config(db: AsyncSession, url: str, tolerance: int, colors: dict[str, str]) -> dict:
    """Store the settings; only colours that differ from the defaults are kept."""
    custom = {name.strip().lower(): clean_hex(hex_) for name, hex_ in colors.items() if name.strip()}
    custom = {name: hex_ for name, hex_ in custom.items() if DEFAULT_COLORS.get(name) != hex_}
    await set_setting(db, _key("url"), clean_url(url))
    await set_setting(db, _key("tolerance"), str(tolerance))
    await set_setting(db, _key("colors"), json.dumps(custom))
    await db.commit()
    return await load_config(db)


async def _get_print_plan(url: str) -> dict:
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        response = await client.get(f"{url}/api/print-plan")
        response.raise_for_status()
        return response.json()


async def fetch_components(db: AsyncSession, url: str) -> tuple[list[dict], str | None, str | None]:
    """Seller Hub's components, when they were read, and an error (the cached copy is used then)."""
    if url:
        try:
            plan = await _get_print_plan(url)
            components = [
                {k: c.get(k) for k in COMPONENT_FIELDS} for c in plan.get("components") or [] if isinstance(c, dict)
            ]
            fetched_at = _now()
            await set_setting(db, _key("cache"), json.dumps({"fetched_at": fetched_at, "components": components}))
            await db.commit()
            return components, fetched_at, None
        except (httpx.HTTPError, ValueError) as exc:
            error = f"Seller Hub did not answer ({type(exc).__name__}: {exc})"
    else:
        error = "Seller Hub URL not set"
    cache = json.loads(await get_setting(db, _key("cache")) or "{}")
    return cache.get("components") or [], cache.get("fetched_at"), error


def color_distance(a: str, b: str) -> float:
    ra, ga, ba = (int(a[i : i + 2], 16) for i in (0, 2, 4))
    rb, gb, bb = (int(b[i : i + 2], 16) for i in (0, 2, 4))
    return math.dist((ra, ga, ba), (rb, gb, bb))


def printer_snapshot(printers: list[Printer]) -> list[dict]:
    """Model, free or not, and loaded filaments of each active printer, from the live status."""
    out = []
    for p in printers:
        status = printer_manager.get_status(p.id)
        loaded = []
        if status is not None and status.raw_data:
            for f in scheduler._build_loaded_filaments(status):
                loaded.append({"type": f["type"], "color": (f["color"] or "").lstrip("#").lower()[:6]})
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "model": p.model or "",
                "idle": scheduler._is_printer_idle(p.id),
                "loaded": loaded,
            }
        )
    return out


async def with_spools_set_by_hand(db: AsyncSession, snapshot: list[dict]) -> list[dict]:
    """Printers reporting no filament (Klipper without CFS) get the spool the user said is on them."""
    spools = {s.printer_id: s for s in (await db.execute(select(FarmPrinterSpool))).scalars()}
    return [
        {**p, "loaded": [{"type": spools[p["id"]].material, "color": spools[p["id"]].color}], "by_hand": True}
        if p["id"] in spools and not p["loaded"]
        else p
        for p in snapshot
    ]


def printer_fit(printer: dict, materials: list[str], color_hex: str | None, tolerance: int) -> dict:
    """Is the plate's material loaded, and a spool of the component's colour in that material?"""
    wanted = {_canonical_filament_type(m) for m in materials if m}
    usable = (
        [f for f in printer["loaded"] if _canonical_filament_type(f["type"]) in wanted] if wanted else printer["loaded"]
    )
    if not printer["loaded"]:
        material_ok = color_ok = None  # single-spool printers without slots: not readable
    else:
        material_ok = bool(usable) and all(
            any(_canonical_filament_type(f["type"]) == m for f in printer["loaded"]) for m in wanted
        )
        color_ok = (
            None
            if color_hex is None
            else any(f["color"] and color_distance(f["color"], color_hex) <= tolerance for f in usable)
        )
    return {
        "id": printer["id"],
        "name": printer["name"],
        "idle": printer["idle"],
        "material_ok": material_ok,
        "color_ok": color_ok,
        "by_hand": printer.get("by_hand", False),
    }


async def _confirmed_plates(db: AsyncSession, shape_ids: set[int], codes: set[str]) -> list[FarmPlate]:
    if not shape_ids and not codes:
        return []
    query = select(FarmPlate).where(
        FarmPlate.status == "confirmed",
        FarmPlate.yields.any(FarmPlateYield.shape_id.in_(shape_ids) | FarmPlateYield.component_code.in_(codes)),
        FarmPlate.library_file_id.notin_(await excluded_files(db)),
    )
    return list((await db.execute(query)).scalars())


async def build_plan(db: AsyncSession, queued: dict[str, int] | None = None, codes: list[str] | None = None) -> dict:
    """Components to print (``to_print > 0``), most urgent first, each with its ranked plates.

    ``queued``: pieces per component code already in the queue or printing (from Accoda).
    ``codes``: these components instead, whatever Seller Hub asks (printing a product by hand).
    """
    config = await load_config(db)
    components, fetched_at, error = await fetch_components(db, config["url"])
    if codes is None:
        wanted = [c for c in components if isinstance(c.get("to_print"), int) and c["to_print"] > 0]
    else:
        hub = {c.get("code"): c for c in components}
        wanted = [hub.get(code) or {"code": code} for code in dict.fromkeys(codes)]
    ours = {
        c.code: c
        for c in (await db.execute(select(FarmComponent).where(FarmComponent.code.in_([w["code"] for w in wanted]))))
        .scalars()
        .all()
    }
    plates = await _confirmed_plates(
        db,
        {s for c in ours.values() for s in component_shapes(c)},
        {c.code for c in ours.values() if not component_shapes(c)},
    )
    paths = dict(
        (
            await db.execute(
                select(FarmLibraryFile.library_file_id, FarmLibraryFile.path).where(
                    FarmLibraryFile.library_file_id.in_({p.library_file_id for p in plates})
                )
            )
        ).all()
    )
    shape_names = dict((await db.execute(select(FarmShape.id, FarmShape.name))).all())
    # The products using each component, colour variants included (a variant takes the component of each
    # shaped part in its colour): their names and covers help tell the piece apart from its title.
    by_shape = {(c.shape_id, (c.color or "").lower()): c.id for c in ours.values() if c.shape_id}
    used_in: dict[int, list[dict]] = {}
    for product, name, cover, component_id, shape_id in await db.execute(
        select(FarmProduct, Project.name, Project.cover_image_filename, FarmComponent.id, FarmComponent.shape_id)
        .join(FarmProductPart, FarmProductPart.project_id == FarmProduct.project_id)
        .join(FarmComponent, FarmComponent.id == FarmProductPart.component_id)
        .join(Project, Project.id == FarmProduct.project_id)
        .order_by(Project.cover_image_filename.is_(None), Project.name)
    ):
        entry = {"project_id": product.project_id, "name": name, "has_cover": bool(cover)}
        variants = {by_shape.get((shape_id, (v.get("color") or "").lower())) for v in product.variants or []}
        for target in ({component_id} | (variants if shape_id else set())) - {None}:
            if entry not in used_in.setdefault(target, []):
                used_in[target].append(entry)
    resolver = await PrinterModelResolver.load(db)
    active = list((await db.execute(select(Printer).where(Printer.is_active.is_(True)))).scalars())
    printers = await with_spools_set_by_hand(db, printer_snapshot(active))
    queued = queued or {}

    rows = []
    for c in wanted:
        comp = ours.get(c["code"])
        c = {**c, "name": c.get("name") or (comp.name if comp else c["code"]), "to_print": c.get("to_print") or 0}
        color_hex = config["colors"].get((comp.color or "").lower()) if comp and comp.color else None
        options = []
        for plate in plates:
            pieces = component_yield(comp, plate.yields) if comp else 0
            if not pieces:
                continue
            model = resolver.resolve(plate.printer_model).target_model or plate.printer_model or ""
            fits = [
                printer_fit(p, plate.materials or [], color_hex, config["tolerance"])
                for p in printers
                if p["model"].lower() == model.lower()
            ]
            ready = any(f["idle"] and f["material_ok"] and f["color_ok"] for f in fits)
            free = any(f["idle"] for f in fits)
            options.append(
                {
                    "plate_id": plate.id,
                    "library_file_id": plate.library_file_id,
                    "file": paths.get(plate.library_file_id, ""),
                    "plate": plate.plate_index,
                    "printer_model": model,
                    "material": "+".join(plate.materials or []),
                    "yield": pieces,
                    "print_time_seconds": plate.print_time_seconds,
                    "filament_grams": plate.filament_grams,
                    "sliced": plate.sliced_file_id is not None,
                    "printers": fits,
                    "score": 2 if ready else 1 if free else 0,
                }
            )
        # Proposal first: a free printer with material and colour loaded, then any free one; more pieces first.
        options.sort(key=lambda o: (-o["score"], -o["yield"], o["print_time_seconds"] or 0))
        in_queue = queued.get(c["code"], 0)
        rows.append(
            {
                **{k: c.get(k) for k in COMPONENT_FIELDS},
                "color": comp.color if comp else None,
                "color_hex": color_hex,
                "shape": shape_names.get(comp.shape_id) if comp and comp.shape_id else None,
                "shape_id": comp.shape_id if comp else None,
                "known": comp is not None,
                "products": used_in.get(comp.id, []) if comp else [],
                "in_queue": in_queue,
                "missing": max(0, c["to_print"] - in_queue),
                "plates": options,
            }
        )
    if codes is None:  # by hand the product's order stays
        rows.sort(
            key=lambda r: (URGENCY.get(r["status"], 9), r["days_left"] if r["days_left"] is not None else math.inf)
        )
    return {"fetched_at": fetched_at, "error": error, "tolerance": config["tolerance"], "components": rows}
