from __future__ import annotations

import asyncio

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..context import Context
from ..search_service import get_search_view
from ..security import verify_access_token

router = APIRouter()


@router.websocket("/ws/searches/{search_id}")
async def search_updates(websocket: WebSocket, search_id: str) -> None:
    """
    Live search status over WebSocket. Authenticates with ?token=<access token>
    and streams the search view whenever its status changes, until it is done or failed.
    """
    ctx: Context = websocket.app.state.ctx
    token = websocket.query_params.get("token", "")
    try:
        payload = verify_access_token(ctx.settings.jwt_secret, token)
    except jwt.PyJWTError:
        await websocket.close(code=4401)
        return

    user_id = await ctx.pool.fetchval(
        "SELECT id::text FROM users WHERE id=$1 AND deleted_at IS NULL", payload["sub"]
    )
    if user_id is None:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    last_status: str | None = None
    try:
        for _ in range(600):  # safety cap: ~10 minutes at 1s polling
            view = await get_search_view(ctx.pool, user_id, search_id)
            if view is None:
                await websocket.send_json({"error": "not_found"})
                break
            if view["status"] != last_status:
                last_status = view["status"]
                await websocket.send_json(view)
            if view["status"] in ("done", "failed"):
                break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
    finally:
        await websocket.close()
