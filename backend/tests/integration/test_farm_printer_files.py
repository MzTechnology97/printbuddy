"""Printfarm (fork): fingerprinted queue files on printers, reuse and clean-up of old ones. No printer is contacted."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from backend.app.farm import printer_files
from backend.app.farm.printer_files import FarmPrinterFile


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Sopra.3mf", "Sopra__{h}.3mf"),
        ("Sopra_plate2.gcode", "Sopra_plate2__{h}.gcode"),
        ("Sopra.gcode.3mf", "Sopra__{h}.gcode.3mf"),
        ("Sopra__deadbeef.3mf", "Sopra__{h}.3mf"),  # a changed file gets a new fingerprint, not two
    ],
)
def test_fingerprint_in_the_name(name, expected):
    import hashlib

    h = hashlib.sha256(b"G28").hexdigest()[:8]
    assert printer_files.with_fingerprint(name, b"G28") == expected.format(h=h)


@pytest.mark.asyncio
async def test_reuse_only_when_the_printer_lists_the_file(async_client, db_session):
    async def names(*args):
        return {"Sopra__1a2b3c4d.3mf", "manuale.3mf"}

    with patch.object(printer_files, "_list_names", names):
        assert await printer_files.check_reusable(1, "bambu", "ip", "code", "/Sopra__1a2b3c4d.3mf", "A1") is True
        assert await printer_files.check_reusable(1, "bambu", "ip", "code", "/Sopra__99999999.3mf", "A1") is False
        # Names without a fingerprint are never treated as reusable.
        assert await printer_files.check_reusable(1, "bambu", "ip", "code", "/manuale.3mf", "A1") is False

    with patch("backend.app.core.database.async_session", _session_factory(db_session)):
        assert await printer_files.before_upload(1, "/Sopra__1a2b3c4d.3mf") is True  # skip the upload
        assert await printer_files.before_upload(1, "/Sopra__1a2b3c4d.3mf") is False  # used once only
        assert await printer_files.before_upload(1, "/manuale.3mf") is False
    rows = (await db_session.execute(select(FarmPrinterFile))).scalars().all()
    # Every file put on a printer is recorded for the clean-up; only fingerprinted names are reused.
    assert sorted((r.printer_id, r.remote_path) for r in rows) == [(1, "/Sopra__1a2b3c4d.3mf"), (1, "/manuale.3mf")]


@pytest.mark.asyncio
async def test_cleanup_deletes_only_old_files_on_idle_printers(db_session, printer_factory):
    idle = await printer_factory(name="A1 uno", model="A1")
    busy = await printer_factory(name="A1 due", model="A1")
    now = printer_files._now()
    db_session.add_all(
        [
            FarmPrinterFile(
                printer_id=idle.id, remote_path="/vecchio__11111111.3mf", last_used_at=now - timedelta(days=31)
            ),
            FarmPrinterFile(
                printer_id=idle.id, remote_path="/recente__22222222.3mf", last_used_at=now - timedelta(days=2)
            ),
            FarmPrinterFile(
                printer_id=busy.id, remote_path="/vecchio__33333333.3mf", last_used_at=now - timedelta(days=40)
            ),
        ]
    )
    await db_session.commit()
    deleted = []

    async def fake_delete(ip, code, remote_path, **kwargs):
        deleted.append(remote_path)
        return True

    with (
        patch("backend.app.services.bambu_ftp.delete_file_async", fake_delete),
        patch(
            "backend.app.services.print_scheduler.scheduler._is_printer_idle",
            lambda printer_id, require_plate_clear=True: printer_id == idle.id,
        ),
    ):
        assert await printer_files.cleanup(db_session) == 1
    assert deleted == ["/vecchio__11111111.3mf"]
    left = sorted(r.remote_path for r in (await db_session.execute(select(FarmPrinterFile))).scalars())
    assert left == ["/recente__22222222.3mf", "/vecchio__33333333.3mf"]


def _session_factory(db_session):
    class _Ctx:
        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()
