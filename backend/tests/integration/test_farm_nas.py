"""Printfarm (fork): NAS share configured from Settings and used straight over SMB.

The SMB server is faked by a local directory; no real host, share or user is used.
"""

import io
import os
import shutil
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.farm import library_sync
from backend.app.farm.models import FarmLibraryFile
from backend.app.farm.nas import access, cache, remote, service
from backend.app.farm.nas.remote import RemoteEntry, check_name, split_path
from backend.app.farm.plates.models import FarmPlate
from backend.app.models.library import LibraryFile
from backend.app.models.settings import Settings
from backend.tests.integration.test_farm_library import BAMBU, age, make_3mf
from backend.tests.integration.test_farm_plates import TWO_PLATES, make_project

SUB = "Stampa 3D"


class FakeRemote:
    """A share backed by a local dir; records every path it is asked for."""

    calls: list[tuple[str, tuple[str, ...]]] = []
    fail = False
    drops = 0  # next file reads refused, as by a NAS that just dropped the connection

    def __init__(self, root: Path, password: str) -> None:
        self.root, self.password = root, password

    def _path(self, op, parts):
        FakeRemote.calls.append((op, tuple(parts)))
        if FakeRemote.fail:
            raise ConnectionError("host unreachable")
        return self.root.joinpath(*(check_name(p) for p in parts))

    def scandir(self, parts):
        return [
            RemoteEntry(p.name, p.is_dir(), p.stat().st_size, p.stat().st_mtime)
            for p in self._path("list", parts).iterdir()
        ]

    def read(self, parts, fn):
        path = self._path("read", parts)
        if FakeRemote.drops:
            FakeRemote.drops -= 1
            raise ConnectionError("Connection refused")
        with open(path, "rb") as fh:
            return fn(fh)

    def copy_to(self, parts, dest):
        self.read(parts, lambda src: shutil.copyfileobj(src, dest))

    def makedirs(self, parts):
        self._path("mkdir", parts).mkdir(parents=True, exist_ok=True)

    def write(self, parts, data):
        self._path("write", parts).write_bytes(data)

    def rename(self, src, dst):
        target = self._path("rename", dst)
        if target.exists():
            raise FileExistsError(target.name)  # like SMB: never replaces
        self._path("rename", src).rename(target)

    def replace(self, src, dst):
        os.replace(self._path("replace", src), self._path("replace", dst))

    def close(self):
        pass


@pytest.fixture
def share(tmp_path, monkeypatch):
    root = tmp_path / "share"
    (root / "Other").mkdir(parents=True)
    (root / "credentials.txt").write_text("not for us")
    make_3mf(root / "Other" / "outside.3mf", BAMBU)
    make_3mf(root / SUB / "Box" / "box.3mf", BAMBU, "Bambu Lab P1S")
    make_project(root / SUB / "Hook" / "hook.3mf", TWO_PLATES)
    (root / SUB / "notes.txt").write_text("ignored")
    (root / SUB / "Box" / "photo.jpg").write_bytes(b"ignored: not a print file")
    fresh = make_3mf(root / SUB / "saving.3mf", BAMBU)
    os.utime(fresh, None)  # being saved right now

    FakeRemote.calls, FakeRemote.fail, FakeRemote.drops = [], False, 0
    passwords = []

    def fake_remote(host, share_name, username, password):
        passwords.append(password)
        return FakeRemote(root, password)

    monkeypatch.setattr(service, "make_remote", fake_remote)
    monkeypatch.setattr(service, "cache_dir", lambda: tmp_path / "data" / "farm_nas" / "cache")
    monkeypatch.setattr(remote, "RETRY_DELAYS", (0, 0))
    access.activate(None)
    library_sync._plates_checked.clear()
    FakeRemote.passwords = passwords
    yield root
    access.activate(None)


SETTINGS = {"host": "nas.test", "share": "share", "path": SUB, "username": "farm", "password": "s3cret"}


