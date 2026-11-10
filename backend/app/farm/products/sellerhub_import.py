"""Products from Seller Hub, picked by hand: the listings without a product, and the import of the chosen ones.

``GET /farm/import/sellerhub/listings`` reads Seller Hub's plan (``GET {url}/api/print-plan``, the channel What to
print already uses, no credentials) and returns its parts whose SKUs and ASINs no product owns, variants included.
Seller Hub's parts are already one row per ASIN (or per named part) with all its SKUs; inactive listings never reach
them (``excluded_inactive``) and the ones marked "not printed" there are listed apart (``ignored``): both are counted.

``POST /farm/import/sellerhub/listings`` creates one product (name, SKUs, ASINs, Amazon photo as cover) for each
chosen key, reading the plan again: nothing is created on its own, components are added by hand afterwards, and
nothing is written to Seller Hub, Amazon or Etsy.

``GET /farm/import/sellerhub/thumbnail`` serves a listing photo: the SPA's CSP blocks Amazon's CDN.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.farm.plan import service as plan_service
from backend.app.farm.products import routes as products
from backend.app.farm.products.models import FarmProduct
from backend.app.models.project import Project

router = APIRouter()

THUMB_PX = 160


def _ids(values: list) -> list[str]:
    """Seller Hub's SKUs or ASINs, cleaned and de-duplicated; one with spaces is not an identifier and is dropped."""
    out: list[str] = []
    for v in values:
        v = products._clean(str(v or ""), 100)
        if v and " " not in v and v not in out:
            out.append(v)
    return out


def listings(plan: dict) -> list[dict]:
    """One row per part of Seller Hub's plan: key, product name, Amazon title, SKUs, ASINs, photo."""
    rows = []
    for part in plan.get("parts") or []:
        if not isinstance(part, dict) or not isinstance(part.get("key"), str):
            continue
        skus_rows = [s for s in part.get("skus") or [] if isinstance(s, dict)]
        skus, asins = _ids([s.get("sku") for s in skus_rows]), _ids([s.get("asin") for s in skus_rows])
        if not skus and not asins:
            continue
        title = next((str(s["title"]) for s in skus_rows if s.get("title")), "")
        name = products._clean(str(part.get("name") or title or (skus or asins)[0]), 255)
        image = products._amazon_image(part.get("image_url")) or next(
            (u for u in (products._amazon_image(s.get("image_url")) for s in skus_rows) if u), None
        )
        rows.append(
            {
                "key": part["key"][:200],
                "name": name,
                "title": products._clean(title, 500),
                "skus": skus,
                "asins": asins,
                "image_url": image,
            }
        )
    return rows


def _keys(skus: list[str], asins: list[str]) -> list[str]:
    return [f"sku:{s}" for s in skus] + [f"asin:{a}" for a in asins]


async def _owned(db: AsyncSession) -> set[str]:
    """``sku:…`` / ``asin:…`` of every product, its variants' included."""
    owned: set[str] = set()
    for p in (await db.execute(select(FarmProduct))).scalars():
        owned.update(_keys(*products._listings(p.skus, p.asins, p.variants)))
    return owned


async def _plan(db: AsyncSession) -> dict:
    url = (await plan_service.load_config(db))["url"]
    if not url:
        raise HTTPException(409, "Seller Hub URL not set (What to print → settings)")
    try:
        plan = await plan_service._get_print_plan(url)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"Seller Hub did not answer ({type(exc).__name__})") from exc
    if not isinstance(plan, dict):
        raise HTTPException(502, "Seller Hub answered with something that is not a plan")
    return plan


@router.get("/import/sellerhub/listings", dependencies=[products._read])
async def importable_listings(db: AsyncSession = Depends(get_db)) -> dict:
    plan = await _plan(db)
    owned = await _owned(db)
    rows = [r for r in listings(plan) if not owned.intersection(_keys(r["skus"], r["asins"]))]
    excluded = plan.get("excluded_inactive")
    return {
        "listings": rows,
        # Not proposed: Seller Hub leaves inactive listings out of its plan and lists the "not printed" ones apart.
        "excluded_inactive": excluded if isinstance(excluded, int) and not isinstance(excluded, bool) else 0,
        "ignored": len([s for s in plan.get("ignored") or [] if isinstance(s, dict)]),
    }


class ImportIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=500)


@router.post("/import/sellerhub/listings", dependencies=[products._create])
async def import_listings(body: ImportIn, db: AsyncSession = Depends(get_db)) -> dict:
    """A product for each chosen key that is still in Seller Hub's plan and still without a product."""
    by_key = {r["key"]: r for r in listings(await _plan(db))}
    owned = await _owned(db)
    result: dict = {"created": [], "already_owned": [], "not_found": [], "covers_added": 0}
    for key in dict.fromkeys(body.keys):
        row = by_key.get(key)
        if row is None:
            result["not_found"].append(key[:200])
            continue
        ids = _keys(row["skus"], row["asins"])
        # Created in the meantime, or by an earlier row of this same import sharing an SKU or ASIN.
        if owned.intersection(ids):
            result["already_owned"].append(key)
            continue
        project = Project(name=row["name"])
        db.add(project)
        await db.flush()
        db.add(FarmProduct(project_id=project.id, skus=row["skus"], asins=row["asins"]))
        await db.flush()
        owned.update(ids)
        if await products._set_amazon_cover(project, row["image_url"]):
            result["covers_added"] += 1
        result["created"].append({"key": key, "project_id": project.id, "name": row["name"]})
    await db.commit()
    return result


@router.get("/import/sellerhub/thumbnail")
async def listing_image(url: str = Query(max_length=2048)) -> Response:
    """A small copy of a listing photo from Amazon's CDN.

    Unauthenticated on purpose, like the MakerWorld thumbnails (``/thumbnail`` is a public pattern of the auth
    middleware): ``<img>`` can't send a token, and these are Amazon's public product images. Only
    ``m.media-amazon.com/images/I/…`` is fetched, so it is no open proxy.
    """
    full = products._amazon_image(url)
    if full is None:
        raise HTTPException(400, "Not an Amazon listing image")
    stem, ext = full.rsplit(".", 1)
    content = await products._fetch_image(f"{stem}._SL{THUMB_PX}_.{ext}")
    if content is None:
        raise HTTPException(502, "Image not available")
    return Response(
        content=content,
        media_type="image/png" if ext.lower() == "png" else "image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )
