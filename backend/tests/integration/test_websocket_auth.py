"""/api/v1/ws needs a token when auth is enabled (ported from upstream 0.2.4.5)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect
from httpx import AsyncClient

from backend.app.api.routes import websocket as ws_route


def _fake_ws():
    ws = MagicMock()
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.receive_json = AsyncMock(side_effect=WebSocketDisconnect())
    return ws


async def _connect(token):
    from backend.app.core import auth

    ws = _fake_ws()
    with (
        patch.object(ws_route, "async_session", auth.async_session),  # the test DB (async_client patches auth's)
        patch.object(ws_route.printer_manager, "get_all_statuses", return_value={}),
        patch.object(ws_route.background_dispatch, "get_state", AsyncMock(return_value={})),
    ):
        await ws_route.websocket_endpoint(ws, token=token)
    return ws


async def _enable_auth(db_session):
    from backend.app.models.settings import Settings

    db_session.add(Settings(key="auth_enabled", value="true"))
    await db_session.commit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auth_disabled_connects_without_token(async_client: AsyncClient):
    ws = await _connect(None)
    ws.accept.assert_awaited_once()
    ws.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("token", [None, "", "not-a-real-token"])
async def test_auth_enabled_refuses_missing_or_bad_token(async_client: AsyncClient, db_session, token):
    await _enable_auth(db_session)
    ws = await _connect(token)
    ws.close.assert_awaited_once_with(code=ws_route.WS_CLOSE_UNAUTHORIZED)
    ws.accept.assert_not_awaited()
    assert ws not in ws_route.ws_manager.active_connections


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auth_enabled_accepts_minted_token(async_client: AsyncClient, db_session):
    from backend.app.core.auth import create_websocket_token

    await _enable_auth(db_session)
    ws = await _connect(await create_websocket_token())
    ws.accept.assert_awaited_once()
    ws.close.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_auth_probe_error_fails_closed(async_client: AsyncClient):
    with patch.object(ws_route, "is_auth_enabled", AsyncMock(side_effect=RuntimeError("db down"))):
        ws = await _connect(None)
    ws.close.assert_awaited_once_with(code=ws_route.WS_CLOSE_UNAUTHORIZED)
    ws.accept.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ws_token_route_needs_login_when_auth_enabled(async_client: AsyncClient, db_session):
    from backend.app.core.auth import verify_websocket_token

    resp = await async_client.post("/api/v1/auth/ws-token")
    assert resp.status_code == 200
    assert await verify_websocket_token(resp.json()["token"])

    await _enable_auth(db_session)
    resp = await async_client.post("/api/v1/auth/ws-token")
    assert resp.status_code == 401
