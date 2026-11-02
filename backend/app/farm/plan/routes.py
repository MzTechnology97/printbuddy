"""Farm API: what to print (phase 3) and its settings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.auth import require_permission_if_auth_enabled
from backend.app.core.database import get_db
from backend.app.core.permissions import Permission
from backend.app.farm.plan import service

router = APIRouter(tags=["farm"])  # mounted under /farm by the products router

_read = Depends(require_permission_if_auth_enabled(Permission.QUEUE_READ))
_settings = Depends(require_permission_if_auth_enabled(Permission.SETTINGS_UPDATE))


class PlanSettingsIn(BaseModel):
    url: str = Field(default="", max_length=300)
    tolerance: int = Field(default=service.DEFAULT_TOLERANCE, ge=0, le=441)
    colors: dict[str, str] = Field(default_factory=dict, max_length=200)


@router.get("/plan", dependencies=[_read])
async def get_plan(db: AsyncSession = Depends(get_db)) -> dict:
    return await service.build_plan(db)


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
