"""Printfarm (fork) library: slicer/printer detection, watched-folder sync, embedded slicing.

All 3MF files are generated here; none come from a real farm.
"""

import json
import os
import time
import zipfile
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.app.api.routes import library
from backend.app.farm import detect, library_sync
from backend.app.farm.models import FarmLibraryFile, FarmWatchedFolder
from backend.app.models.library import LibraryFile

ORCA_25 = {"Application": "BambuStudio-02.06.00.51", "OrcaSlicer": "2.5.0"}
ORCA_23 = {"Application": "BambuStudio-2.3.2-rc2"}
BAMBU = {"Application": "BambuStudio-02.06.00.51"}
CREALITY = {"Application": "Creality_Print V7.1.0.1234"}


def make_3mf(path: Path, metadata: dict | None = None, printer_model: str | None = None, body: str = "") -> Path:
    meta = "".join(f'<metadata name="{k}">{v}</metadata>' for k, v in (metadata or {}).items())
    model = (
        '<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter">'
        f'{meta}<resources><object id="1"/></resources><build/></model>{body}'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("3D/3dmodel.model", model)
        if printer_model is not None:
            zf.writestr("Metadata/project_settings.config", json.dumps({"printer_model": printer_model}))
    return age(path)


def age(path: Path, seconds: int = 300) -> Path:
    """Backdate mtime so the file counts as settled (not being saved)."""
    past = time.time() - seconds
    os.utime(path, (past, past))
    return path


# ---------------------------------------------------------------- detection


class TestDetection:
    @pytest.mark.parametrize(
        ("metadata", "expected"),
        [
            (CREALITY, detect.SLICER_CREALITY),
            (ORCA_25, detect.SLICER_ORCA),  # Orca 2.4+ masquerades as BambuStudio-02.xx
            (ORCA_23, detect.SLICER_ORCA),
            ({"Application": "BambuStudio-2.3.0"}, detect.SLICER_ORCA),
            (BAMBU, detect.SLICER_BAMBU),
            ({}, detect.SLICER_CAD),
            ({"Application": "PrusaSlicer-2.8.1"}, detect.SLICER_UNKNOWN),
        ],
    )
    def test_slicer_rule(self, tmp_path, metadata, expected):
        assert detect.inspect_3mf(make_3mf(tmp_path / "x.3mf", metadata)).slicer == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Bambu Lab P1S", "P1S"),
            ("Bambu Lab X1 Carbon", "X1C"),
            ("Creality K2 Pro", "Creality K2 Pro"),
            ("Voron Voron 2.4 350", "Sovol SV08"),  # SV08 projects use the Voron profile
            (None, None),
        ],
    )
    def test_printer_model(self, tmp_path, raw, expected):
        info = detect.inspect_3mf(make_3mf(tmp_path / "x.3mf", BAMBU, printer_model=raw))
        assert info.printer_model_raw == raw
        assert info.printer_model == expected

    def test_metadata_after_resources_is_ignored(self, tmp_path):
        path = make_3mf(tmp_path / "x.3mf", {}, body='<metadata name="OrcaSlicer">x</metadata>')
        assert detect.inspect_3mf(path).slicer == detect.SLICER_CAD

    def test_not_a_zip_is_corrupt(self, tmp_path):
        bad = tmp_path / "bad.3mf"
        bad.write_bytes(b"PK\x03\x04 truncated")
        with pytest.raises(detect.CorruptFileError):
            detect.inspect_3mf(bad)

    def test_zip_without_model_is_corrupt(self, tmp_path):
        path = tmp_path / "empty.3mf"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("Metadata/project_settings.config", "{}")
        with pytest.raises(detect.CorruptFileError, match="3dmodel"):
            detect.inspect_3mf(path)


# ---------------------------------------------------------------- sync


@pytest.fixture(autouse=True)
def allow_test_external_root(monkeypatch, tmp_path):
    monkeypatch.setattr(library.app_settings, "external_roots", str(tmp_path))


@pytest.fixture
def nas(tmp_path):
    root = tmp_path / "nas"
    make_3mf(root / "Hook" / "P1S-PLA-hook.3mf", ORCA_25, "Bambu Lab P1S")
    make_3mf(root / "Box" / "box.3mf", BAMBU, "Bambu Lab A1")
    return root


