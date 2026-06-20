"""Sessions router (sessionless cockpit, PR-1).

The cockpit collapses to exactly ONE ever-living ``capture_session`` per tenant
(``ensure_perpetual``, never rotated/renamed/continued/closed). The user-facing
session lifecycle is GONE: this router no longer exposes list / detail / rename /
continue / new / delete. What remains:

- ``POST /api/sessions/clear`` — the cockpit "Limpiar": resolve the tenant's one
  perpetual session FOR UPDATE, stamp the non-destructive view-cutoff
  (``cleared_response_id = MAX(responses.id)``), commit, and re-emit
  ``session.active`` carrying the now-empty post-cutoff slice. Deletes ZERO
  ``responses`` rows — approved ✅ rows survive for the deferred PR-2 history.
- ``GET /api/sessions/export`` — the COCKPIT ``.txt`` export (no path id): the
  perpetual session's live, post-Limpiar view (CUTOFF-RESPECTING), mirroring the
  same ``view`` query param the panel footer already sends.
- ``GET /api/sessions/{id}/export`` — the ADMIN / PR-2 export (cutoff-AGNOSTIC,
  full history) — KEPT, NOT removed.

The shared schemas (``SessionOut``/``SessionDetailOut``/``SessionResponseRow``/
``SessionCcRow``/``session_to_out``) stay too: the admin cross-tenant support
view (``api/admin.py``, Story 3.6) imports them, and PR-2 will reuse them.

Tenant scoping: ``tenant_id`` comes ONLY from ``user.tenant_id`` (the session)
— never from the body or path (architecture mandate). The cockpit ``/clear`` and
``/export`` resolve the perpetual session by tenant; a tenant with none yet 404s
identically (no existence leak), exactly as the per-id lookups do.
"""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.broadcaster import broadcaster
from app.db.base import get_session
from app.db.models import CaptureSession, User
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import responses as responses_repo
from app.errors import session_not_found
from app.services import batches as batches_service
from app.services import exports

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

_PG_INT_MAX = 2**31 - 1  # ids are int4; larger binds overflow asyncpg


# --- Schemas (inline, codebase convention) ---------------------------------
# KEPT for the admin support view (api/admin.py imports these) + PR-2. The
# client-facing CRUD that used to consume them is gone (sessionless cockpit).


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


def session_to_out(capture_session: CaptureSession) -> SessionOut:
    """Shared CaptureSession → SessionOut mapper (used by the admin support
    view, Story 3.6 — exact mirror of the ``gate_to_out`` precedent)."""
    return SessionOut(
        id=capture_session.id,
        name=capture_session.name,
        gate_display_value=capture_session.gate_display_value,
        gate_name=capture_session.gate_name,
        is_active=capture_session.is_active,
        created_at=capture_session.created_at,
    )


async def _require_perpetual(
    session: AsyncSession,
    tenant_id: int,
    *,
    for_update: bool = False,
) -> CaptureSession:
    """Resolve the tenant's ONE perpetual capture session, or 404.

    ``tenant_id`` is the session's, never the path's. A tenant with no session
    yet (never sent a batch) 404s identically to every other missing-session
    case (no existence leak). ``for_update=True`` (the ``/clear`` path) locks
    the row until commit so a concurrent batch-start / clear serializes against
    the cutoff stamp.
    """
    target = await capture_sessions_repo.get_active(
        session, tenant_id, for_update=for_update
    )
    if target is None:
        raise session_not_found()
    return target


# --- Routes ------------------------------------------------------------------


@router.post("/clear")
async def clear_view(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int | None]:
    """The cockpit "Limpiar" (sessionless cockpit, PR-1): clear all three live
    panels (Completa, Aprobadas ✅, Datos CC) via a NON-destructive view-cutoff.

    Resolves the tenant's ONE perpetual session FOR UPDATE and stamps
    ``cleared_response_id = MAX(responses.id)`` (an ``id`` high-water-mark, NOT a
    timestamp). The DISPLAY reads (cockpit/snapshot panels + the cockpit export)
    then hide every row with ``Response.id <= cutoff``; ZERO ``responses`` rows
    are deleted, so the approved-✅ history survives for the deferred PR-2 and
    every integrity / attribution / reconciler / dedup / credit / awaiting_reply
    query is untouched — "esperando respuesta" does NOT spike.

    Post-commit re-emit of ``session.active`` (verbatim ``active_session_data``,
    which threads the new cutoff) rebinds every open tab to the now-empty slice;
    a tab that misses it reconciles with its next snapshot (same merge). Tenant
    scoped: a tenant with no session yet 404s identically (``tenant_id`` only
    from the session). Returns ``{"cleared_response_id": cutoff}``.
    """
    target = await _require_perpetual(session, user.tenant_id, for_update=True)
    cutoff = await capture_sessions_repo.clear_view(session, target)
    await session.commit()
    payload = await batches_service.active_session_data(session, user.tenant_id)
    await broadcaster.emit(user.tenant_id, "session.active", payload)
    return {"cleared_response_id": cutoff}