async def configure(client, **overrides):
    resp = await client.put("/api/v1/farm/nas", json={**SETTINGS, **overrides})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def sync(client):
    resp = await client.post("/api/v1/farm/nas/sync")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def files_by_name(client):
    return {Path(f["path"]).name: f for f in (await client.get("/api/v1/farm/library/files")).json()}


def cached(tmp_path):
    root = tmp_path / "data" / "farm_nas" / "cache"
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_split_path_rejects_escapes():
    assert split_path("Stampa 3D\\Box/") == ("Stampa 3D", "Box")
    for bad in ("Stampa 3D/../secret", "a/./b", "c:x", "a\x00b"):
        with pytest.raises(ValueError):
            split_path(bad)


@pytest.mark.integration
class TestNasSettings:
    async def test_password_is_write_only_and_encrypted(self, async_client, db_session, share):
        view = await configure(async_client)
        assert view["password_set"] is True and "password" not in view
        assert "s3cret" not in (await async_client.get("/api/v1/farm/nas")).text
        assert "s3cret" not in (await async_client.get("/api/v1/settings")).text
        stored = await db_session.scalar(select(Settings.value).where(Settings.key == "farm_nas_password"))
        assert stored.startswith("fernet:") and "s3cret" not in stored

        # Saving without a password keeps the stored one.
        await configure(async_client, password=None, scan_interval_seconds=600, cache_mb=1024)
        await sync(async_client)
        assert FakeRemote.passwords[-1] == "s3cret"
        view = (await async_client.get("/api/v1/farm/nas")).json()
        assert (view["scan_interval_seconds"], view["cache_mb"]) == (600, 1024)

    async def test_stored_password_never_follows_a_new_host(self, async_client, share):
        await configure(async_client)
        sent = list(FakeRemote.passwords)
        other = {**SETTINGS, "host": "attacker.test", "password": None}
        for method, url in (("put", "/api/v1/farm/nas"), ("post", "/api/v1/farm/nas/test")):
            resp = await getattr(async_client, method)(url, json=other)
            assert resp.status_code == 400 and "password again" in resp.json()["detail"], url
        resp = await async_client.post("/api/v1/farm/nas/browse", json={**other, "path": ""})
        assert resp.status_code == 400
        assert FakeRemote.passwords == sent  # nothing was sent anywhere
        # Same host, user and share: the stored password is reused.
        resp = await async_client.post("/api/v1/farm/nas/test", json={**SETTINGS, "password": None})
        assert resp.status_code == 200 and FakeRemote.passwords == [*sent, "s3cret"]

    async def test_share_root_and_traversal_rejected(self, async_client, share):
        for path in ("/", "Stampa 3D/../Other"):
            resp = await async_client.put("/api/v1/farm/nas", json={**SETTINGS, "path": path})
            assert resp.status_code == 400, path
        resp = await async_client.put("/api/v1/farm/nas", json={**SETTINGS, "host": "nas.test/share"})
        assert resp.status_code == 400

    async def test_refuses_clear_text_password(self, async_client, share, monkeypatch):
        monkeypatch.setattr(service, "is_encryption_active", lambda: False)
        resp = await async_client.put("/api/v1/farm/nas", json=SETTINGS)
        assert resp.status_code == 400 and "encryption" in resp.json()["detail"]

    async def test_browse_lists_directories_only(self, async_client, share):
        resp = await async_client.post("/api/v1/farm/nas/browse", json={**SETTINGS, "path": ""})
        assert resp.json() == {"path": "", "directories": ["Other", SUB]}

    async def test_connection_test(self, async_client, share):
        resp = await async_client.post("/api/v1/farm/nas/test", json=SETTINGS)
        assert resp.json() == {"ok": True, "directories": 2, "print_files": 1}
        FakeRemote.fail = True
        resp = await async_client.post("/api/v1/farm/nas/test", json=SETTINGS)
        assert resp.status_code == 502 and "unreachable" in resp.json()["detail"]


