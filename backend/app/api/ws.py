"""WebSocket endpoint ``/ws`` (Story 2.2) — server→client ONLY.

Handshake: the session cookie authenticates the tenant with the SAME chain as
``deps._resolve_session_user`` (valid session → not blocked → plan not
expired → not must_change_password), via a WS-local helper — HTTP deps raise
``AppError``, which a WS route can't render. Any failure closes with code
4401 AFTER accept (browsers can't reliably read close codes pre-accept).

On success: a full ``snapshot`` is ALWAYS the first frame (AC 8/11 — a tab
opened mid-batch renders correct state immediately; reconnects reconcile
silently), then the socket joins the tenant-scoped broadcaster. Client
payloads are read only as keep-alive and discarded — commands go through REST.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.broadcaster import broadcaster
from app.db.base import async_session_factory
from app.db.models import User
from app.services import auth as auth_service
from app.services import batches as batches_service
from app.services import plans as plans_service

router = APIRouter()

# Custom close code (app range) for a failed cookie handshake.
WS_UNAUTHORIZED = 4401


async def resolve_ws_user(session: AsyncSession, token: str | None) -> User | None:
    """Mirror of ``deps._resolve_session_user`` returning ``None`` on failure.

    No revocation side effects here: a just-expired/blocked user simply fails
    the handshake — their next HTTP request runs the one-shot revocation
    (1.4/1.5 semantics stay owned by the HTTP chain).
    """
    if not token:
        return None
    auth_session = await auth_service.get_valid_session(session, token)
    if auth_session is None:
        return None
    user = auth_session.user
    if user.is_blocked:
        return None
    if plans_service.is_plan_expired(user):
        return None
    if user.must_change_password:
        return None
    return user


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    """Cookie handshake → snapshot-first → tenant-scoped event stream."""
    await websocket.accept()

    token = websocket.cookies.get(settings.session_cookie_name)
    async with async_session_factory() as session:
        user = await resolve_ws_user(session, token)
        if user is None:
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        tenant_id = user.tenant_id  # bound for the socket's lifetime
        snapshot = await batches_service.snapshot(session, tenant_id)

    await websocket.send_json({"event": "snapshot", "data": snapshot})

    broadcaster.register(tenant_id, websocket)
    try:
        while True:
            # Keep-alive only. Server→client contract: any client payload is
            # discarded, never acted upon (commands go through REST).
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unregister(tenant_id, websocket)
