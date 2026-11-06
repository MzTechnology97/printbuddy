"""Moonraker websocket push (fork): a fake Moonraker on localhost speaks JSON-RPC like the real one.

Message shapes follow Moonraker's API (printer.objects.list / subscribe, notify_status_update); data is made up.
"""

import json
import queue
import threading
import time

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve

from backend.app.services.printer_providers.moonraker import MoonrakerPrinterClient, _ws_is_event

OBJECTS = ["webhooks", "print_stats", "virtual_sdcard", "extruder", "configfile", "filament_switch_sensor fs"]
STATUS = {
    "webhooks": {"state": "ready", "state_message": "Printer is ready"},
    "print_stats": {"state": "printing", "filename": "part.gcode", "print_duration": 60.0},
    "virtual_sdcard": {"progress": 0.25},
    "extruder": {"temperature": 219.6, "target": 220.0},
    "filament_switch_sensor fs": {"filament_detected": True, "enabled": True},
}


@pytest.fixture
def moonraker():
    """A fake Moonraker websocket: answers the requests, sends what the test puts in ``outbox``."""
    outbox: queue.Queue = queue.Queue()
    calls: list[tuple[str, dict]] = []

    def handler(ws):
        while True:
            try:
                raw = ws.recv(timeout=0.05)
            except TimeoutError:
                while not outbox.empty():
                    ws.send(json.dumps(outbox.get()))
                continue
            except ConnectionClosed:
                return
            message = json.loads(raw)
            calls.append((message["method"], message.get("params") or {}))
            result = {
                "printer.objects.list": {"objects": OBJECTS},
                "printer.objects.subscribe": {"eventtime": 1.0, "status": json.loads(json.dumps(STATUS))},
            }.get(message["method"], {})
            ws.send(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}))

    server = serve(handler, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield {"port": server.socket.getsockname()[1], "outbox": outbox, "calls": calls}
    server.shutdown()


def _wait(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return False


def test_websocket_serves_the_polls_and_pushes_a_pause(moonraker, monkeypatch):
    states = []
    client = MoonrakerPrinterClient(
        f"http://127.0.0.1:{moonraker['port']}", on_state_change=lambda state: states.append(state.state)
    )
    http = []
    monkeypatch.setattr(client, "_get", lambda path, **kwargs: http.append(path) or {})
    client.start_websocket()
    try:
        assert _wait(client._ws_fresh)
        subscribed = next(params for method, params in moonraker["calls"] if method == "printer.objects.subscribe")
        assert "configfile" not in subscribed["objects"]  # large: still read over HTTP when needed
        assert "print_stats" in subscribed["objects"]

        assert client.check_staleness() is True
        assert (client.state.state, client.state.progress) == ("RUNNING", 25.0)
        assert not [p for p in http if p.startswith("printer/objects")]  # the poll read the websocket's cache

        # Moonraker pushes the pause: the status changes without waiting for the next poll.
        moonraker["outbox"].put(
            {"jsonrpc": "2.0", "method": "notify_status_update", "params": [{"print_stats": {"state": "paused"}}, 2.0]}
        )
        assert _wait(lambda: client.state.state == "PAUSE")
        assert states[-1] == "PAUSE"
    finally:
        client.stop_websocket()
    assert not client._ws_fresh()  # stopped: polls go back to HTTP


def test_websocket_cache_answers_only_what_it_holds(moonraker):
    client = MoonrakerPrinterClient(f"http://127.0.0.1:{moonraker['port']}")
    assert client._ws_cached(["print_stats"]) is None  # not started: HTTP
    client.start_websocket()
    try:
        assert _wait(client._ws_fresh)
        assert client._ws_cached(["configfile"]) is None  # skipped object: HTTP
        assert client._ws_cached(["print_stats", "heater_bed"]) == {"print_stats": STATUS["print_stats"]}
        assert client._available_objects() == OBJECTS
        moonraker["outbox"].put({"jsonrpc": "2.0", "method": "notify_klippy_disconnected", "params": []})
        assert _wait(lambda: not client._ws_fresh())  # Klipper gone: its objects too
    finally:
        client.stop_websocket()


def test_websocket_events_that_update_the_status_at_once():
    assert _ws_is_event("print_stats", {"state": "complete"})
    assert _ws_is_event("webhooks", {"state": "shutdown"})
    assert _ws_is_event("filament_switch_sensor fs", {"filament_detected": False})
    assert not _ws_is_event("extruder", {"temperature": 220.1})
    assert not _ws_is_event("print_stats", {"print_duration": 61.0})


def test_websocket_url_follows_the_moonraker_url():
    assert MoonrakerPrinterClient("http://printer.local:7125")._ws_url() == "ws://printer.local:7125/websocket"
    assert (
        MoonrakerPrinterClient("https://printer.local/moonraker/")._ws_url()
        == "wss://printer.local/moonraker/websocket"
    )
