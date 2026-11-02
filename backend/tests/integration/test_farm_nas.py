"""Printfarm (fork): NAS share configured from Settings and mirrored read-only.

The SMB server is faked by a local directory; no real host, share or user is used.
"""

import os
import shutil
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from backend.app.farm.nas import service
from backend.app.farm.nas.remote import RemoteEntry, check_name, split_path
from backend.app.models.settings import Settings
from backend.tests.integration.test_farm_library import BAMBU, age, make_3mf

SUB = "Stampa 3D"


class FakeRemote:
    """A share backed by a local dir; records every path it is asked for."""

    calls: list[tuple[str, ...]] = []
    fail = False

    def __init__(self, root: Path, password: str) -> None:
        self.root, self.password = root, password

    def _path(self, parts):
        FakeRemote.calls.append(tuple(parts))
        if FakeRemote.fail:
            raise ConnectionError("host unreachable")
        return self.root.joinpath(*(check_name(p) for p in parts))

    def scandir(self, parts):
        return [
            RemoteEntry(p.name, p.is_dir(), p.stat().st_size, p.stat().st_mtime) for p in self._path(parts).iterdir()
        ]

    def copy_to(self, parts, dest):
        with open(self._path(parts), "rb") as src:
            shutil.copyfileobj(src, dest)

    def close(self):
        pass


@pytest.fixture
def share(tmp_path, monkeypatch):
    root = tmp_path / "share"
    (root / "Other").mkdir(parents=True)
    (root / "credentials.txt").write_text("not for us")
    make_3mf(root / "Other" / "outside.3mf", BAMBU)
    make_3mf(root / SUB / "Box" / "box.3mf", BAMBU, "Bambu Lab P1S")
    (root / SUB / "notes.txt").write_text("ignored")
    fresh = make_3mf(root / SUB / "saving.3mf", BAMBU)
    os.utime(fresh, None)  # being saved right now

    FakeRemote.calls, FakeRemote.fail = [], False
    passwords = []

    def fake_remote(host, share_name, username, password):
        passwords.append(password)
        return FakeRemote(root, password)

    monkeypatch.setattr(service, "make_remote", fake_remote)
    monkeypatch.setattr(service, "mirror_dir", lambda: tmp_path / "data" / "farm_nas" / "mirror")
    FakeRemote.passwords = passwords
    return root


SETTINGS = {"host": "nas.test", "share": "share", "path": SUB, "username": "farm", "password": "s3cret"}


async def configure(client, **overrides):
    resp = await client.put("/api/v1/farm/nas", json={**SETTINGS, **overrides})
    assert resp.status_code == 200, resp.text
    return resp.json()


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
        await configure(async_client, password=None, scan_interval_seconds=600)
        await async_client.post("/api/v1/farm/nas/sync")
        assert FakeRemote.passwords[-1] == "s3cret"
        assert (await async_client.get("/api/v1/farm/nas")).json()["scan_interval_seconds"] == 600

    async def test_stored_password_never_follows_a_new_host(self, async_client, share):
        await configure(async_client)
        other = {**SETTINGS, "host": "attacker.test", "password": None}
        for method, url in (("put", "/api/v1/farm/nas"), ("post", "/api/v1/farm/nas/test")):
            resp = await getattr(async_client, method)(url, json=other)
            assert resp.status_code == 400 and "password again" in resp.json()["detail"], url
        resp = await async_client.post("/api/v1/farm/nas/browse", json={**other, "path": ""})
        assert resp.status_code == 400
        assert FakeRemote.passwords == []  # nothing was sent anywhere
        # Same host, user and share: the stored password is reused.
        resp = await async_client.post("/api/v1/farm/nas/test", json={**SETTINGS, "password": None})
        assert resp.status_code == 200 and FakeRemote.passwords == ["s3cret"]

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
        assert resp.json() == {"ok": True, "directories": 1, "print_files": 1}
        FakeRemote.fail = True
        resp = await async_client.post("/api/v1/farm/nas/test", json=SETTINGS)
        assert resp.status_code == 502 and "unreachable" in resp.json()["detail"]


@pytest.mark.integration
class TestNasMirror:
    async def test_sync_mirrors_only_print_files_of_the_subfolder(self, async_client, share, tmp_path):
        await configure(async_client)
        result = (await async_client.post("/api/v1/farm/nas/sync")).json()
        assert (result["nas"]["downloaded"], result["nas"]["pending"]) == (1, 1)
        assert result["added"] == 1

        mirror = tmp_path / "data" / "farm_nas" / "mirror"
        copied = sorted(p.relative_to(mirror).as_posix() for p in mirror.rglob("*") if p.is_file())
        assert copied == ["Box/box.3mf"]
        remote_mtime = os.stat(share / SUB / "Box" / "box.3mf").st_mtime
        assert abs(os.stat(mirror / "Box" / "box.3mf").st_mtime - remote_mtime) < 1
        assert all(parts[:1] == (SUB,) for parts in FakeRemote.calls)  # never outside the subfolder

        files = (await async_client.get("/api/v1/farm/library/files")).json()
        assert [(Path(f["path"]).name, f["printer_model"]) for f in files] == [("box.3mf", "P1S")]

        again = (await async_client.post("/api/v1/farm/nas/sync")).json()
        assert (again["nas"]["downloaded"], again["nas"]["unchanged"], again["added"]) == (0, 1, 0)

    async def test_changes_and_deletions_follow_the_share(self, async_client, share, tmp_path):
        await configure(async_client)
        await async_client.post("/api/v1/farm/nas/sync")
        before = (await async_client.get("/api/v1/farm/library/files")).json()[0]

        make_3mf(share / SUB / "Box" / "box.3mf", BAMBU, "Bambu Lab X1 Carbon", body="<!-- v2 -->")
        age(share / SUB / "Box" / "box.3mf", 120)
        result = (await async_client.post("/api/v1/farm/nas/sync")).json()
        assert (result["nas"]["downloaded"], result["updated"]) == (1, 1)
        after = (await async_client.get("/api/v1/farm/library/files")).json()[0]
        assert (after["library_file_id"], after["printer_model"]) == (before["library_file_id"], "X1C")

        (share / SUB / "Box" / "box.3mf").unlink()
        result = (await async_client.post("/api/v1/farm/nas/sync")).json()
        assert (result["nas"]["deleted"], result["missing"]) == (1, 1)
        assert not (tmp_path / "data" / "farm_nas" / "mirror" / "Box").exists()

    async def test_unreachable_nas_keeps_last_copy(self, async_client, share):
        await configure(async_client)
        await async_client.post("/api/v1/farm/nas/sync")
        FakeRemote.fail = True
        result = (await async_client.post("/api/v1/farm/nas/sync")).json()
        assert "unreachable" in result["nas"]["error"]
        assert result["missing"] == 0
        files = (await async_client.get("/api/v1/farm/library/files")).json()
        assert [f["status"] for f in files] == ["ok"]
        view = (await async_client.get("/api/v1/farm/nas")).json()
        assert "unreachable" in view["last_result"]["error"]


def test_mirror_skips_files_saved_moments_ago(tmp_path):
    from backend.app.farm.nas.mirror import mirror

    root = tmp_path / "share"
    make_3mf(root / SUB / "a.3mf", BAMBU)
    os.utime(root / SUB / "a.3mf", (time.time(), time.time()))
    FakeRemote.fail = False
    result = mirror(FakeRemote(root, ""), (SUB,), tmp_path / "m")
    assert (result["downloaded"], result["pending"]) == (0, 1)
