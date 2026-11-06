import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from backend.app.core.auth import is_auth_enabled, verify_websocket_token
from backend.app.core.database import async_session
from backend.app.core.websocket import ws_manager
from backend.app.services.background_dispatch import background_dispatch
from backend.app.services.printer_manager import printer_manager, printer_state_to_dict

logger = logging.getLogger(__name__)
router = APIRouter()

# Private-use close code (RFC 6455 4000-4999): the SPA mints a fresh token on it.
WS_CLOSE_UNAUTHORIZED = 4401


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(default=None)):
    """WebSocket endpoint for real-time updates.

    With auth enabled, ``?token=`` must hold a token from ``POST /auth/ws-token``
    (Bambuddy 0.2.4.5). Checked before accept() so a refused client never joins
    the broadcast list. With auth disabled, anyone can connect as before.
    """
    try:
        async with async_session() as db:
            auth_required = await is_auth_enabled(db)
    except Exception:  # fail closed: a DB error must not open the stream
        logger.error("WebSocket auth probe failed; refusing connection", exc_info=True)
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return
    if auth_required and not (token and await verify_websocket_token(token)):
        logger.info("WebSocket connect refused: missing or invalid token")
        await websocket.close(code=WS_CLOSE_UNAUTHORIZED)
        return

    logger.info("WebSocket client connecting...")
    await ws_manager.connect(websocket)
    logger.info("WebSocket client connected")

    try:
        # Send initial status of all printers
        statuses = printer_manager.get_all_statuses()
        for printer_id, state in statuses.items():
            await websocket.send_json(
                {
                    "type": "printer_status",
                    "printer_id": printer_id,
                    "data": printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
                }
            )

        dispatch_state = await background_dispatch.get_state()
        if (dispatch_state.get("dispatched", 0) + dispatch_state.get("processing", 0)) > 0:
            await websocket.send_json(
                {
                    "type": "background_dispatch",
                    "data": dispatch_state,
                }
            )
        logger.info("Sent initial status for %s printers", len(statuses))

        # Keep connection alive and handle incoming messages
        while True:
            data = await websocket.receive_json()

            # Handle ping/pong for keepalive
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})

            # Handle status request
            elif data.get("type") == "get_status":
                printer_id = data.get("printer_id")
                if printer_id:
                    state = printer_manager.get_status(printer_id)
                    if state:
                        await websocket.send_json(
                            {
                                "type": "printer_status",
                                "printer_id": printer_id,
                                "data": printer_state_to_dict(state, printer_id, printer_manager.get_model(printer_id)),
                            }
                        )

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected normally")
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket error: %s", e, exc_info=True)
        await ws_manager.disconnect(websocket)