async def add_external(client: AsyncClient, path: Path) -> dict:
    resp = await client.post("/api/v1/library/folders/external", json={"name": "NAS", "external_path": str(path)})
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
async def watched(async_client: AsyncClient, nas):
    folder = await add_external(async_client, nas)
    resp = await async_client.put(f"/api/v1/farm/library/folders/{folder['id']}", json={"scan_interval_seconds": 300})
    assert resp.status_code == 200
    return folder


async def scan(client: AsyncClient, folder: dict) -> dict:
    resp = await client.post(f"/api/v1/library/folders/{folder['id']}/scan")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def files_by_name(client: AsyncClient) -> dict[str, dict]:
    return {Path(f["path"]).name: f for f in (await client.get("/api/v1/farm/library/files")).json()}


@pytest.mark.integration
class TestWatchedFolderSync:
    async def test_scan_imports_with_detection(self, async_client, watched):
        result = await scan(async_client, watched)
        assert result["added"] == 2 and result["removed"] == 0
        files = await files_by_name(async_client)
        hook = files["P1S-PLA-hook.3mf"]
        assert (hook["slicer"], hook["printer_model"], hook["status"]) == ("orcaslicer", "P1S", "ok")
        assert files["box.3mf"]["slicer"] == "bambu_studio"
        assert (await scan(async_client, watched))["added"] == 0  # idempotent

    async def test_scan_proposes_plates_and_drops_photos(self, async_client, db_session, watched, nas):
        from backend.app.farm.plates.models import FarmPlate
        from backend.tests.integration.test_farm_plates import TWO_PLATES, make_project

        library_sync._plates_checked.clear()
        project = make_project(nas / "Hook" / "project.3mf", TWO_PLATES)
        photo = nas / "Hook" / "photo.jpg"
        photo.write_bytes(b"not a print file")
        # A photo row left by an earlier version, when watched folders took images too.
        db_session.add(
            LibraryFile(
                folder_id=watched["id"],
                is_external=True,
                filename=photo.name,
                file_path=str(photo),
                file_type="jpg",
                file_size=16,
            )
        )
        await db_session.commit()

        result = await scan(async_client, watched)
        assert (result["added"], result["removed"]) == (3, 1)
        paths = (await db_session.execute(select(LibraryFile.file_path))).scalars().all()
        assert str(photo) not in paths and photo.exists()  # row dropped, file untouched
        project_id = (await files_by_name(async_client))[project.name]["library_file_id"]
        plates = (
            await db_session.execute(select(FarmPlate.plate_index).where(FarmPlate.library_file_id == project_id))
        ).scalars()
        assert sorted(plates) == [1, 2]  # proposed by the scan, before anyone opens the file

    async def test_modified_file_keeps_id(self, async_client, db_session, watched, nas):
        await scan(async_client, watched)
        before = (await files_by_name(async_client))["box.3mf"]
        path = make_3mf(nas / "Box" / "box.3mf", ORCA_23, "Bambu Lab X1 Carbon", body="<!-- edited -->")
        age(path, 120)

        result = await scan(async_client, watched)
        assert (result["updated"], result["added"]) == (1, 0)
        after = (await files_by_name(async_client))["box.3mf"]
        assert after["library_file_id"] == before["library_file_id"]
        assert after["sha256"] != before["sha256"]
        assert (after["slicer"], after["printer_model"]) == ("orcaslicer", "X1C")
        lib_file = await db_session.get(LibraryFile, after["library_file_id"])
        assert lib_file.file_hash == after["sha256"]
        assert lib_file.file_size == path.stat().st_size

    async def test_moved_and_renamed_file_is_relinked(self, async_client, db_session, watched, nas):
        await scan(async_client, watched)
        before = (await files_by_name(async_client))["P1S-PLA-hook.3mf"]
        target = nas / "Archive" / "hook-v2.3mf"
        target.parent.mkdir()
        (nas / "Hook" / "P1S-PLA-hook.3mf").rename(target)

        result = await scan(async_client, watched)
        assert (result["relinked"], result["added"], result["missing"]) == (1, 0, 0)
        lib_file = await db_session.get(LibraryFile, before["library_file_id"])
        assert (lib_file.file_path, lib_file.filename) == (str(target), "hook-v2.3mf")
        files = (await async_client.get(f"/api/v1/library/files?folder_id={lib_file.folder_id}")).json()
        assert [f["id"] for f in files] == [before["library_file_id"]]
        assert (await files_by_name(async_client))["hook-v2.3mf"]["library_file_id"] == before["library_file_id"]

    async def test_deleted_file_is_kept_as_missing_then_relinked(self, async_client, watched, nas):
        await scan(async_client, watched)
        path = nas / "Box" / "box.3mf"
        data = path.read_bytes()
        path.unlink()
        result = await scan(async_client, watched)
        assert (result["missing"], result["removed"]) == (1, 0)
        missing = (await files_by_name(async_client))["box.3mf"]
        assert missing["status"] == "missing"

        # Comes back later, elsewhere: relinked to the same record.
        back = nas / "Box2" / "box.3mf"
        back.parent.mkdir()
        back.write_bytes(data)
        age(back)
        result = await scan(async_client, watched)
        assert (result["relinked"], result["missing"]) == (1, 0)
        restored = (await files_by_name(async_client))["box.3mf"]
        assert restored["library_file_id"] == missing["library_file_id"]
        assert restored["status"] == "ok"

    async def test_file_being_saved_is_skipped(self, async_client, watched, nas):
        path = make_3mf(nas / "new.3mf", BAMBU)
        os.utime(path, None)  # mtime = now: still being written
        result = await scan(async_client, watched)
        assert (result["added"], result["pending"]) == (2, 1)
        assert "new.3mf" not in await files_by_name(async_client)

        age(path)
        assert (await scan(async_client, watched))["added"] == 1

    async def test_corrupt_file_is_flagged_not_imported(self, async_client, db_session, watched, nas):
        bad = nas / "broken.3mf"
        bad.write_bytes(b"not a zip")
        age(bad)
        result = await scan(async_client, watched)
        assert (result["added"], result["corrupt"]) == (2, 1)
        broken = (await files_by_name(async_client))["broken.3mf"]
        assert (broken["status"], broken["library_file_id"]) == ("corrupt", None)
        imported = (await db_session.execute(select(LibraryFile.filename))).scalars().all()
        assert "broken.3mf" not in imported

        make_3mf(bad, BAMBU)  # fixed by saving again
        assert (await scan(async_client, watched))["added"] == 1
        assert (await files_by_name(async_client))["broken.3mf"]["status"] == "ok"

    async def test_symlink_escaping_the_share_is_ignored(self, async_client, watched, nas, tmp_path):
        outside = make_3mf(tmp_path / "outside" / "secret.3mf", BAMBU)
        (nas / "link.3mf").symlink_to(outside)
        (nas / "inside.3mf").symlink_to(nas / "Box" / "box.3mf")
        await scan(async_client, watched)
        names = await files_by_name(async_client)
        assert "inside.3mf" in names and "link.3mf" not in names

    async def test_read_error_skips_file_without_aborting(self, async_client, watched, monkeypatch):
        real_analyze = library_sync._analyze

        def flaky(entry):
            if entry.filename == "box.3mf":
                raise OSError(5, "Input/output error")  # what a flaky SMB mount returns
            return real_analyze(entry)

        monkeypatch.setattr(library_sync, "_analyze", flaky)
        result = await scan(async_client, watched)
        assert (result["added"], result["unreadable"], result["corrupt"]) == (1, 1, 0)

        monkeypatch.setattr(library_sync, "_analyze", real_analyze)
        assert (await scan(async_client, watched))["added"] == 1

    async def test_tracked_file_saved_corrupt_keeps_record(self, async_client, watched, nas):
        await scan(async_client, watched)
        before = (await files_by_name(async_client))["box.3mf"]
        path = nas / "Box" / "box.3mf"
        path.write_bytes(b"half-written")
        age(path, 120)
        assert (await scan(async_client, watched))["corrupt"] == 1
        after = (await files_by_name(async_client))["box.3mf"]
        assert (after["library_file_id"], after["status"]) == (before["library_file_id"], "corrupt")

    async def test_slicer_override_survives_edits(self, async_client, watched, nas):
        await scan(async_client, watched)
        file_id = (await files_by_name(async_client))["box.3mf"]["library_file_id"]
        resp = await async_client.put(f"/api/v1/farm/library/files/{file_id}/slicer", json={"slicer": "orcaslicer"})
        assert resp.json()["slicer"] == "orcaslicer"

        make_3mf(nas / "Box" / "box.3mf", BAMBU, body="<!-- v2 -->")
        await scan(async_client, watched)
        state = (await files_by_name(async_client))["box.3mf"]
        assert (state["slicer_detected"], state["slicer"]) == ("bambu_studio", "orcaslicer")

        resp = await async_client.put(f"/api/v1/farm/library/files/{file_id}/slicer", json={"slicer": "auto"})
        assert resp.json()["slicer"] == "bambu_studio"

    async def test_unwatched_folder_keeps_upstream_scan(self, async_client, nas):
        folder = await add_external(async_client, nas)
        assert await scan(async_client, folder) == {"status": "success", "added": 2, "removed": 0}
        assert (await async_client.get("/api/v1/farm/library/files")).json() == []

    async def test_scheduler_syncs_due_folders(self, async_client, db_session, watched):
        await library_sync.farm_library_scheduler.run_due()
        watch = await db_session.get(FarmWatchedFolder, watched["id"])
        assert watch.last_scan_at is not None
        assert watch.last_result["added"] == 2
        first_scan = watch.last_scan_at

        await library_sync.farm_library_scheduler.run_due()  # not due again yet
        await db_session.refresh(watch)
        assert watch.last_scan_at == first_scan


