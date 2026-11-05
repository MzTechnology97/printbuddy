"""Printfarm (fork): colours of the filament lines in use, from the Open Filament Database (stubbed, no network)."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from backend.app.farm import brand_colors
from backend.app.models.color_catalog import ColorCatalogEntry
from backend.app.models.local_preset import LocalPreset
from backend.app.services.open_filament_database import OpenFilamentDatabaseNotFound, OpenFilamentDatabaseUnavailable

BRANDS = [
    {"name": "IEMAI", "slug": "iemai"},
    {"name": "eSUN 3D", "slug": "esun-3d"},
    {"name": "QIDI Tech", "slug": "qidi-tech"},
    {"name": "SUNLU", "slug": "sunlu"},
]
CATALOGUE = {  # slug -> material -> line slug -> (line name, [(colour, hex)])
    "iemai": {
        "ABS": {"abs": ("ABS", [("Black", "#000000"), ("White", "ffffff")])},
        "PETG": {
            "petg-cf": ("PETG-CF", [("Black", "#111111"), ("Senza codice", "")]),
            "petg-silk": ("PETG Silk", [("Gold", "#D4AF37")]),  # no preset uses it
        },
        "PC": {"pc-cf": ("PC-CF", [("Black", "#0B0B0BFF")])},  # #RRGGBBAA
    },
    "esun-3d": {"ABS": {"abs-plus": ("ABS+", [("Fire Red", "#C8102E")])}},
    "qidi-tech": {"ASA": {"asa": ("ASA", [("Grey", "#808080")])}},
    "sunlu": {
        "PLA": {
            "pla": ("PLA", [("Red", "#FF0000")]),
            "marble-pla": ("Marble PLA", [("Marble White", "#EEEEEE")]),
            "silk-pla": ("Silk PLA", [("Silk Gold", "#FFD700")]),
        }
    },
}


class FakeClient:
    def __init__(self, broken=(), down=False, missing=(), refuse=(), busy=()):
        self.broken, self.down, self.missing, self.refuse = set(broken), down, set(missing), set(refuse)
        self.busy = set(busy)
        self.calls: list = []

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
        self.calls.append((slug, material))
        return {"filaments": [{"slug": f, "name": n} for f, (n, _) in CATALOGUE[slug][material].items()]}

    async def get_filament(self, slug, material, line):
        self.calls.append((slug, material, line))
        if line in self.missing:
            raise OpenFilamentDatabaseNotFound("404")
        if line in self.refuse:
            raise ValueError("Invalid OFDB path segment")
        if line in self.busy:
            raise OpenFilamentDatabaseUnavailable("Open Filament Database returned HTTP 503")
        name, variants = CATALOGUE[slug][material][line]
        return {"name": name, "variants": [{"name": c, "color_hex": h} for c, h in variants]}


async def presets(db_session, *names_and_vendors):
    start = len((await db_session.execute(select(LocalPreset))).scalars().all())
    for i, (name, vendor) in enumerate(names_and_vendors, start):
        db_session.add(
            LocalPreset(name=name or f"Filamento {i}", preset_type="filament", filament_vendor=vendor, setting="{}")
        )
    await db_session.commit()


MINE = [("IEMAI ABS @K1", "IEMAI"), ("IEMAI PETG-CF @K1", "IEMAI"), ("IEMAI-PC-CF", "IEMAI"), ("ESUN ABS+", "Esun")]


async def catalogue(db_session):
    rows = (await db_session.execute(select(ColorCatalogEntry))).scalars().all()
    return sorted((r.manufacturer, r.material, r.color_name, r.hex_color) for r in rows)


def test_brands_are_matched_however_the_slicer_spells_them():
    matched, unmatched = brand_colors.match_brands({"IEMAI", "Esun", "Qidi", "Retro D", "Generic", ""}, BRANDS)
    assert {v: b["slug"] for v, b in matched.items()} == {"IEMAI": "iemai", "Esun": "esun-3d", "Qidi": "qidi-tech"}
    assert unmatched == ["Retro D"]  # "Generic" is not a brand


@pytest.mark.parametrize(
    ("material", "line", "preset", "used"),
    [
        ("PLA", "Marble PLA", "Sunlu-Marble PLA", True),
        ("PLA", "PLA", "Sunlu-Marble PLA", True),  # the plain line of a material in use
        ("PLA", "Silk PLA", "Sunlu-Marble PLA", False),
        ("PA6", "PA6-CF", "Sunlu PA6-CF", True),
        ("PLA", "PLA+ 2.0", "SUNLU PLA+ 2.0", True),
        ("PETG", "PETG", "Sunlu-Marble PLA", False),  # another material
        ("PC", "PC", "Bambu PCTG", False),  # PCTG is not PC
    ],
)
def test_only_the_lines_in_use(material, line, preset, used):
    assert brand_colors.line_in_use(material, line, [brand_colors._words(preset)]) is used


@pytest.mark.asyncio
async def test_colours_of_the_lines_in_use_are_added_once(db_session):
    await presets(db_session, *MINE, ("Retro D PLA", "Retro D"))
    # Already there, with another spelling of the brand and other case: kept, not added twice.
    db_session.add(ColorCatalogEntry(manufacturer="iemai", color_name="BLACK", hex_color="#000000", material="abs"))
    db_session.add(ColorCatalogEntry(manufacturer="eSUN", color_name="Fire Red", hex_color="#C8102E", material="ABS+"))
    await db_session.commit()

    result = await brand_colors.sync(db_session, FakeClient())
    assert result["status"] == "done"
    assert result["added_by_brand"] == {"IEMAI": 3, "eSUN 3D": 0}
    assert result["unmatched"] == ["Retro D"]
    assert ("IEMAI", "PC-CF", "Black", "#0B0B0BFF") in await catalogue(db_session)  # 8-digit hex kept
    names = {(m, line, c) for m, line, c, _ in await catalogue(db_session)}
    assert ("IEMAI", "PETG Silk", "Gold") not in names  # a line no preset uses
    assert ("IEMAI", "PETG-CF", "Senza codice") not in names  # no valid hex

    again = FakeClient()
    assert (await brand_colors.sync(db_session, again))["status"] == "unchanged"
    assert again.calls == ["brands"]  # nothing asked within the week
    assert (await brand_colors.sync(db_session, FakeClient(), force=True))["added"] == 0


@pytest.mark.asyncio
async def test_the_slicers_own_catalogue_does_not_count(db_session):
    """Bambu Studio's catalogue lists every vendor it ships: only the user's own filaments say what is in use."""
    db_session.add(
        LocalPreset(
            name="SUNLU Silk PLA @BBL X1C",
            preset_type="filament",
            filament_vendor="SUNLU",
            source=brand_colors.SYSTEM_SOURCE,
            setting="{}",
        )
    )
    await db_session.commit()
    await presets(db_session, ("IEMAI ABS @K1", "IEMAI"))
    result = await brand_colors.sync(db_session, FakeClient())
    assert result["added_by_brand"] == {"IEMAI": 2}


@pytest.mark.asyncio
async def test_a_colour_deleted_by_hand_does_not_come_back(db_session):
    await presets(db_session, *MINE)
    await brand_colors.sync(db_session, FakeClient())
    await db_session.execute(delete(ColorCatalogEntry).where(ColorCatalogEntry.color_name == "White"))
    await db_session.commit()
    assert (await brand_colors.sync(db_session, FakeClient(), force=True))["added"] == 0
    assert "White" not in {c for _, _, c, _ in await catalogue(db_session)}


@pytest.mark.asyncio
async def test_a_line_that_cannot_be_read_is_skipped_not_the_brand(db_session):
    """A persistent 404 or a refused name on one line: the rest of the brand comes in, no retry every hour."""
    await presets(db_session, *MINE)
    result = await brand_colors.sync(db_session, FakeClient(missing={"petg-cf"}, refuse={"pc-cf"}))
    assert result["added_by_brand"]["IEMAI"] == 2  # ABS Black and White
    assert result["skipped_lines"] == {"IEMAI": 2}
    assert result["retry"] == {}
    later = FakeClient(missing={"petg-cf"})
    assert (await brand_colors.sync(db_session, later))["status"] == "unchanged"
    assert later.calls == ["brands"]


@pytest.mark.asyncio
async def test_a_line_not_answered_now_is_asked_again_later(db_session):
    """A timeout or a 5xx on one line: the rest comes in now, the brand is retried after the wait."""
    await presets(db_session, *MINE)
    result = await brand_colors.sync(db_session, FakeClient(busy={"pc-cf"}))
    assert result["added_by_brand"]["IEMAI"] == 3 and result["retry"]["iemai"]["fails"] == 1
    assert "iemai" not in result["brands"]
    state = await brand_colors._read_json(db_session, brand_colors.STATE_KEY, {})
    state["retry"]["iemai"]["next_at"] = (brand_colors._now() - timedelta(seconds=1)).isoformat()
    await brand_colors.set_setting(db_session, brand_colors.STATE_KEY, brand_colors.json.dumps(state))
    await db_session.commit()
    done = await brand_colors.sync(db_session, FakeClient())
    assert done["added_by_brand"] == {"IEMAI": 1} and done["retry"] == {} and "iemai" in done["brands"]


@pytest.mark.asyncio
async def test_a_brand_that_fails_is_retried_after_a_growing_wait(db_session):
    await presets(db_session, *MINE)
    result = await brand_colors.sync(db_session, FakeClient(broken={"iemai"}))
    assert result["retry"]["iemai"]["fails"] == 1 and result["added_by_brand"] == {"eSUN 3D": 1}
    # Before the wait: not asked again. After it: only that brand is read again.
    assert (await brand_colors.sync(db_session, FakeClient()))["status"] == "unchanged"
    state = await brand_colors._read_json(db_session, brand_colors.STATE_KEY, {})
    state["retry"]["iemai"]["next_at"] = (brand_colors._now() - timedelta(seconds=1)).isoformat()
    await brand_colors.set_setting(db_session, brand_colors.STATE_KEY, brand_colors.json.dumps(state))
    await db_session.commit()
    still_down = await brand_colors.sync(db_session, FakeClient(broken={"iemai"}))
    assert still_down["retry"]["iemai"]["fails"] == 2  # the wait doubles
    gap = brand_colors._when(still_down["retry"]["iemai"]["next_at"]) - brand_colors._now()
    assert timedelta(seconds=7000) < gap <= timedelta(seconds=7200)
    state = await brand_colors._read_json(db_session, brand_colors.STATE_KEY, {})
    state["retry"]["iemai"]["next_at"] = (brand_colors._now() - timedelta(seconds=1)).isoformat()
    await brand_colors.set_setting(db_session, brand_colors.STATE_KEY, brand_colors.json.dumps(state))
    await db_session.commit()
    back = FakeClient()
    done = await brand_colors.sync(db_session, back)
    assert done["added_by_brand"] == {"IEMAI": 4} and done["retry"] == {}
    assert "esun-3d" not in back.calls  # only the brand that was due


@pytest.mark.asyncio
async def test_a_new_brand_or_a_week_later_runs_again(db_session):
    await presets(db_session, *MINE)
    await brand_colors.sync(db_session, FakeClient())
    await presets(db_session, ("Qidi-ASA", "Qidi"))
    assert (await brand_colors.sync(db_session, FakeClient()))["added_by_brand"]["QIDI Tech"] == 1
    state = await brand_colors._read_json(db_session, brand_colors.STATE_KEY, {})
    state["at"] = (brand_colors._now() - timedelta(days=8)).isoformat()
    await brand_colors.set_setting(db_session, brand_colors.STATE_KEY, brand_colors.json.dumps(state))
    await db_session.commit()
    assert (await brand_colors.sync(db_session, FakeClient()))["status"] == "done"


@pytest.mark.asyncio
async def test_two_runs_at_once_insert_once(db_session):
    await presets(db_session, *MINE)
    await asyncio.gather(
        brand_colors.sync(db_session, FakeClient(), force=True), brand_colors.sync(db_session, FakeClient(), force=True)
    )
    rows = await catalogue(db_session)
    assert len(rows) == len(set(rows)) == 5


@pytest.mark.asyncio
async def test_at_most_max_per_brand(db_session, monkeypatch):
    await presets(db_session, *MINE)
    monkeypatch.setattr(brand_colors, "MAX_PER_BRAND", 2)
    result = await brand_colors.sync(db_session, FakeClient())
    assert result["added_by_brand"]["IEMAI"] == 2 and result["capped"] == ["IEMAI"]


@pytest.mark.asyncio
async def test_database_down_changes_nothing(db_session):
    await presets(db_session, *MINE)
    assert (await brand_colors.sync(db_session, FakeClient(down=True)))["status"] == "unavailable"
    assert await catalogue(db_session) == []
    assert await brand_colors._read_json(db_session, brand_colors.STATE_KEY, {}) == {}


@pytest.mark.asyncio
async def test_api_starts_in_the_background_and_reports(async_client, db_session, monkeypatch):
    await presets(db_session, *MINE)
    monkeypatch.setattr(brand_colors, "OpenFilamentDatabaseClient", FakeClient)

    class _Session:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(brand_colors.database, "async_session", lambda: _Session())
    started = await async_client.post("/api/v1/farm/brand-colors/sync")
    assert (started.status_code, started.json()) == (202, {"status": "started"})
    await brand_colors._background
    state = (await async_client.get("/api/v1/farm/brand-colors")).json()
    assert (state["added"], state["running"]) == (5, False)  # IEMAI 4 + eSUN 1
