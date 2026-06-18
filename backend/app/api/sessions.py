"""Sessions router (Story 3.3): the Historial — list, detail, rename, delete;
plus ``POST /{id}/continue`` (Story 3.4): reopen a closed session as the
active capture session, and ``GET /{id}/export`` (Story 3.5): the
Completa/Filtrada views as downloadable ``.txt``.

CRUD (GET/PATCH/DELETE) follows architecture's literal route list
(``/api/sessions/{id}``); continue is the non-CRUD verb-suffix action
(idiom ``/api/batches/{id}/pause|resume|stop``); export is a GET — a safe,
idempotent read (the verb-suffix POST idiom is for actions that mutate).

Tenant scoping: ``tenant_id`` comes ONLY from ``user.tenant_id`` (the session)
— never from the body or path (architecture mandate). Any authenticated role
may browse: the owner navigates their own Historial exactly like a client
(same criterion as ``POST /api/batches``); the cross-tenant support view
lives in ``api/admin.py`` (Story 3.6), which imports these schemas +
``session_to_out`` so both surfaces serve the identical shapes. Every lookup
here is tenant-scoped and the 404 never leaks existence (unknown id, another
tenant's id and out-of-int4 id look the same).
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.broadcaster import broadcaster
from app.core.redact import redact_reply_text
from app.db.base import get_session
from app.db.models import CaptureSession, User
from app.db.repos import batches as batches_repo
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import responses as responses_repo
from app.errors import batch_live, session_conflict, session_in_use, session_not_found
from app.services import batches as batches_service
from app.services import exports

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_PG_INT_MAX = 2**31 - 1  # ids are int4; larger binds overflow asyncpg

# The friendly-name cap of AC 4 = ``CaptureSession.name`` String(200) —
# mirror of legacy ``escribir_nombre``'s 200-char cap.
_NAME_MAX = 200


# --- Schemas (inline, codebase convention) ---------------------------------


class SessionOut(BaseModel):
    id: int
    name: str | None
    # Client-visible "Comando visible" snapshot. The real ``gate_value`` is
    # owner-only and deliberately NOT exposed here (this shape also feeds the
    # admin support view).
    gate_display_value: str
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
    # "Filtrada con response": count of ✅ 'full' revisions (the same rows the
    # frontend filters out of ``responses`` by ``status == 'ok'``).
    responses_ok_total: int
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


def session_to_out(capture_session: CaptureSession) -> SessionOut:
    """Shared CaptureSession → SessionOut mapper (also used by the admin
    support view, Story 3.6 — exact mirror of the ``gate_to_out``
    precedent)."""
    return SessionOut(
        id=capture_session.id,
        name=capture_session.name,
        gate_display_value=capture_session.gate_display_value,
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
        items=[session_to_out(s) for s in sessions], total=len(sessions)
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
        **session_to_out(target).model_dump(),
        responses=[
            SessionResponseRow(
                id=row.id,
                message_id=row.message_id,
                status=row.status,
                text=redact_reply_text(row.text),
                created_at=row.created_at,
            )
            for row in responses
        ],
        cc=[SessionCcRow(id=row.id, text=row.text) for row in cc],
        responses_total=len(responses),
        responses_ok_total=await responses_repo.full_count(
            session, target.id, status=responses_repo.STATUS_OK
        ),
        cc_total=len(cc),
    )


@router.get("/{session_id}/export", response_class=PlainTextResponse)
async def export_session(
    session_id: int,
    view: Literal["completa", "filtrada", "filtrada_completa"],
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Download one view as ``.txt`` (Story 3.5, FR18) — generated on the fly
    from rows, no cache, no files on disk (architecture mandate).

    ``view`` maps the legacy ``?tipo=completa|filtrada`` (``tipo``→``view``);
    the ``Literal`` validates at the edge ⇒ 422 on anything else (same
    treatment as every validation 422 in the project — the UI never builds an
    invalid view). The tenant-scoped lookup's 404 trío (unknown / foreign /
    out-of-int4 id) IS the AC 3 isolation — no new code, no existence leak.

    NO live-batch guard (AC 2: works both during a live batch and on closed
    sessions — same lane as rename). Zero rows ⇒ 200 with an empty body
    (recorded decision: a 404 here would conflate "no data" with "no
    session"). ``PlainTextResponse`` already sets ``text/plain; charset=utf-8``;
    the filename in ``Content-Disposition`` is the backend's single authority.
    """
    target = await _require_session(session, user.tenant_id, session_id)
    if view == "completa":
        rows = await responses_repo.list_full(session, target.id, None)
        content = exports.completa_txt(rows)
    elif view == "filtrada_completa":
        # "Filtrada con response": the full text of only the ✅ revisions —
        # same builder as Completa, fed the status-filtered rows.
        rows = await responses_repo.list_full(
            session, target.id, None, status=responses_repo.STATUS_OK
        )
        content = exports.completa_txt(rows)
    else:
        rows = await responses_repo.list_cc(session, target.id, None)
        content = exports.filtrada_txt(rows)
    filename = exports.export_filename(target, view)
    return PlainTextResponse(
        content,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # AC 1's "no cache" enforced in the HTTP contract, not just by
            # current browser heuristics: the body carries CC data.
            "Cache-Control": "no-store",
        },
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
    return session_to_out(target)


