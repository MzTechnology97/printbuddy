"""Printfarm (fork): colours of the filament brands in use, from the Open Filament Database (stubbed, no network)."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.app.farm import brand_colors
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.local_preset import LocalPreset
from backend.app.services.open_filament_database import OpenFilamentDatabaseUnavailable

BRANDS = [
    {"name": "IEMAI", "slug": "iemai"},
    {"name": "eSUN 3D", "slug": "esun-3d"},
    {"name": "QIDI Tech", "slug": "qidi-tech"},
    {"name": "Bambu Lab", "slug": "bambu-lab"},
]
CATALOGUE = {  # slug -> material -> filament slug -> (name, [(colour, hex)])
    "iemai": {
        "ABS": {"abs": ("ABS", [("Black", "#000000"), ("White", "ffffff")])},
        "PETG": {"petg-cf": ("PETG-CF", [("Black", "#111111"), ("Senza colore", "")])},
    },
    "esun-3d": {"PLA": {"pla-plus": ("PLA+", [("Fire Engine Red", "#C8102E")])}},
    "qidi-tech": {"PA": {"paht-cf": ("PAHT-CF", [("Black", "#0A0A0A")])}},
    "bambu-lab": {"PLA": {"pla-basic": ("PLA Basic", [("Jade White", "#FFFFFF")])}},
}


class FakeClient:
    def __init__(self, broken=(), down=False):
        self.broken, self.down, self.calls = set(broken), down, []

    async def get_brands(self):
        self.calls.append("brands")
        if self.down:
            raise OpenFilamentDatabaseUnavailable("down")
        return {"brands": BRANDS}

    async def get_brand(self, slug):
        self.calls.append(slug)
        if slug in self.broken:
            raise OpenFilamentDatabaseUnavailable("timeout")
        return {"materials": [{"material": m} for m in CATALOGUE[slug]]}

    async def get_material(self, slug, material):
        return {"filaments": [{"slug": f} for f in CATALOGUE[slug][material]]}

    async def get_filament(self, slug, material, filament):
        name, variants = CATALOGUE[slug][material][filament]
        return {"name": name, "variants": [{"name": c, "color_hex": h} for c, h in variants]}


async def presets(db_session, *vendors):
    start = len((await db_session.execute(select(LocalPreset))).scalars().all())
    for i, vendor in enumerate(vendors, start):
        db_session.add(LocalPreset(name=f"Filamento {i}", preset_type="filament", filament_vendor=vendor, setting="{}"))
    await db_session.commit()


async def catalogue(db_session):
    rows = (await db_session.execute(select(ColorCatalogEntry))).scalars().all()
    return sorted((r.manufacturer, r.material, r.color_name, r.hex_color) for r in rows)


def test_brands_are_matched_however_the_slicer_spells_them():
    matched, unmatched = brand_colors.match_brands(
        {"IEMAI", "Esun", "Qidi", "Bambulab", "Retro D", "Generic", ""}, BRANDS
    )
    assert {v: b["slug"] for v, b in matched.items()} == {
        "IEMAI": "iemai",
        "Esun": "esun-3d",
        "Qidi": "qidi-tech",
        "Bambulab": "bambu-lab",
    }
    assert unmatched == ["Retro D"]  # "Generic" is not a brand


@pytest.mark.asyncio
async def test_colours_of_the_brands_in_use_are_added_once(db_session):
    await presets(db_session, "IEMAI", "IEMAI", "Esun", "Retro D")
    # Already there, written differently: kept as it is, not added twice.
    db_session.add(ColorCatalogEntry(manufacturer="iemai", color_name="black", hex_color="#000000", material="abs"))
    await db_session.commit()

    result = await brand_colors.sync(db_session, FakeClient())
    assert result["status"] == "done"
    assert result["added_by_brand"] == {"IEMAI": 2, "eSUN 3D": 1}
    assert result["unmatched"] == ["Retro D"]
    assert await catalogue(db_session) == [
        ("IEMAI", "ABS", "White", "#FFFFFF"),
        ("IEMAI", "PETG-CF", "Black", "#111111"),  # a colour without a hex is skipped
        ("eSUN 3D", "PLA+", "Fire Engine Red", "#C8102E"),
        ("iemai", "abs", "black", "#000000"),
    ]

    # Same brands within the week: nothing is asked again.
    again = FakeClient()
    assert (await brand_colors.sync(db_session, again))["status"] == "unchanged"
    assert again.calls == ["brands"]
    # Run by hand: everything is read again, nothing is duplicated.
    assert (await brand_colors.sync(db_session, FakeClient(), force=True))["added"] == 0


@pytest.mark.asyncio
async def test_a_new_brand_or_a_week_later_runs_again(db_session):
    await presets(db_session, "IEMAI")
    await brand_colors.sync(db_session, FakeClient())
    await presets(db_session, "Qidi")  # a filament of a new brand arrives from the slicers
    result = await brand_colors.sync(db_session, FakeClient())
    assert result["added_by_brand"] == {"IEMAI": 0, "QIDI Tech": 1}

    state = await brand_colors._state(db_session)
    state["at"] = (brand_colors._now() - timedelta(days=8)).isoformat()
    await brand_colors.set_setting(db_session, brand_colors.STATE_KEY, brand_colors.json.dumps(state))
    await db_session.commit()
    assert (await brand_colors.sync(db_session, FakeClient()))["status"] == "done"


@pytest.mark.asyncio
async def test_a_brand_that_fails_is_tried_again_next_time(db_session):
    await presets(db_session, "IEMAI", "Esun")
    result = await brand_colors.sync(db_session, FakeClient(broken={"iemai"}))
    assert (result["failed"], result["added_by_brand"]) == (["IEMAI"], {"eSUN 3D": 1})
    assert result["brands"] == ["esun-3d"]
    retry = await brand_colors.sync(db_session, FakeClient())
    assert retry["status"] == "done" and retry["added_by_brand"]["IEMAI"] == 3


@pytest.mark.asyncio
async def test_database_down_changes_nothing(db_session):
    await presets(db_session, "IEMAI")
    assert (await brand_colors.sync(db_session, FakeClient(down=True)))["status"] == "unavailable"
    assert await catalogue(db_session) == []
    assert await brand_colors._state(db_session) == {}


@pytest.mark.asyncio
async def test_api_runs_now_and_reports(async_client, db_session, monkeypatch):
    await presets(db_session, "IEMAI")
    monkeypatch.setattr(brand_colors, "OpenFilamentDatabaseClient", FakeClient)
    ran = await async_client.post("/api/v1/farm/brand-colors/sync")
    assert ran.status_code == 200
    assert ran.json()["added_by_brand"] == {"IEMAI": 3}
    state = (await async_client.get("/api/v1/farm/brand-colors")).json()
    assert (state["brands"], state["added"]) == (["iemai"], 3)