@router.get("/export", response_class=PlainTextResponse)
async def export_cockpit(
    view: Literal["completa", "filtrada", "filtrada_completa"],
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """The COCKPIT ``.txt`` export (sessionless cockpit, PR-1) — the panels'
    ``↓ .txt`` footer, on the tenant's perpetual session, CUTOFF-RESPECTING.

    Mirrors the SAME ``view`` selector the existing ``GET /{id}/export`` accepts
    (``completa`` | ``filtrada`` | ``filtrada_completa``) but threads the
    session's ``cleared_response_id`` into the export builders so the file
    contains ONLY the live, post-Limpiar view — consistent with "limpiar
    literal". (The full-history dump belongs to the deferred PR-2 / the admin
    ``GET /{id}/export``, which stays cutoff-AGNOSTIC.)

    No live-batch guard (works during a live batch and idle). Zero rows ⇒ 200
    with an empty body. The tenant with no session yet 404s identically
    (``tenant_id`` only from the session). ``PlainTextResponse`` sets
    ``text/plain; charset=utf-8``; the filename is the backend's authority and
    the body carries CC data (``Cache-Control: no-store``).
    """
    target = await _require_perpetual(session, user.tenant_id)
    cutoff = target.cleared_response_id
    if view == "completa":
        rows = await responses_repo.list_full(
            session, target.id, None, cleared_response_id=cutoff
        )
        content = exports.completa_txt(rows, target.cookie_mode)
    elif view == "filtrada_completa":
        # "Filtrada con response": the full text of only the ✅ revisions —
        # same builder as Completa, fed the status-filtered rows.
        rows = await responses_repo.list_full(
            session, target.id, None, status=responses_repo.STATUS_OK,
            cleared_response_id=cutoff,
        )
        content = exports.completa_txt(rows, target.cookie_mode)
    else:
        rows = await responses_repo.list_cc(
            session, target.id, None, cleared_response_id=cutoff
        )
        content = exports.filtrada_txt(rows)
    filename = exports.export_filename(target, view)
    return PlainTextResponse(
        content,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # The body carries CC data — "no cache" in the HTTP contract.
            "Cache-Control": "no-store",
        },
    )


@router.get("/{session_id}/export", response_class=PlainTextResponse)
async def export_session(
    session_id: int,
    view: Literal["completa", "filtrada", "filtrada_completa"],
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """The ADMIN / PR-2 per-session ``.txt`` export — CUTOFF-AGNOSTIC (full
    history), KEPT from Story 3.5.

    Unlike the cockpit ``GET /export``, this NEVER applies the Limpiar cutoff:
    it dumps the complete Completa/Filtrada data of one session by id. Tenant
    scoped — unknown / foreign / out-of-int4 id all 404 identically (no
    existence leak). Generated on the fly, no cache, no files on disk.
    """
    if not 0 < session_id <= _PG_INT_MAX:
        raise session_not_found()
    target = await capture_sessions_repo.get_for_tenant(
        session, user.tenant_id, session_id
    )
    if target is None:
        raise session_not_found()
    if view == "completa":
        rows = await responses_repo.list_full(session, target.id, None)
        content = exports.completa_txt(rows, target.cookie_mode)
    elif view == "filtrada_completa":
        rows = await responses_repo.list_full(
            session, target.id, None, status=responses_repo.STATUS_OK
        )
        content = exports.completa_txt(rows, target.cookie_mode)
    else:
        rows = await responses_repo.list_cc(session, target.id, None)
        content = exports.filtrada_txt(rows)
    filename = exports.export_filename(target, view)
    return PlainTextResponse(
        content,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
