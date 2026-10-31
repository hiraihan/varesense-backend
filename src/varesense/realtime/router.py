import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from varesense.auth.deps import resolve_user_from_token
from varesense.core.db import get_session
from varesense.core.security import TOKEN_KIND_ACCESS
from varesense.realtime.hub import hub

router = APIRouter(tags=["realtime"])

WS_REJECT = 1008


@router.websocket("/ws")
async def ws_push(
    websocket: WebSocket,
    token: str = Query(...),
    session: AsyncSession = Depends(get_session),
):
    try:
        user = await resolve_user_from_token(session, token, TOKEN_KIND_ACCESS)
    except HTTPException:
        await websocket.close(code=WS_REJECT)
        return
    await websocket.accept()

    q = hub.subscribe(user.organization_id)

    async def _send() -> None:
        while True:
            message = await q.get()
            await websocket.send_json(message)

    sender = asyncio.create_task(_send())
    try:
        # Watch the connection so an idle disconnect (no pending message) still
        # tears down the sender and its subscription promptly.
        while True:
            await websocket.receive()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        hub.unsubscribe(user.organization_id, q)