@pytest.mark.integration
class TestNasLibrary:
    async def test_scan_reads_metadata_and_plates_without_downloading(self, async_client, db_session, share, tmp_path):
        await configure(async_client)
        result = await sync(async_client)
        assert (result["added"], result["pending"]) == (2, 1)  # saving.3mf waits; photo and notes ignored

        files = await files_by_name(async_client)
        assert (files["box.3mf"]["printer_model"], files["hook.3mf"]["printer_model"]) == ("P1S", "P1S")
        assert cached(tmp_path) == []  # nothing was copied
        assert all(parts[:1] == (SUB,) for _, parts in FakeRemote.calls)  # never outside the subfolder
        assert not any(parts[-1].endswith((".txt", ".jpg")) for _, parts in FakeRemote.calls)
        plates = (
            await db_session.execute(
                select(FarmPlate.plate_index).where(FarmPlate.library_file_id == files["hook.3mf"]["library_file_id"])
            )
        ).scalars()
        assert sorted(plates) == [1, 2]

        reads = sum(op == "read" for op, _ in FakeRemote.calls)
        again = await sync(async_client)
        assert (again["added"], again["updated"]) == (0, 0)
        assert sum(op == "read" for op, _ in FakeRemote.calls) == reads  # unchanged files aren't opened

    async def test_changes_moves_and_deletions_follow_the_share(self, async_client, share):
        await configure(async_client)
        await sync(async_client)
        before = (await files_by_name(async_client))["box.3mf"]

        make_3mf(share / SUB / "Box" / "box.3mf", BAMBU, "Bambu Lab X1 Carbon", body="<!-- v2 -->")
        age(share / SUB / "Box" / "box.3mf", 120)
        assert (await sync(async_client))["updated"] == 1
        after = (await files_by_name(async_client))["box.3mf"]
        assert (after["library_file_id"], after["printer_model"]) == (before["library_file_id"], "X1C")
        assert after["sha256"] != before["sha256"]

        (share / SUB / "Archive").mkdir()
        (share / SUB / "Box" / "box.3mf").rename(share / SUB / "Archive" / "box-v2.3mf")
        result = await sync(async_client)
        assert (result["relinked"], result["added"], result["missing"]) == (1, 0, 0)

        (share / SUB / "Archive" / "box-v2.3mf").unlink()
        assert (await sync(async_client))["missing"] == 1

    async def test_paths_made_relative_at_startup_still_work(
        self, async_client, db_session, share, tmp_path, monkeypatch
    ):
        """Upstream strips the data dir from library paths at every startup: NAS rows must keep working."""
        from backend.app.core.config import settings as app_settings

        monkeypatch.setattr(app_settings, "base_dir", tmp_path / "data")  # the cache lives under the data dir
        await configure(async_client)
        await sync(async_client)
        box_id = (await files_by_name(async_client))["box.3mf"]["library_file_id"]
        prefix = f"{tmp_path / 'data'}/"
        for lib_file in (await db_session.execute(select(LibraryFile))).scalars():
            lib_file.file_path = lib_file.file_path.removeprefix(prefix)  # what the migration does
        await db_session.commit()

        result = await sync(async_client)
        assert (result["added"], result["relinked"], result["updated"], result["missing"]) == (0, 0, 0, 0)
        resp = await async_client.get(f"/api/v1/farm/library/files/{box_id}/project")
        assert resp.status_code == 200, resp.text
        lib_file = await db_session.get(LibraryFile, box_id)
        assert not lib_file.file_path.startswith("/")
        assert await cache.ensure_local(db_session, lib_file)
        assert (tmp_path / "data" / lib_file.file_path).read_bytes() == (share / SUB / "Box" / "box.3mf").read_bytes()

    async def test_unreachable_nas_changes_nothing(self, async_client, share):
        await configure(async_client)
        await sync(async_client)
        FakeRemote.fail = True
        result = await sync(async_client)
        assert result["status"] == "error" and "unreachable" in result["error"]
        assert [f["status"] for f in (await files_by_name(async_client)).values()] == ["ok", "ok"]
        view = (await async_client.get("/api/v1/farm/nas")).json()
        assert "unreachable" in view["last_result"]["error"]


