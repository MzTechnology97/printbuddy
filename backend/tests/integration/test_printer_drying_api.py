"""POST /printers/{id}/drying/start reports what the printer answered, not just that it was sent."""

from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.integration
@pytest.mark.parametrize(("reply", "status"), [("fail", 409), ("success", 200), (None, 200)])
async def test_start_drying_follows_the_printer_reply(async_client, printer_factory, reply, status):
    printer = await printer_factory(model="P2S")
    with (
        patch("backend.app.api.routes.printers.printer_manager") as pm,
        patch("backend.app.api.routes.printers.supports_drying", return_value=True),
    ):
        pm.get_status.return_value = MagicMock(firmware_version="01.02.00.00", raw_data={"ams": []})
        pm.send_drying_command.return_value = "7"  # the sequence_id of the reply
        pm.drying_result.return_value = reply
        resp = await async_client.post(
            f"/api/v1/printers/{printer.id}/drying/start", params={"ams_id": 0, "temp": 65, "duration": 12}
        )
    assert resp.status_code == status, resp.text
    if reply == "fail":
        assert "refused to start drying" in resp.json()["detail"]
    pm.drying_result.assert_called_with(printer.id, "7")
