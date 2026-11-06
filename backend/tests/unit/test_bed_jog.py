"""Unit tests for the bed-jog and home-axes endpoints (#791).

Tests:
  POST /api/v1/printers/{printer_id}/bed-jog?distance=<mm>&force=<bool>
  POST /api/v1/printers/{printer_id}/home-axes?axes=<z|xy|all>
"""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient


class TestBedJogAPI:
    @pytest.mark.asyncio
    async def test_bed_jog_not_found(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/printers/99999/bed-jog?distance=10")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_bed_jog_zero_distance_rejected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=0")
        assert response.status_code == 400
        assert "distance" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_bed_jog_too_large_rejected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=500")
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_bed_jog_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Disconnected")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=10")
            assert response.status_code == 400
            assert "not connected" in response.json()["detail"].lower()

    @pytest.mark.asyncio
    async def test_bed_jog_send_failure(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = False
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=10")
            assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_bed_jog_success_without_force(self, async_client: AsyncClient, printer_factory):
        """When force=false the M211 guard lines must not be emitted."""
        printer = await printer_factory(name="P1")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=10&force=false")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert "G91" in sent_gcode
            assert "G1 Z10.00" in sent_gcode
            assert "G90" in sent_gcode
            assert "M211" not in sent_gcode

    @pytest.mark.asyncio
    async def test_bed_jog_success_with_force(self, async_client: AsyncClient, printer_factory):
        """force=true must wrap the move in M211 S0 / M211 S1."""
        printer = await printer_factory(name="P1")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance=-5&force=true")
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            lines = sent_gcode.splitlines()
            assert lines[0] == "M211 S0"
            assert lines[-1] == "M211 S1"
            assert "G1 Z-5.00" in sent_gcode

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "model",
        # bed-on-Z models plus every A1 spelling / internal code (N1 = A1 Mini, N2S = A1)
        ["X1C", "P1S", "P2S", "H2D", "X2D", "A1", "A1 Mini", "A1MINI", "N1", "N2S"],
    )
    @pytest.mark.parametrize("distance,expected", [(5, "G1 Z5.00"), (-5, "G1 Z-5.00")])
    async def test_bed_jog_sends_gap_unchanged_on_every_model(
        self, async_client: AsyncClient, printer_factory, model, distance, expected
    ):
        """#1334 (upstream 1.2.5.6): G-code Z is the nozzle-bed gap on every
        model, so the API's signed gap goes onto the wire unchanged. On an A1
        the old sign flip turned distance=5 (more clearance) into G1 Z-5, which
        drives the toolhead at the plate."""
        printer = await printer_factory(name=f"Test-{model}", model=model)
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/bed-jog?distance={distance}")
            assert response.status_code == 200
            lines = mock_client.send_gcode.call_args[0][0].split("\n")
            assert lines == ["G91", f"{expected} F600", "G90"], f"{model}: got {lines!r}"


class TestAxisJogAPI:
    @pytest.mark.asyncio
    async def test_axis_jog_rejects_invalid_axis(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Klipper")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/axis-jog?axis=e&distance=10")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("axis", "distance", "expected"),
        [("x", 10, "G1 X10.00 F3000"), ("y", -5, "G1 Y-5.00 F3000"), ("z", 1, "G1 Z1.00 F600")],
    )
    async def test_axis_jog_sends_relative_move(
        self, async_client: AsyncClient, printer_factory, axis, distance, expected
    ):
        printer = await printer_factory(name="Klipper", provider="klipper", model="Klipper/Moonraker")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(
                f"/api/v1/printers/{printer.id}/axis-jog?axis={axis}&distance={distance}"
            )
            assert response.status_code == 200
            sent_gcode = mock_client.send_gcode.call_args[0][0]
            assert sent_gcode.splitlines() == ["G91", expected, "G90"]


class TestDisableSteppersAPI:
    @pytest.mark.asyncio
    async def test_disable_steppers_uses_provider_gcode_transport(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Prusa MK4S", provider="prusalink", model="Prusa MK4S")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/disable-steppers")
            assert response.status_code == 200
            mock_client.send_gcode.assert_called_once_with("M84")