@router.post("/{session_id}/continue", response_model=SessionOut)
async def continue_session(
    session_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Continuar (Story 3.4, AC 1–3): reactivate this session as the tenant's
    active capture session.

    The CC dedup needs NO preloading here — it is DB-backed per
    ``capture_session_id`` (``add_new_cc`` + ``uq_responses_session_cc``), so
    reactivating the session is the whole "dedup set preserved": the next
    batch of the same gate binds to it via ``resolve_for_batch`` and every
    already-captured CC value stays deduped.

    Guard (AC 3): ANY live batch of the tenant (sending|paused|stopping) ⇒
    409 ``batch_live`` — legacy parity "nueva/continuar return HTTP 409 while
    a batch is live or paused (`_lote_vivo`)". Unlike delete's guard it does
    NOT matter which session the live batch is bound to.

    The lookup takes ``FOR UPDATE`` so it serializes with a concurrent DELETE
    of the same target (3.3 takes the same lock) and with another continue of
    the SAME target. Two continues of DIFFERENT targets (or a continue
    crossing a batch start) can still race into
    ``uq_capture_sessions_one_active_per_tenant`` at commit — mapped to 409
    ``session_conflict``, never a raw 500.

    Idempotent: continuing the ALREADY-active session (surface idle) is a
    clean no-op in ``activate`` ⇒ 200 + emit (cheap multi-tab reconcile; the
    UI never offers the button on "En curso", but another tab may have
    activated it in between). Function name: ``continue`` is a reserved word.
    """
    target = await _require_session(
        session, user.tenant_id, session_id, for_update=True
    )
    live = await batches_repo.get_live_batch(session, user.tenant_id)
    if live is not None:
        raise batch_live()
    try:
        await capture_sessions_repo.activate(session, target)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise session_conflict() from None
    # POST-commit: fresh SELECTs in the same request session see the
    # committed state; the payload leaves fully materialized (flat dicts,
    # isoformat timestamps — the 2.3 MissingGreenlet lesson is solved inside
    # the helper). Emitted VERBATIM as the snapshot's session slice so a tab
    # that misses the event reconciles with its next snapshot.
    payload = await batches_service.active_session_data(session, user.tenant_id)
    await broadcaster.emit(user.tenant_id, "session.active", payload)
    # expire_on_commit=False (db/base.py) keeps the attributes valid here.
    return session_to_out(target)


@router.post("/new", response_model=SessionOut)
async def new_session(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionOut:
    """Nueva sesión: explicitly start a FRESH active capture session on the
    same gate as the current active one (a clean per-session dedup set).

    The implicit lifecycle (``resolve_for_batch``) only ever *reuses* the
    active same-gate session, so a client working one gate can never reset
    dedup or produce a closed (``is_active=False``) row that Historial would
    offer to "Continuar". This is the explicit opt-out: it forks the active
    session's gate via ``create_active`` (which deactivates the prior one
    UPDATE-first, so the partial unique index never trips on the honest path),
    making the old session closed-and-retomable.

    Gate source: the CURRENTLY active session — there is no gate picker in the
    cockpit (to start on a new gate, send a batch on it). No active session ⇒
    404 ``session_not_found`` (nothing to fork; the cockpit hides the button
    when no session is active anyway).

    Guards mirror ``continue_session`` exactly: ANY live batch
    (sending|paused|stopping) ⇒ 409 ``batch_live`` (reshuffling the active
    session mid-batch would split capture); a concurrent new/continue/batch
    racing into ``uq_capture_sessions_one_active_per_tenant`` ⇒ 409
    ``session_conflict``, never a raw 500. The post-commit ``session.active``
    emit (verbatim ``active_session_data``) rebinds every tab — the new
    session's slice is empty, so the cockpit panels clear.
    """
    active = await capture_sessions_repo.get_active(
        session, user.tenant_id, for_update=True
    )
    if active is None:
        raise session_not_found()
    live = await batches_repo.get_live_batch(session, user.tenant_id)
    if live is not None:
        raise batch_live()
    try:
        fresh = await capture_sessions_repo.create_active(
            session,
            user.tenant_id,
            active.gate_value,
            active.gate_name,
            active.gate_display_value,
            active.special_mode,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise session_conflict() from None
    payload = await batches_service.active_session_data(session, user.tenant_id)
    await broadcaster.emit(user.tenant_id, "session.active", payload)
    return session_to_out(fresh)


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
