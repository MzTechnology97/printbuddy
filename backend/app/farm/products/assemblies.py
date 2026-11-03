"""Groups of a kit's parts assembled together before the finished product (sub-assemblies).

"Maniglia mezza luna" = Mezza luna 1 ×1 + Mezza luna 2 ×1, two per handle set sold: Seller Hub
can then count and assemble the groups its stock allows, before the whole product can be
packed. Stored on the product (``FarmProduct.assemblies``), by component code, so a group
only ever holds the product's own parts; parts in no group are packed directly.

Each group has a key stable within the product ("A1"), a name, how many go in one unit sold
(``per_unit``) and its parts with their quantity per group. The bill of materials stays the
product's parts: groups only say which of those pieces are closed into groups. For each part,
per_unit × quantity summed over its groups must not exceed its quantity in the product (the
rest are packed directly); the sheet warns when it does.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.database import get_db
from backend.app.farm.products import routes as products
from backend.app.farm.products.models import FarmProduct

router = APIRouter(tags=["farm"])  # mounted under /farm by the products router

KEY_RE = re.compile(r"^A[1-9][0-9]{0,3}$")


def assemblies_of(product: FarmProduct, quantities: dict[str, int]) -> list[dict]:
    """The product's groups over its current parts (``{code: quantity}``), with what does not add up.

    A part taken out of the product leaves its group; a group left empty is dropped. A set (one
    component) has none.
    """
    if product.kind == "set":
        return []
    groups = []
    for a in product.assemblies or []:
        parts = [p for p in a["parts"] if p["component_code"] in quantities]
        if parts:
            groups.append((a, parts))
    in_groups: dict[str, int] = {}
    for a, parts in groups:
        for p in parts:
            in_groups[p["component_code"]] = in_groups.get(p["component_code"], 0) + p["quantity"] * a["per_unit"]
    out = []
    for a, parts in groups:
        warnings = [
            {
                "component_code": p["component_code"],
                "expected": in_groups[p["component_code"]],
                "quantity": quantities[p["component_code"]],
            }
            for p in parts
            if in_groups[p["component_code"]] > quantities[p["component_code"]]  # more in groups than in the product
        ]
        out.append(
            {"key": a["key"], "name": a["name"], "per_unit": a["per_unit"], "parts": parts, "warnings": warnings}
        )
    return out


def contract(groups: list[dict], codes: dict[str, str] | None = None, pieces: int = 1) -> list[dict]:
    """Groups as Seller Hub gets them: ``codes`` maps a variant's components, ``pieces`` its units per sale."""
    return [
        {
            "key": a["key"],
            "name": a["name"],
            "per_unit": a["per_unit"] * pieces,
            "parts": [
                {
                    "component_code": (codes or {}).get(p["component_code"], p["component_code"]),
                    "quantity": p["quantity"],
                }
                for p in a["parts"]
            ],
        }
        for a in groups
    ]


class AssemblyPartIn(BaseModel):
    component_code: str = Field(pattern=products.CODE_RE.pattern)
    quantity: int = Field(default=1, ge=1, le=products.QTY_MAX)  # per group


class AssemblyIn(BaseModel):
    key: str | None = Field(default=None, max_length=5)  # an existing group's; None: a new group
    name: str = Field(min_length=1, max_length=200)
    per_unit: int = Field(default=1, ge=1, le=products.QTY_MAX)  # groups per unit sold
    parts: list[AssemblyPartIn] = Field(min_length=1, max_length=50)


class AssembliesIn(BaseModel):
    assemblies: list[AssemblyIn] = Field(max_length=50)


@router.put("/products/{project_id}/assemblies", dependencies=[products._update])
async def set_assemblies(project_id: int, data: AssembliesIn, db: AsyncSession = Depends(get_db)) -> dict:
    """Replace the product's groups to assemble. Only its own parts."""
    from backend.app.farm.products.pieces import _parts

    product = await db.get(FarmProduct, project_id)
    if product is None:
        raise HTTPException(404, "Not a product")
    if product.kind == "set" and data.assemblies:
        raise HTTPException(422, "A set is printed as one component: nothing to assemble")
    quantities = {c.code: part.quantity for part, c in await _parts(db, project_id)}
    kept = [a.key for a in data.assemblies if a.key and KEY_RE.match(a.key)]
    if len(set(kept)) != len(kept):
        raise HTTPException(422, "Two groups with the same key")
    numbers = [int(k[1:]) for k in kept] + [int(a["key"][1:]) for a in product.assemblies or []]
    next_number = max(numbers, default=0) + 1
    groups = []
    for a in data.assemblies:
        name = products._clean(a.name, 200)
        if not name:
            raise HTTPException(422, "Name the group")
        codes = [p.component_code for p in a.parts]
        for code in codes:
            if code not in quantities:
                raise HTTPException(422, f"{code} is not a part of this product")
        if len(set(codes)) != len(codes):
            raise HTTPException(422, "A part listed twice in one group: set its pieces per group instead")
        key = a.key if a.key in kept else None
        if key is None:
            key, next_number = f"A{next_number}", next_number + 1
        groups.append(
            {
                "key": key,
                "name": name,
                "per_unit": a.per_unit,
                "parts": [{"component_code": p.component_code, "quantity": p.quantity} for p in a.parts],
            }
        )
    product.assemblies = groups or None
    product.updated_at = products._now()
    await db.commit()
    return {"project_id": project_id, "assemblies": assemblies_of(product, quantities)}
