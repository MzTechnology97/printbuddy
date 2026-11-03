"""Farm API: what to print (phase 3) and its settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.plan import filaments, service
from backend.app.farm.plan.models import FarmPrinterSpool
from backend.app.farm.plates.printer_map import PrinterModelResolver
from backend.app.models.printer import Printer
from backend.app.services.printer_providers.factory import normalize_provider

router = APIRouter(tags=["farm"])  # mounted under /farm by the products router

_read = Depends(require_permission_if_auth_enabled(Permission.QUEUE_READ))
_settings = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_UPDATE))
_spool = Depends(require_permission_if_auth_enabled(Permission.QUEUE_CREATE))


class PlanSettingsIn(BaseModel):
    url: str = Field(default="", max_length=300)
    tolerance: int = Field(default=service.DEFAULT_TOLERANCE, ge=0, le=441)
    colors: dict[str, str] = Field(default_factory=dict, max_length=200)


@router.get("/plan", dependencies=[_read])
async def get_plan(db: AsyncSession = Depends(get_db)) -> dict:
    from backend.app.farm.jobs.service import queued_pieces

    return await service.build_plan(db, queued=await queued_pieces(db))


@router.get("/plan/products/{project_id}", dependencies=[_read])
async def get_product_plan(project_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    """The parts of a product with their plates, to print it by hand whatever Seller Hub asks.

    Each part also says how many go in one product (``kit_quantity``): stock / quantity is how many sets it makes.
    """
    from backend.app.farm.jobs.service import queued_pieces
    from backend.app.farm.products.models import FarmComponent, FarmProduct, FarmProductPart

    if await db.get(FarmProduct, project_id) is None:
        raise HTTPException(404, "Not a product")
    # shortcut: the parts in the product's own colour; offer its colour variants if they are printed by hand too.
    quantities = dict(
        (
            await db.execute(
                select(FarmComponent.code, FarmProductPart.quantity)
                .join(FarmProductPart, FarmProductPart.component_id == FarmComponent.id)
                .where(FarmProductPart.project_id == project_id, FarmComponent.retired_at.is_(None))
                .order_by(FarmProductPart.sort_order)
            )
        ).all()
    )
    plan = await service.build_plan(db, queued=await queued_pieces(db), codes=list(quantities))
    for row in plan["components"]:
        row["kit_quantity"] = quantities.get(row["code"]) or 1
    return plan


@router.get("/plan/settings", dependencies=[_read])
async def get_plan_settings(db: AsyncSession = Depends(get_db)) -> dict:
    return {**await service.load_config(db), "default_colors": service.DEFAULT_COLORS}


@router.put("/plan/settings", dependencies=[_settings])
async def put_plan_settings(data: PlanSettingsIn, db: AsyncSession = Depends(get_db)) -> dict:
    try:
        config = await service.save_config(db, data.url, data.tolerance, data.colors)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {**config, "default_colors": service.DEFAULT_COLORS}


class SpoolIn(BaseModel):
    material: str = Field(min_length=1, max_length=20)
    color_name: str = Field(min_length=1, max_length=50)


async def _klipper_printers(db: AsyncSession) -> list[Printer]:
    printers = (await db.execute(select(Printer).where(Printer.is_active.is_(True)).order_by(Printer.name))).scalars()
    return [p for p in printers if normalize_provider(p.provider) != "bambu"]


@router.get("/printer-spools", dependencies=[_read])
async def list_printer_spools(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Klipper printers that report no filament (no CFS), with the spool set by hand on each."""
    printers = await _klipper_printers(db)
    spools = {s.printer_id: s for s in (await db.execute(select(FarmPrinterSpool))).scalars()}
    out = []
    ids = {p.id for p in printers}
    resolver = await PrinterModelResolver.load(db)
    for p in service.printer_snapshot(printers):
        if p["id"] not in ids or p["loaded"]:
            continue  # reads its own spools (K2 with CFS)
        spool = spools.get(p["id"])
        out.append(
            {
                "printer_id": p["id"],
                "name": p["name"],
                "model": p["model"],
                "material": spool.material if spool else None,
                "color_name": spool.color_name if spool else None,
                "color": spool.color if spool else None,
                "updated_at": spool.updated_at if spool else None,
                "materials": filaments.materials(p["model"], resolver),
            }
        )
    return out


@router.get("/filament-catalog", dependencies=[_read])
async def get_filament_catalog(model: str | None = None, db: AsyncSession = Depends(get_db)) -> list[dict]:
    """Filaments (vendor, name, type, compatible printer presets) per printer model, from the slicer profiles.

    ``model`` keeps one Printbuddy printer model ("P1S", "K2"); ``printer_model`` is the Printbuddy model
    each catalog model maps to, null when no printer of the farm has it.
    """
    resolver = await PrinterModelResolver.load(db)
    keep = filaments.catalog_model(model, resolver) if model else None
    if model and keep is None:
        raise HTTPException(404, f"No filament catalog for printer model '{model}'")
    out = []
    for key, entries in filaments.catalog().items():
        if keep in (None, key):
            target = resolver.resolve(key).target_model
            out.append({"model": key, "printer_model": target, "filaments": entries})
    return out


@router.put("/printer-spools/{printer_id}", dependencies=[_spool])
async def set_printer_spool(printer_id: int, data: SpoolIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Say which spool is on a Klipper printer: material and a colour of the colour table."""
    if printer_id not in {p.id for p in await _klipper_printers(db)}:
        raise HTTPException(404, "No active Klipper printer with this id")
    config = await service.load_config(db)
    color = config["colors"].get(data.color_name.strip().lower())
    if color is None:
        raise HTTPException(422, f"Unknown colour '{data.color_name}': add it to the colour table first")
    spool = await db.get(FarmPrinterSpool, printer_id) or FarmPrinterSpool(printer_id=printer_id)
    spool.material, spool.color_name, spool.color = data.material.strip().upper(), data.color_name.strip(), color
    db.add(spool)
    await db.commit()
    return {"printer_id": printer_id, "material": spool.material, "color_name": spool.color_name, "color": color}


@router.delete("/printer-spools/{printer_id}", dependencies=[_spool])
async def clear_printer_spool(printer_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    spool = await db.get(FarmPrinterSpool, printer_id)
    if spool is not None:
        await db.delete(spool)
        await db.commit()
    return {"printer_id": printer_id, "material": None}
