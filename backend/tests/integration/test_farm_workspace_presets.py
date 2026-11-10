"""Fork (printfarm): the 3MF Workspace's preset lists and resolved preset values (farm/workspace/presets.py)."""

from __future__ import annotations

import json

import pytest

from backend.app.models.local_preset import LocalPreset
from backend.app.services import orca_profiles

API = "/api/v1/farm/workspace/presets"
BASES = {
    "Test Process 0.20 @Base": {"inherits": "fdm_test_common", "layer_height": "0.2", "wall_loops": "2"},
    "fdm_test_common": {"layer_height": "0.3", "sparse_infill_density": "15%", "wall_loops": "3"},
}


@pytest.fixture
def bases(monkeypatch):
    """System presets come from the cached OrcaSlicer base profiles: test ones, no network."""

    async def fetch(name, profile_type, db):
        return dict(BASES[name]) if name in BASES else None

    monkeypatch.setattr(orca_profiles, "fetch_and_cache_base_profile", fetch)


@pytest.mark.asyncio
async def test_lists_the_user_presets_of_a_section(async_client, db_session):
    db_session.add_all(
        [
            LocalPreset(name="My PLA", preset_type="filament", setting=json.dumps({"nozzle_temperature": ["210"]})),
            LocalPreset(
                name="Fine 0.12",
                preset_type="process",
                setting=json.dumps({"layer_height": "0.12"}),
                inherits="Test Process 0.20 @Base",
            ),
        ]
    )
    await db_session.commit()
    resp = await async_client.get(API, params={"section": "process"})
    assert resp.status_code == 200, resp.text
    assert [(p["name"], p["inherits"]) for p in resp.json()["user"]] == [("Fine 0.12", "Test Process 0.20 @Base")]
    assert (await async_client.get(API, params={"section": "plates"})).status_code == 422


@pytest.mark.asyncio
async def test_resolves_a_user_preset_over_its_parents(async_client, db_session, bases):
    row = LocalPreset(
        name="Fine 0.12",
        preset_type="process",
        setting=json.dumps({"inherits": "Test Process 0.20 @Base", "layer_height": "0.12"}),
        inherits="Test Process 0.20 @Base",
    )
    db_session.add(row)
    await db_session.commit()
    resp = await async_client.get(f"{API}/values", params={"section": "process", "source": "user", "id": row.id})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Fine 0.12" and body["inherits"] == "Test Process 0.20 @Base"
    # the child's value wins, then its parent's, then the grandparent's
    assert body["values"] == {"layer_height": "0.12", "wall_loops": "2", "sparse_infill_density": "15%"}
    # a preset of another section is not found there
    assert body["missing_parent"] is None
    other = await async_client.get(f"{API}/values", params={"section": "filament", "source": "user", "id": row.id})
    assert other.status_code == 404


@pytest.mark.asyncio
async def test_a_user_preset_whose_parent_is_unknown_says_so(async_client, db_session, bases):
    row = LocalPreset(
        name="Orphan",
        preset_type="process",
        setting=json.dumps({"layer_height": "0.12"}),
        inherits="Not a system preset",
    )
    db_session.add(row)
    await db_session.commit()
    resp = await async_client.get(f"{API}/values", params={"section": "process", "source": "user", "id": row.id})
    assert resp.status_code == 200, resp.text
    assert resp.json()["missing_parent"] == "Not a system preset"
    assert resp.json()["values"] == {"layer_height": "0.12"}  # only its own: the rest is unknown


@pytest.mark.asyncio
async def test_resolves_a_system_preset_by_name(async_client, bases):
    resp = await async_client.get(
        f"{API}/values", params={"section": "process", "source": "system", "name": "Test Process 0.20 @Base"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["values"] == {"layer_height": "0.2", "wall_loops": "2", "sparse_infill_density": "15%"}
    missing = await async_client.get(f"{API}/values", params={"section": "process", "source": "system", "name": "Nope"})
    assert missing.status_code == 404
    assert (
        await async_client.get(f"{API}/values", params={"section": "process", "source": "system"})
    ).status_code == 422
