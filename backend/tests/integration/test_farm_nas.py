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