@pytest.mark.integration
class TestNasCache:
    async def test_file_is_cached_on_use_only_and_refreshed_when_changed(
        self, async_client, db_session, share, tmp_path
    ):
        await configure(async_client)
        await sync(async_client)
        box_id = (await files_by_name(async_client))["box.3mf"]["library_file_id"]
        assert cached(tmp_path) == []

        # An upstream route that needs the file: fetched into the cache first.
        resp = await async_client.get(f"/api/v1/library/files/{box_id}/download")
        assert resp.status_code == 200 and resp.content == (share / SUB / "Box" / "box.3mf").read_bytes()
        assert cached(tmp_path) == ["Box/box.3mf"]

        # Used again: no new download. Changed on the NAS: the stale copy is replaced.
        lib_file = await db_session.get(LibraryFile, box_id)
        reads = sum(op == "read" for op, _ in FakeRemote.calls)
        assert await cache.ensure_local(db_session, lib_file)
        assert sum(op == "read" for op, _ in FakeRemote.calls) == reads
        make_3mf(share / SUB / "Box" / "box.3mf", BAMBU, "Bambu Lab P1S", body="<!-- v2 -->")
        age(share / SUB / "Box" / "box.3mf", 120)
        await sync(async_client)
        await db_session.refresh(lib_file)
        assert await cache.ensure_local(db_session, lib_file)
        assert Path(lib_file.file_path).read_bytes() == (share / SUB / "Box" / "box.3mf").read_bytes()

    async def test_refused_connection_is_retried(self, async_client, db_session, share):
        await configure(async_client)
        await sync(async_client)
        lib_file = await db_session.get(LibraryFile, (await files_by_name(async_client))["box.3mf"]["library_file_id"])
        FakeRemote.drops = 2  # refused twice, then the NAS accepts again
        assert await cache.ensure_local(db_session, lib_file)
        assert Path(lib_file.file_path).read_bytes() == (share / SUB / "Box" / "box.3mf").read_bytes()
        assert not list(Path(lib_file.file_path).parent.glob(".*.part"))

        os.remove(lib_file.file_path)
        FakeRemote.drops = 99  # stays unreachable: the caller gets "not available", nothing half-written
        assert not await cache.ensure_local(db_session, lib_file)
        assert not list(Path(lib_file.file_path).parent.iterdir())
        FakeRemote.drops = 0

    async def test_orphan_states_are_removed(self, db_session):
        db_session.add(FarmLibraryFile(watch_folder_id=999_999, path="/gone/a.3mf", mtime=0, size=0))
        await db_session.commit()
        await library_sync.cleanup_orphans(db_session)
        assert (
            await db_session.scalar(select(FarmLibraryFile).where(FarmLibraryFile.watch_folder_id == 999_999)) is None
        )


@pytest.fixture
def sidecar(monkeypatch, tmp_path):
    """Fake slicer: every call returns a new, distinct output."""
    from backend.app.services.slicer_api import SlicerApiService, SliceResult

    calls = []

    async def fake_slice(self, **kwargs):
        calls.append(kwargs)
        out = make_3mf(tmp_path / "out" / f"{len(calls)}.gcode.3mf", BAMBU, body=f"<!-- slice {len(calls)} -->")
        return SliceResult(
            content=out.read_bytes(), print_time_seconds=60 * len(calls), filament_used_g=1.0, filament_used_mm=10.0
        )

    monkeypatch.setattr(SlicerApiService, "slice_without_profiles", fake_slice)
    return calls


async def slice_now(db_session, file_id, plate=None):
    from backend.app.farm.slicing import slice_embedded

    lib_file = await db_session.get(LibraryFile, file_id)
    state = await db_session.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == file_id))
    await db_session.refresh(state)
    return await slice_embedded(db_session, lib_file=lib_file, state=state, plate=plate)


