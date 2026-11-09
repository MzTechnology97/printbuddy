"""K-profile restore after a printer restart (upstream #3219).

Driven through a real BambuMQTTClient with a fake paho client: every command
the guard sends is captured from the fake ``publish``; nothing reaches a printer.
"""

import json
from unittest.mock import MagicMock

import pytest

from backend.app.services import kprofile_drift

PLA = {"tray_type": "PLA", "tray_info_idx": "GFA00", "tray_color": "FFFFFFFF", "tag_uid": "AB12CD34EF567890"}
PETG = {"tray_type": "PETG", "tray_info_idx": "GFG00", "tray_color": "000000FF", "tag_uid": "AB12CD34EF567890"}


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(kprofile_drift.time, "monotonic", c)
    return c


@pytest.fixture
def client(clock):
    from backend.app.services.bambu_mqtt import BambuMQTTClient

    c = BambuMQTTClient(ip_address="192.0.2.1", serial_number="DRIFTTEST", access_code="12345678")
    c._client = MagicMock()
    c._client.subscribe.return_value = (0, 1)
    c._on_connect(c._client, None, None, 0)
    return c


def _push(client, cali_idx, filament=PLA, gcode_state="IDLE", nozzle="0.4", tray_id=2):
    client._process_message(
        {
            "print": {
                "gcode_state": gcode_state,
                "nozzle_diameter": nozzle,
                "ams": {"ams": [{"id": "0", "tray": [{"id": str(tray_id), "cali_idx": cali_idx, **filament}]}]},
            }
        }
    )


def _cali_sels(client):
    sent = [json.loads(call.args[1]) for call in client._client.publish.call_args_list]
    return [m["print"] for m in sent if m.get("print", {}).get("command") == "extrusion_cali_sel"]


def _restart(client, clock):
    """Printer power-cycle: the MQTT session drops and a new one starts."""
    clock.now += 60
    client._on_connect(client._client, None, None, 0)


def test_restores_lost_profile_after_restart(client, clock):
    _push(client, 5)
    _restart(client, clock)
    _push(client, -1)

    sels = _cali_sels(client)
    assert len(sels) == 1
    assert sels[0]["cali_idx"] == 5
    assert sels[0]["ams_id"] == 0
    assert sels[0]["tray_id"] == 2  # global tray id = ams*4 + slot
    assert sels[0]["filament_id"] == "GFA00"
    assert sels[0]["nozzle_diameter"] == "0.4"


def test_rate_limited_and_gives_up(client, clock):
    _push(client, 5)
    _restart(client, clock)
    _push(client, -1)
    _push(client, -1)  # push right after the send: still lost, must not resend
    assert len(_cali_sels(client)) == 1

    clock.now += kprofile_drift.RETRY_INTERVAL_S + 1
    _push(client, -1)
    assert len(_cali_sels(client)) == 2

    for _ in range(5):
        clock.now += kprofile_drift.RETRY_INTERVAL_S + 1
        _push(client, -1)
    assert len(_cali_sels(client)) == kprofile_drift.MAX_ATTEMPTS


def test_stops_once_profile_sticks(client, clock):
    _push(client, 5)
    _restart(client, clock)
    _push(client, -1)
    _push(client, 5)  # printer reports the restored profile
    clock.now += kprofile_drift.RETRY_INTERVAL_S + 1
    _push(client, 5)
    assert len(_cali_sels(client)) == 1


def test_no_restore_when_filament_changed(client, clock):
    _push(client, 5)
    _restart(client, clock)
    _push(client, -1, filament=PETG)
    assert _cali_sels(client) == []
    # and the old profile is forgotten for good
    _push(client, -1, filament=PLA)
    assert _cali_sels(client) == []


def test_no_restore_without_restart(client, clock):
    """A selection lost while the session is up is a deliberate Default pick."""
    _push(client, 5)
    clock.now += kprofile_drift.RESTART_WINDOW_S + 1
    _push(client, -1)
    _restart(client, clock)
    _push(client, -1)
    assert _cali_sels(client) == []


def test_no_restore_over_printbuddy_default_pick(client, clock):
    _push(client, 5)
    client.extrusion_cali_sel(ams_id=0, tray_id=2, cali_idx=-1, filament_id="GFA00", nozzle_diameter="0.4")
    _restart(client, clock)
    _push(client, -1)
    assert [s["cali_idx"] for s in _cali_sels(client)] == [-1]


def test_waits_while_printing(client, clock):
    _push(client, 5)
    _restart(client, clock)
    _push(client, -1, gcode_state="RUNNING")
    assert _cali_sels(client) == []
    _push(client, -1, gcode_state="FINISH")
    assert len(_cali_sels(client)) == 1


def test_no_restore_when_nozzle_changed(client, clock):
    _push(client, 5, nozzle="0.4")
    _restart(client, clock)
    _push(client, -1, nozzle="0.6")
    assert _cali_sels(client) == []


def test_never_seen_profile_is_not_invented(client, clock):
    _push(client, -1)
    _restart(client, clock)
    _push(client, -1)
    assert _cali_sels(client) == []


def test_dual_nozzle_unknown_extruder_is_skipped():
    state = MagicMock()
    state.nozzles = [MagicMock(nozzle_diameter="0.4"), MagicMock(nozzle_diameter="0.6")]
    state.ams_extruder_map = {}
    assert kprofile_drift._slot_nozzle(state, 0, 1) is None
    state.ams_extruder_map = {"0": 1}
    assert kprofile_drift._slot_nozzle(state, 0, 1) == "0.6"
    # same size on both nozzles: no mapping needed
    state.nozzles[1].nozzle_diameter = "0.4"
    state.ams_extruder_map = {}
    assert kprofile_drift._slot_nozzle(state, 0, 1) == "0.4"
