"""Per-printer FTPS transfer lock and overall caps on the async FTP helpers.

Ports of upstream #2957/#2529 (one transfer at a time per printer)
and #2572 (no helper may wait forever on the executor or a dead printer).
"""

import asyncio
import threading
import time

import pytest

from backend.app.services import bambu_ftp


class FakeClient:
    """Stands in for BambuFTPClient; records how many transfers overlap per printer."""

    A1_MODELS: set = set()
    _mode_cache: dict = {}
    active: dict[str, int] = {}
    peak: dict[str, int] = {}
    events: list[tuple[str, str, float]] = []
    lock = threading.Lock()
    transfer_seconds = 0.2
    connect_seconds = 0.0

    def __init__(self, ip_address, *args, **kwargs):
        self.ip = ip_address

    @classmethod
    def cache_mode(cls, ip, mode):
        pass

    def connect(self):
        time.sleep(self.connect_seconds)
        return True

    def disconnect(self):
        pass

    def _enter(self, what):
        with self.lock:
            self.active[self.ip] = self.active.get(self.ip, 0) + 1
            self.peak[self.ip] = max(self.peak.get(self.ip, 0), self.active[self.ip])
            self.events.append((what, "start", time.monotonic()))

    def _leave(self, what):
        with self.lock:
            self.active[self.ip] -= 1
            self.events.append((what, "end", time.monotonic()))

    def upload_file(self, local_path, remote_path, progress_callback=None):
        self._enter(remote_path)
        try:
            deadline = time.monotonic() + self.transfer_seconds
            while time.monotonic() < deadline:
                time.sleep(0.02)
                try:
                    progress_callback(1, 2)
                except Exception:
                    return False  # upload_file's stop path
            return True
        finally:
            self._leave(remote_path)

    def download_to_file(self, remote_path, local_path):
        self._enter(remote_path)
        try:
            time.sleep(self.transfer_seconds)
            return True
        finally:
            self._leave(remote_path)

    def delete_file(self, remote_path):
        return True

    def download_file(self, remote_path):
        return b"data"

    def get_storage_info(self):
        return {}


@pytest.fixture(autouse=True)
def fake_client(monkeypatch):
    FakeClient.active, FakeClient.peak, FakeClient.events = {}, {}, []
    FakeClient.transfer_seconds, FakeClient.connect_seconds = 0.2, 0.0
    monkeypatch.setattr(bambu_ftp, "BambuFTPClient", FakeClient)
    return FakeClient


def _upload(ip, tmp_path, name, **kwargs):
    local = tmp_path / name
    local.write_bytes(b"x")
    return bambu_ftp.upload_file_async(ip, "code", local, f"/{name}", **kwargs)


@pytest.mark.asyncio
async def test_uploads_to_one_printer_never_overlap(tmp_path):
    results = await asyncio.gather(*(_upload("printer-a.test", tmp_path, f"f{i}.3mf") for i in range(3)))
    assert results == [True, True, True]
    assert FakeClient.peak["printer-a.test"] == 1


@pytest.mark.asyncio
async def test_different_printers_transfer_in_parallel(tmp_path):
    await asyncio.gather(_upload("printer-a.test", tmp_path, "a.3mf"), _upload("printer-b.test", tmp_path, "b.3mf"))
    starts = [t for _, kind, t in FakeClient.events if kind == "start"]
    assert abs(starts[0] - starts[1]) < FakeClient.transfer_seconds


@pytest.mark.asyncio
async def test_download_waits_for_the_printer_then_goes_ahead(tmp_path, monkeypatch):
    """The download gate is soft: a print must not lose its 3MF to queueing."""
    monkeypatch.setattr(bambu_ftp, "_DOWNLOAD_GATE_WAIT_SECONDS", 0.1)
    async with bambu_ftp._printer_transfer("printer-a.test"):
        ok = await bambu_ftp.download_file_try_paths_async("printer-a.test", "code", ["/a.3mf"], tmp_path / "a.3mf")
    assert ok is True


@pytest.mark.asyncio
async def test_download_queues_behind_another_download(tmp_path):
    await asyncio.gather(
        bambu_ftp.download_file_try_paths_async("printer-a.test", "code", ["/a.3mf"], tmp_path / "a.3mf"),
        bambu_ftp.download_file_async("printer-a.test", "code", "/b.3mf", tmp_path / "b.3mf"),
    )
    assert FakeClient.peak["printer-a.test"] == 1


@pytest.mark.asyncio
async def test_timed_out_upload_is_stopped_before_the_printer_is_handed_on(tmp_path):
    """wait_for cannot stop an executor thread; the zombie must not stream beside the next upload (#2529)."""
    FakeClient.transfer_seconds = 5.0
    slow = asyncio.create_task(_upload("printer-a.test", tmp_path, "slow.3mf", timeout=0.2))
    await asyncio.sleep(0.05)
    FakeClient.transfer_seconds = 0.05
    nxt = asyncio.create_task(_upload("printer-a.test", tmp_path, "next.3mf"))

    assert await asyncio.wait_for(slow, timeout=3) is False
    assert await asyncio.wait_for(nxt, timeout=3) is True
    assert FakeClient.peak["printer-a.test"] == 1
    slow_end = next(t for name, kind, t in FakeClient.events if name == "/slow.3mf" and kind == "end")
    next_start = next(t for name, kind, t in FakeClient.events if name == "/next.3mf" and kind == "start")
    assert next_start >= slow_end


@pytest.mark.asyncio
async def test_try_paths_has_an_overall_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(bambu_ftp, "_TRANSFER_UNWIND_SECONDS", 0.05)
    FakeClient.transfer_seconds = 1.0
    started = time.monotonic()
    ok = await bambu_ftp.download_file_try_paths_async(
        "printer-a.test", "code", ["/a.3mf", "/b.3mf"], tmp_path / "a.3mf", timeout=0.1
    )
    assert ok is False
    assert time.monotonic() - started < 0.9


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call", "failure"),
    [
        (lambda: bambu_ftp.delete_file_async("printer-a.test", "code", "/a.3mf", timeout=0.1), False),
        (lambda: bambu_ftp.download_file_bytes_async("printer-a.test", "code", "/a.3mf", timeout=0.1), None),
        (lambda: bambu_ftp.get_storage_info_async("printer-a.test", "code", timeout=0.1), None),
    ],
)
async def test_helpers_give_up_on_a_printer_that_never_answers(call, failure):
    """#2572: the socket timeout bounds a running worker, not the wait for one."""
    FakeClient.connect_seconds = 0.6
    started = time.monotonic()
    assert await call() is failure
    assert time.monotonic() - started < 0.5