@pytest.mark.integration
class TestNasSlicing:
    async def test_new_slice_replaces_the_previous_output_of_the_same_plate(
        self, async_client, db_session, share, sidecar
    ):
        await configure(async_client)
        await sync(async_client)
        box_id = (await files_by_name(async_client))["box.3mf"]["library_file_id"]
        out_dir = share / SUB / "Box" / "Printbuddy"

        first = await slice_now(db_session, box_id)
        assert first.name == "box.gcode.3mf"
        v1 = (out_dir / "box.gcode.3mf").read_bytes()

        # Sliced again (same plate: "no plate" is plate 1): same row, same file, new content.
        second = await slice_now(db_session, box_id, plate=1)
        assert second.library_file_id == first.library_file_id
        assert sorted(p.name for p in out_dir.iterdir()) == ["box.gcode.3mf"]  # replaced, no copy or temp left
        assert (out_dir / "box.gcode.3mf").read_bytes() != v1
        out = await db_session.get(LibraryFile, first.library_file_id)
        await db_session.refresh(out)
        assert Path(out.file_path).read_bytes() == (out_dir / "box.gcode.3mf").read_bytes()  # cache copy
        assert out.file_metadata["print_time_seconds"] == 120

        # Another plate is another output.
        third = await slice_now(db_session, box_id, plate=2)
        assert third.library_file_id != first.library_file_id
        assert sorted(p.name for p in out_dir.iterdir()) == ["box - plate 2.gcode.3mf", "box.gcode.3mf"]

        # The next scan sees the outputs as known; evicted from the cache they come back from the NAS.
        result = await sync(async_client)
        assert (result["added"], result["updated"], result["relinked"]) == (0, 0, 0)
        os.remove(out.file_path)
        assert await cache.ensure_local(db_session, out)
        assert Path(out.file_path).read_bytes() == (out_dir / "box.gcode.3mf").read_bytes()

    async def test_plates_find_their_nas_output(self, async_client, db_session, share, sidecar):
        await configure(async_client)
        await sync(async_client)
        hook_id = (await files_by_name(async_client))["hook.3mf"]["library_file_id"]
        out = await slice_now(db_session, hook_id)  # the default plate, as the slice dialog sends it

        plates = (await async_client.get(f"/api/v1/farm/library/files/{hook_id}/plates")).json()
        assert [(p["plate_index"], p["sliced_file_id"]) for p in plates] == [(1, out.library_file_id), (2, None)]

    async def test_project_saved_again_is_resliced_automatically(self, async_client, db_session, share, sidecar):
        import asyncio

        from backend.app.farm import reslice
        from backend.app.services.slice_dispatch import slice_dispatch

        reslice._running.clear()
        await configure(async_client)
        await sync(async_client)
        hook_id = (await files_by_name(async_client))["hook.3mf"]["library_file_id"]
        out = await slice_now(db_session, hook_id)
        before = (share / SUB / "Hook" / "Printbuddy" / "hook.gcode.3mf").read_bytes()

        # Saved again from the PC under the same name, with different settings.
        changed = {1: [("Hook", 1)] * 6 + [("Base", 2)], 2: TWO_PLATES[2]}
        make_project(share / SUB / "Hook" / "hook.3mf", changed, printer_model="Bambu Lab X1 Carbon")
        age(share / SUB / "Hook" / "hook.3mf", 120)
        assert (await sync(async_client))["updated"] == 1

        for _ in range(200):  # the re-slice runs as a background slice job
            jobs = [j for j in slice_dispatch._jobs.values() if j.source_id == hook_id]
            if jobs and all(j.status in ("completed", "failed") for j in jobs):
                break
            await asyncio.sleep(0.05)
        assert [j.status for j in jobs] == ["completed"]
        assert len(sidecar) == 2  # the first slice and the automatic one

        state = await db_session.scalar(select(FarmLibraryFile).where(FarmLibraryFile.library_file_id == hook_id))
        await db_session.refresh(state)
        resliced = await db_session.get(LibraryFile, out.library_file_id)  # same row: queued prints follow
        await db_session.refresh(resliced)
        assert resliced.file_metadata["sliced_from_sha256"] == state.sha256
        assert (share / SUB / "Hook" / "Printbuddy" / "hook.gcode.3mf").read_bytes() != before
        plates = (await async_client.get(f"/api/v1/farm/library/files/{hook_id}/plates")).json()
        assert (plates[0]["sliced_file_id"], plates[0]["print_time_seconds"]) == (out.library_file_id, 120)

        # A project without outputs is not sliced when it changes.
        make_3mf(share / SUB / "Box" / "box.3mf", BAMBU, "Bambu Lab X1 Carbon", body="<!-- v2 -->")
        age(share / SUB / "Box" / "box.3mf", 120)
        assert (await sync(async_client))["updated"] == 1
        await asyncio.sleep(0.2)
        assert len(sidecar) == 2