# ---------------------------------------------------------------- slicing


@pytest.mark.integration
class TestEmbeddedSlicing:
    @pytest.fixture
    def sidecar(self, monkeypatch, tmp_path):
        from backend.app.services.slicer_api import SlicerApiService, SliceResult

        calls = []
        output = make_3mf(tmp_path / "out.gcode.3mf", BAMBU).read_bytes()

        async def fake_slice(self, **kwargs):
            calls.append({"url": self.base_url, **kwargs})
            return SliceResult(content=output, print_time_seconds=3600, filament_used_g=12.5, filament_used_mm=4000.0)

        monkeypatch.setattr(SlicerApiService, "slice_without_profiles", fake_slice)
        return calls

    async def _slice(self, db_session, file_id, plate=1):
        from backend.app.farm.slicing import slice_embedded

        lib_file = await db_session.get(LibraryFile, file_id)
        state = await db_session.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == file_id))
        return await slice_embedded(db_session, lib_file=lib_file, state=state, plate=plate)

    async def test_routes_each_file_to_its_sidecar(self, async_client, db_session, watched, sidecar):
        await scan(async_client, watched)
        files = await files_by_name(async_client)
        await self._slice(db_session, files["P1S-PLA-hook.3mf"]["library_file_id"])
        await self._slice(db_session, files["box.3mf"]["library_file_id"])
        assert [c["url"] for c in sidecar] == [
            library.app_settings.slicer_api_url.rstrip("/"),
            library.app_settings.bambu_studio_api_url.rstrip("/"),
        ]
        assert all(c["export_3mf"] and c["plate"] == 1 for c in sidecar)

    async def test_override_picks_sidecar_and_output_is_saved(self, async_client, db_session, watched, sidecar):
        await scan(async_client, watched)
        file_id = (await files_by_name(async_client))["box.3mf"]["library_file_id"]
        await async_client.put(f"/api/v1/farm/library/files/{file_id}/slicer", json={"slicer": "orcaslicer"})

        response = await self._slice(db_session, file_id)
        assert sidecar[0]["url"] == library.app_settings.slicer_api_url.rstrip("/")
        out = await db_session.get(LibraryFile, response.library_file_id)
        assert out.filename == "box.gcode.3mf"
        assert out.file_metadata["sliced_from_library_file_id"] == file_id
        assert out.file_metadata["sliced_with"] == "orcaslicer"
        assert response.used_embedded_settings is True

    async def test_cad_export_is_rejected(self, async_client, watched, nas, sidecar):
        make_3mf(nas / "cad.3mf", {})
        await scan(async_client, watched)
        file_id = (await files_by_name(async_client))["cad.3mf"]["library_file_id"]
        resp = await async_client.post(f"/api/v1/farm/library/files/{file_id}/slice", json={"plate": 1})
        assert resp.status_code == 400
        assert sidecar == []