class TestTemperatureControlAPI:
    @pytest.mark.asyncio
    async def test_set_nozzle_temperature_uses_provider_helper(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Klipper", provider="klipper", model="Klipper/Moonraker")
        mock_client = MagicMock()
        mock_client.set_nozzle_temperature.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/nozzle?target=215")
            assert response.status_code == 200
            mock_client.set_nozzle_temperature.assert_called_once_with(215)

    @pytest.mark.asyncio
    async def test_set_bed_temperature_uses_provider_helper(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Klipper", provider="klipper", model="Klipper/Moonraker")
        mock_client = MagicMock()
        mock_client.set_bed_temperature.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/bed?target=60")
            assert response.status_code == 200
            mock_client.set_bed_temperature.assert_called_once_with(60)

    @pytest.mark.asyncio
    async def test_temperature_limits_are_validated(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Klipper")
        nozzle = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/nozzle?target=400")
        bed = await async_client.post(f"/api/v1/printers/{printer.id}/temperature/bed?target=160")
        assert nozzle.status_code == 400
        assert bed.status_code == 400


class TestHomeAxesAPI:
    @pytest.mark.asyncio
    async def test_home_axes_not_found(self, async_client: AsyncClient):
        response = await async_client.post("/api/v1/printers/99999/home-axes?axes=z")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_home_axes_invalid(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="P1")
        response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes=bogus")
        assert response.status_code == 400

    @pytest.mark.asyncio
    @pytest.mark.parametrize("axes", ["x", "y", "z", "xy", "all"])
    async def test_home_axes_always_runs_full_home_for_bambu(self, async_client: AsyncClient, printer_factory, axes):
        # Regression for #1052: Bambu providers must always send a bare `G28` so the printer's
        # safe auto-home sequence (toolhead park → XY home → Z home) runs. Sending `G28 Z` alone
        # on H2C/H2D/H2S/X1 can crash the bed into the toolhead.
        printer = await printer_factory(name="P1", provider="bambu")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes={axes}")
            assert response.status_code == 200
            mock_client.send_gcode.assert_called_once_with("G28")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("axes", "expected"),
        [("x", "G28 X"), ("y", "G28 Y"), ("z", "G28 Z"), ("xy", "G28 X Y"), ("all", "G28")],
    )
    async def test_home_axes_preserves_axes_for_prusalink(
        self, async_client: AsyncClient, printer_factory, axes, expected
    ):
        printer = await printer_factory(name="Prusa MK4S", provider="prusalink", model="Prusa MK4S")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes={axes}")
            assert response.status_code == 200
            mock_client.send_gcode.assert_called_once_with(expected)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("axes", "expected"),
        [("x", ["x"]), ("z", ["z"]), ("xy", ["x", "y"]), ("all", ["x", "y", "z"])],
    )
    async def test_home_axes_uses_octoprint_home_command(
        self, async_client: AsyncClient, printer_factory, axes, expected
    ):
        printer = await printer_factory(name="Ender 3", provider="octoprint")
        mock_client = MagicMock()
        mock_client.home_axes.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes={axes}")
            assert response.status_code == 200
            mock_client.home_axes.assert_called_once_with(expected)
            mock_client.send_gcode.assert_not_called()

    @pytest.mark.asyncio
    async def test_home_axes_reports_octoprint_conflict(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Ender 3", provider="octoprint")
        mock_client = MagicMock()
        mock_client.home_axes.return_value = False
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes=all")
            assert response.status_code == 409


class TestExtrudeAPI:
    @pytest.mark.asyncio
    async def test_extrude_uses_provider_gcode_transport(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="Prusa MK4S", provider="prusalink", model="Prusa MK4S")
        mock_client = MagicMock()
        mock_client.send_gcode.return_value = True
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = mock_client
            response = await async_client.post(f"/api/v1/printers/{printer.id}/extrude?length=10&speed=300")
            assert response.status_code == 200
            mock_client.send_gcode.assert_called_once_with("M83\nG1 E10.00 F300\nM82")

    @pytest.mark.asyncio
    async def test_home_axes_not_connected(self, async_client: AsyncClient, printer_factory):
        printer = await printer_factory(name="D")
        with patch("backend.app.api.routes.printers.printer_manager") as mock_pm:
            mock_pm.get_client.return_value = None
            response = await async_client.post(f"/api/v1/printers/{printer.id}/home-axes?axes=z")
            assert response.status_code == 400
