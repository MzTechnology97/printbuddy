"""Built-in camera reconnect / cleanup behaviour (upstream 1.2.5.7, 1.3.0, #2580).

- The RTSP reconnect budget counts failures in a row, not every drop: a stock
  X1C ends each session after about a minute, and a lifetime budget of 30
  stopped the live view for good after about half an hour.
- The cleanup tick leaves a reconnecting stream's entry alone; removing it
  made the generator read "stopped" and end the live view.
- A SIGKILLed ffmpeg that is never reaped no longer parks the stream forever.
"""

import asyncio
from contextlib import suppress
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.app.api.routes import camera
from backend.app.services.camera_profiles import CameraProfile

pytestmark = pytest.mark.asyncio

FRAME = b"\xff\xd8frame\xff\xd9"


class _FakeServer:
    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _Stdout:
    def __init__(self, frames: int) -> None:
        self._frames = frames

    async def read(self, _size: int = -1) -> bytes:
        if self._frames <= 0:
            return b""
        self._frames -= 1
        return FRAME


class _Proc:
    _next_pid = 78000

    def __init__(self, frames: int) -> None:
        _Proc._next_pid += 1
        self.pid = _Proc._next_pid
        self.returncode = None
        self.stdout = _Stdout(frames)
        self.stderr = None

    def terminate(self) -> None:
        self.returncode = 0

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int:
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


@pytest.fixture
def rtsp(monkeypatch):
    """Fake ffmpeg sessions: each spawn takes the next entry's frame count."""
    sessions: list[int] = []
    spawned: list[int] = []
    delays: list[float] = []
    real_sleep = asyncio.sleep

    async def _fake_exec(*_args, **_kwargs):
        frames = sessions.pop(0) if sessions else 0
        spawned.append(frames)
        return _Proc(frames)

    async def _fake_proxy(_ip: str, _port: int):
        return 48998, _FakeServer()

    async def _recording_sleep(seconds, *args, **kwargs):
        delays.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(camera, "get_ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr(camera, "create_tls_proxy", _fake_proxy)
    monkeypatch.setattr(camera.asyncio, "create_subprocess_exec", _fake_exec)
    monkeypatch.setattr(camera.asyncio, "sleep", _recording_sleep)
    return sessions, spawned, delays


def _use_profile(monkeypatch, **kwargs):
    monkeypatch.setattr(camera, "get_camera_profile", lambda _model: CameraProfile(**kwargs))


async def _drain(stream, limit: int = 200) -> int:
    frames = 0
    async for chunk in stream:
        if b"image/jpeg" in chunk:
            frames += 1
        if frames >= limit:
            break
    return frames


def _stream():
    return camera.generate_rtsp_mjpeg_stream(
        ip_address="192.0.2.40",
        access_code="test-code",
        model="X1C",
        fps=10,
        stream_id="99-fanout-budget",
        disconnect_event=asyncio.Event(),
    )


async def test_routine_drops_never_use_up_the_budget(rtsp, monkeypatch):
    """Ten sessions that each deliver video and end, with a budget of two."""
    sessions, spawned, _ = rtsp
    _use_profile(monkeypatch, rtsp_reconnect_max=2, rtsp_reconnect_delay=0.2)
    sessions.extend([3] * 10)

    stream = _stream()
    frames = await asyncio.wait_for(_drain(stream, limit=30), timeout=10)
    with suppress(Exception):
        await stream.aclose()

    assert frames == 30
    assert len(spawned) == 10


async def test_failures_in_a_row_still_give_up(rtsp, monkeypatch):
    sessions, spawned, _ = rtsp
    _use_profile(monkeypatch, rtsp_reconnect_max=3, rtsp_reconnect_delay=0.2)
    sessions.extend([2])  # then every session fails without a frame

    frames = await asyncio.wait_for(_drain(_stream()), timeout=10)

    assert frames == 2
    # The good session, then three failed reconnects, then it gives up.
    assert spawned == [2, 0, 0, 0]


async def test_failures_back_off_and_a_good_session_resets_the_delay(rtsp, monkeypatch):
    sessions, _, delays = rtsp
    _use_profile(monkeypatch, rtsp_reconnect_max=6, rtsp_reconnect_delay=0.2, rtsp_reconnect_backoff_max=1.0)
    sessions.extend([1, 0, 0, 0, 1])

    await asyncio.wait_for(_drain(_stream()), timeout=10)

    reconnect_delays = [d for d in delays if d != 0.1]  # 0.1 = post-spawn startup check
    assert reconnect_delays == [0.2, 0.4, 0.8, 1.0, 0.2, 0.4, 0.8, 1.0, 1.0, 1.0]


# ---------------------------------------------------------------------------
# Cleanup tick vs a reconnecting stream (upstream 1.3.0, cf0ba31f1)
# ---------------------------------------------------------------------------

SID = "7-fanout-deadbeef"


@pytest.fixture
def dead_stream():
    camera._active_streams[SID] = SimpleNamespace(pid=999_999_999, returncode=0)
    yield
    camera._active_streams.pop(SID, None)
    camera._disconnect_events.pop(SID, None)


async def _cleanup():
    with patch.object(camera, "_scan_bambu_ffmpeg_pids", return_value=[]):
        await asyncio.wait_for(camera.cleanup_orphaned_streams(), timeout=2.0)


async def test_cleanup_keeps_entry_of_a_stream_about_to_redial(dead_stream):
    camera._disconnect_events[SID] = asyncio.Event()

    await _cleanup()

    assert SID in camera._active_streams
    assert camera.is_stream_active(7)


async def test_cleanup_removes_a_stopped_stream_entry(dead_stream):
    stopped = asyncio.Event()
    stopped.set()
    camera._disconnect_events[SID] = stopped

    await _cleanup()

    assert SID not in camera._active_streams


async def test_cleanup_removes_an_entry_nothing_owns(dead_stream):
    await _cleanup()

    assert SID not in camera._active_streams


# ---------------------------------------------------------------------------
# Bounded post-kill wait (#2580)
# ---------------------------------------------------------------------------


class _Unreapable:
    """ffmpeg that ignores SIGTERM and is never reaped after SIGKILL."""

    pid = 999_999_998
    returncode = None

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return 0  # pragma: no cover


async def test_terminate_ffmpeg_abandons_an_unreaped_process(monkeypatch):
    monkeypatch.setattr(camera, "_FFMPEG_KILL_TIMEOUT", 0.05)
    # 2 s SIGTERM grace + 0.05 s post-kill wait, never the unbounded hang.
    await asyncio.wait_for(camera._terminate_ffmpeg(_Unreapable(), "7-fanout"), timeout=5.0)