@pytest.mark.integration
class TestNasUpload:

    async def test_a_plate_gone_from_the_project_is_not_resliced(self, async_client, db_session, share, sidecar):
        """Saved again with fewer plates: the output of a vanished plate is left alone, not re-sliced."""
        import asyncio

        from backend.app.farm import reslice
        from backend.app.services.slice_dispatch import slice_dispatch

        reslice._running.clear()
        await configure(async_client)
        await sync(async_client)
        hook_id = (await files_by_name(async_client))["hook.3mf"]["library_file_id"]
        await slice_now(db_session, hook_id, plate=2)

        make_project(share / SUB / "Hook" / "hook.3mf", {1: TWO_PLATES[1]}, printer_model="Bambu Lab X1 Carbon")
        age(share / SUB / "Hook" / "hook.3mf", 120)
        assert (await sync(async_client))["updated"] == 1

        for _ in range(200):
            jobs = [j for j in slice_dispatch._jobs.values() if j.source_id == hook_id]
            if jobs and all(j.status in ("completed", "failed") for j in jobs):
                break
            await asyncio.sleep(0.05)
        assert {j.status for j in jobs} == {"completed"}  # nothing to slice, nothing failed
        assert len(sidecar) == 1  # only the first slice: plate 2 is gone
        plates = (await async_client.get(f"/api/v1/farm/library/files/{hook_id}/plates")).json()
        assert [p["plate_index"] for p in plates] == [1]

    async def _folder_id(self, db_session, name):
        from backend.app.models.library import LibraryFolder

        return await db_session.scalar(select(LibraryFolder.id).where(LibraryFolder.name == name))

    async def test_upload_goes_onto_the_nas_without_replacing(self, async_client, db_session, share, tmp_path):
        await configure(async_client)
        await sync(async_client)
        box = await self._folder_id(db_session, "Box")
        before = (share / SUB / "Box" / "box.3mf").read_bytes()
        mine = make_3mf(tmp_path / "upload.3mf", BAMBU, "Bambu Lab A1", body="<!-- uploaded -->").read_bytes()

        resp = await async_client.post(
            f"/api/v1/library/files?folder_id={box}", files={"file": ("box.3mf", mine, "application/octet-stream")}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["filename"] == "box (2).3mf"  # the name was taken: never replaced
        assert (share / SUB / "Box" / "box (2).3mf").read_bytes() == mine
        assert (share / SUB / "Box" / "box.3mf").read_bytes() == before
        assert not any(p.name.startswith(".") for p in (share / SUB / "Box").iterdir())
        lib_file = await db_session.get(LibraryFile, resp.json()["id"])
        assert Path(lib_file.file_path).read_bytes() == mine  # cached copy
        assert (await files_by_name(async_client))["box (2).3mf"]["printer_model"] == "A1"

        result = await sync(async_client)
        assert (result["added"], result["updated"], result["relinked"]) == (0, 0, 0)  # already known

    async def test_only_print_files_and_nothing_is_deleted_or_moved(self, async_client, db_session, share):
        await configure(async_client)
        await sync(async_client)
        box = await self._folder_id(db_session, "Box")
        resp = await async_client.post(
            f"/api/v1/library/files?folder_id={box}", files={"file": ("notes.png", b"\x89PNG\r\n", "image/png")}
        )
        assert resp.status_code == 400
        assert not (share / SUB / "Box" / "notes.png").exists()

        box_id = (await files_by_name(async_client))["box.3mf"]["library_file_id"]
        hook = await self._folder_id(db_session, "Hook")
        resp = await async_client.post("/api/v1/library/files/move", json={"file_ids": [box_id], "folder_id": hook})
        assert resp.status_code == 403 or resp.json().get("moved", 0) == 0
        # Upstream "delete" of an external file only drops the library row: the NAS file stays
        # and the next scan brings it back.
        assert (await async_client.delete(f"/api/v1/library/files/{box_id}")).status_code == 200
        assert (share / SUB / "Box" / "box.3mf").exists()
        assert not any(op in ("write", "rename") for op, _ in FakeRemote.calls)
        assert (await sync(async_client))["added"] == 1


def test_lru_eviction_keeps_recent_files(tmp_path):
    root = tmp_path / "cache"
    now = time.time()
    for name, age_s in (("old.3mf", 7200), ("older.3mf", 9000), ("recent.3mf", 60)):
        path = root / "P" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 1000)
        os.utime(path, (now - age_s, now - age_s))
    assert cache.evict(root, 2500) == 1000  # least recently used goes first
    assert sorted(p.name for p in (root / "P").iterdir()) == ["old.3mf", "recent.3mf"]
    cache.evict(root, 0)  # files used in the last hour are kept even over the limit
    assert [p.name for p in (root / "P").iterdir()] == ["recent.3mf"]


