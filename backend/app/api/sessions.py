"""Sessions router (Story 3.3): the Historial — list, detail, rename, delete.

Capture sessions only; the export half of this module (`.txt`) is Story 3.5.
Pure CRUD (GET/PATCH/DELETE), REST plural, no verb suffixes — architecture's
literal route list names ``/api/sessions/{id}``.

Tenant scoping: ``tenant_id`` comes ONLY from ``user.tenant_id`` (the session)
— never from the body or path (architecture mandate). Any authenticated role
may browse: the owner navigates their own Historial exactly like a client
(same criterion as ``POST /api/batches``); the cross-tenant support view is
Story 3.6, not here. Every lookup is tenant-scoped and the 404 never leaks
existence (unknown id, another tenant's id and out-of-int4 id look the same).
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.base import get_session
from app.db.models import CaptureSession, User
from app.db.repos import batches as batches_repo
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import responses as responses_repo
from app.errors import session_in_use, session_not_found

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_PG_INT_MAX = 2**31 - 1  # ids are int4; larger binds overflow asyncpg

# The friendly-name cap of AC 4 = ``CaptureSession.name`` String(200) —
# mirror of legacy ``escribir_nombre``'s 200-char cap.
_NAME_MAX = 200


# --- Schemas (inline, codebase convention) ---------------------------------


class SessionOut(BaseModel):
    id: int
    name: str | None
    gate_value: str
    gate_name: str
    # The "En curso"/"Cerrada" badge derives from THIS (recorded 3.1
    # decision), not from "bound to a live batch".
    is_active: bool
    created_at: datetime


class SessionListResponse(BaseModel):
    items: list[SessionOut]
    total: int


class SessionResponseRow(BaseModel):
    # MIRROR of the snapshot's 'responses' rows (services/batches.py) so the
    # 3.2 frontend mappers serve verbatim.
    id: int
    message_id: int
    status: str | None
    text: str
    created_at: datetime


class SessionCcRow(BaseModel):
    # MIRROR of the snapshot's 'cc' rows — no timestamp (filtrada.txt parity).
    id: int
    text: str


class SessionDetailOut(SessionOut):
    responses: list[SessionResponseRow]
    cc: list[SessionCcRow]
    responses_total: int
    cc_total: int


class RenameSessionRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        # Idiom _validate_gate_name (api/admin.py): trimmed, non-empty, no
        # control/invisible chars, ≤200. ValueError ⇒ 422; the frontend
        # mirrors this validation before sending.
        v = v.strip()
        if not v:
            raise ValueError("nombre vacío")
        if any(not ch.isprintable() for ch in v):
            raise ValueError("el nombre no puede contener caracteres invisibles")
        if len(v) > _NAME_MAX:
            raise ValueError("nombre demasiado largo")
        return v


def _session_out(capture_session: CaptureSession) -> SessionOut:
    return SessionOut(
        id=capture_session.id,
        name=capture_session.name,
        gate_value=capture_session.gate_value,
        gate_name=capture_session.gate_name,
        is_active=capture_session.is_active,
        created_at=capture_session.created_at,
    )


async def _require_session(
    session: AsyncSession,
    tenant_id: int,
    session_id: int,
    *,
    for_update: bool = False,
) -> CaptureSession:
    """Tenant-scoped lookup shared by detail/rename/delete.

    Unknown id, another tenant's id and out-of-int4 id all 404 alike
    (idiom ``_controlled_batch``). ``for_update=True`` (the delete path)
    locks the row until commit — see ``delete_session``.
    """
    if not 0 < session_id <= _PG_INT_MAX:
        raise session_not_found()
    target = await capture_sessions_repo.get_for_tenant(
        session, tenant_id, session_id, for_update=for_update
    )
    if target is None:
        raise session_not_found()
    return target


# --- Routes ------------------------------------------------------------------


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionListResponse:
    """The tenant's sessions, newest first (AC 1).

    Flat list — the grouping by gate is presentation, done client-side.
    """
    sessions = await capture_sessions_repo.list_for_tenant(session, user.tenant_id)
    return SessionListResponse(
        items=[_session_out(s) for s in sessions], total=len(sessions)
    )


@router.get("/{session_id}", response_model=SessionDetailOut)
async def get_session_detail(
    session_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionDetailOut:
    """Detail with the COMPLETE Completa/Filtrada data (AC 2).

    ``limit=None`` — the ``_SNAPSHOT_ROWS`` cap is per-reconnection only; the
    full data lives here (3.2's recorded promise). With no cap the totals ARE
    the list lengths — the count queries stay snapshot-only.
    """
    target = await _require_session(session, user.tenant_id, session_id)
    responses = await responses_repo.list_full(session, target.id, None)
    cc = await responses_repo.list_cc(session, target.id, None)
    return SessionDetailOut(
        **_session_out(target).model_dump(),
        responses=[
            SessionResponseRow(
                id=row.id,
                message_id=row.message_id,
                status=row.status,
                text=row.text,
                created_at=row.created_at,
            )
            for row in responses
        ],
        cc=[SessionCcRow(id=row.id, text=row.text) for row in cc],
        responses_total=len(responses),
        cc_total=len(cc),
    )


@router.patch("/{session_id}", response_model=SessionOut)
async def rename_session(
    session_id: int,
    body: RenameSessionRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Rename (AC 4). NO live-batch guard — recorded legacy parity
    ("renombrar is unguarded") — and no uniqueness: names may repeat, the
    stable id is the DB id. ``updated_at`` refreshes itself (onupdate)."""
    target = await _require_session(session, user.tenant_id, session_id)
    target.name = body.name
    await session.commit()
    return _session_out(target)


@router.delete("/{session_id}", status_code=204)
async def delete_session(
    session_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard delete (AC 5) guarded by the LIVE-batch binding (AC 6).

    The guard is "bound to a live batch", NOT ``is_active``: deleting the
    active session with the surface idle IS allowed (no batch to stop) — the
    tenant just has no active session until the next batch creates one.
    ``responses`` rows die via FK CASCADE; the batch history survives with
    ``capture_session_id`` NULL (SET NULL).

    The lookup takes ``FOR UPDATE`` BEFORE the live-batch guard to close the
    TOCTOU against a concurrent ``POST /api/batches`` binding this same
    session (review 3-3 — the window is NOT harmless: the FK is SET NULL, so
    a POST committing between the guard's read and this commit would not
    500, it would be silently unbound, leaving a LIVE batch with no session).
    A concurrent binding's batch INSERT takes ``FOR KEY SHARE`` on the
    session row during its FK check, which conflicts with ``FOR UPDATE``:
    either that POST blocks and errors on the gone row, or it commits first
    and the guard's read here sees the live batch ⇒ 409.
    """
    target = await _require_session(
        session, user.tenant_id, session_id, for_update=True
    )
    live = await batches_repo.get_live_batch(session, user.tenant_id)
    if live is not None and live.capture_session_id == target.id:
        raise session_in_use()
    await capture_sessions_repo.delete(session, target)
    await session.commit()