def test_smb_refused_connection_is_retryable(monkeypatch):
    import smbclient

    from backend.app.farm.nas.remote import NasConnectionError, SmbRemote

    def refused(*_args, **_kwargs):
        raise ValueError("Failed to connect to 'nas.test:445': [Errno 111] Connection refused")

    monkeypatch.setattr(smbclient, "open_file", refused)
    with pytest.raises(NasConnectionError, match="refused"):
        SmbRemote("nas.test", "share", "farm", "x").copy_to((SUB, "a.3mf"), io.BytesIO())


def test_unanswered_request_is_dropped_and_frees_the_session(monkeypatch):
    """smbprotocol waits for a response forever: the watchdog drops the connection instead."""
    import threading

    from smbprotocol.exceptions import SMBConnectionClosed

    from backend.app.farm.nas.remote import NasBusyError, NasConnectionError, SmbRemote

    monkeypatch.setattr(remote, "OP_TIMEOUT", 0.2)
    monkeypatch.setattr(remote, "LOCK_TIMEOUT", 0.3)
    nas = SmbRemote("nas.test", "share", "farm", "x")
    dropped = threading.Event()

    class StuckConnection:  # what a NAS that never answers looks like from the caller
        def disconnect(self, close=True):
            assert close is False  # no polite logoff: it would wait on the server too
            dropped.set()

    nas._cache["nas.test:445"] = StuckConnection()

    with pytest.raises(NasConnectionError), nas._session():
        assert dropped.wait(5), "the watchdog never fired"
        raise SMBConnectionClosed("socket closed")  # what the woken request raises
    assert nas._cache == {}  # the next request opens a new connection
    with nas._session():  # and the session is free again
        pass

    # A session held for too long makes the next caller give up instead of waiting forever.
    holding = threading.Event()
    release = threading.Event()

    def hold():
        with nas._session():
            holding.set()
            release.wait(5)

    monkeypatch.setattr(remote, "OP_TIMEOUT", 10)
    worker = threading.Thread(target=hold)
    worker.start()
    holding.wait(5)
    with pytest.raises(NasBusyError), nas._session():
        pass
    release.set()
    worker.join()


async def test_nas_calls_run_in_their_own_pool():
    import threading

    assert (await access.run(lambda: threading.current_thread().name)).startswith("nas")
