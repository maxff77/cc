"""Client history router (PR-2): approved-✅ responses grouped by gate.

A new read-only client history, fully INDEPENDENT of the cockpit "Limpiar"
cutoff (PR-1). PR-1 made the cockpit sessionless and "Limpiar" a non-destructive
view-cutoff (``capture_sessions.cleared_response_id``); this history reads the
persisted ``responses`` rows DIRECTLY and NEVER applies that cutoff — every
approved ✅ message the tenant ever captured is shown.

What it exposes:
- ``GET /api/history`` — the tenant's approved-✅ messages (a message whose
  LATEST ``kind='full'`` revision is ``status='ok'``) grouped by the batch's
  client-visible gate snapshot (``gate_name`` / ``gate_display_value``), each
  carrying its extracted ``cc`` values. Gates ordered by most-recent activity;
  items newest-first; messages with no gate (batch SET-NULL'd) fall into a
  trailing ``{name: null, display_value: "Sin gate"}`` group.
- ``DELETE /api/history/response/{response_id}`` — delete one message's full
  revisions + cc (404 identical for a foreign/unknown id).
- ``DELETE /api/history/gate?name=<gate_name>`` — delete one gate's history.
- ``DELETE /api/history`` — delete the tenant's entire history.

The deletes are destructive BY DESIGN (the client's own data) — they remove
ONLY ``responses`` rows (the child of ``batches``/``batch_lines``), never the
batches/send_log/lines, so attribution/integrity history is untouched.

🔒 The real ``gate_value`` is owner-only and is NEVER serialized here — only
``gate_name`` + ``gate_display_value`` (the "Comando visible" snapshot) leave
the server. ``tenant_id`` comes ONLY from the session cookie (never the body or
path); unknown/foreign/oversized ids 404 identically (no existence leak). The
endpoint owns the transaction — the deletes commit here (mirrors
``POST /api/sessions/clear``); the repo functions flush-not-commit.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.display_transform import display_transform
from app.core.redact import redact_reply_text
from app.db.base import get_session
from app.db.models import User
from app.db.repos import responses as responses_repo
from app.errors import history_response_not_found

router = APIRouter(prefix="/api/history", tags=["history"])

_PG_INT_MAX = 2**31 - 1  # ids are int4; larger binds overflow asyncpg

# The trailing "no gate" group's label (a message whose batch was SET-NULL'd or
# never carried a gate). ``name`` stays ``null`` (the delete-by-gate handle); the
# display label is fixed client copy.
_NO_GATE_DISPLAY = "Sin gateway"


# --- Schemas (inline, codebase convention) ---------------------------------
# CLIENT-SAFE SHAPE: gate_name + display_value ONLY — the real gate_value is
# owner-only and is deliberately absent from every model below.


class HistoryItem(BaseModel):
    # ``id`` = the latest ✅ revision's responses.id (the delete handle).
    id: int
    text: str
    captured_at: datetime
    cc: list[str]


class HistoryGate(BaseModel):
    # ``name`` is the batch's ``gate_name`` snapshot, or ``None`` for "Sin gate"
    # (the delete-by-gate handle). NEVER ``gate_value``.
    name: str | None
    display_value: str
    count: int
    items: list[HistoryItem]


class HistoryOut(BaseModel):
    gates: list[HistoryGate]


@router.get("", response_model=HistoryOut)
async def list_history(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> HistoryOut:
    """The tenant's approved-✅ messages grouped by gate (cutoff-AGNOSTIC).

    ``history_grouped`` returns the approved messages newest-first; this groups
    them by ``gate_name`` preserving first-seen order — so each gate's first
    message is its newest, and the gates come out ordered by most-recent
    activity. The ``None``-gate messages collect into a trailing "Sin gate"
    group. ``tenant_id`` is the session's only. Empty history ⇒ ``{gates: []}``.
    """
    messages = await responses_repo.history_grouped(session, user.tenant_id)

    # Group preserving first-seen order (newest-message-first ⇒ most-recent gate
    # first). Keyed on gate_name; None collects separately so it always trails.
    named: dict[str | None, HistoryGate] = {}
    no_gate: HistoryGate | None = None
    for msg in messages:
        item = HistoryItem(
            id=msg.id,
            # Mirror the cockpit "Aprobadas" composition (services/batches.py):
            # redact on read, THEN display_transform. ``redact_reply_text`` is
            # idempotent (rows are redacted at capture going forward) but
            # load-bearing for LEGACY rows captured before redaction shipped: it
            # scrubs the operator ``⌿ Checked By`` line / ``Credits:`` balance
            # that would otherwise leak here only. ``cookie_mode=True``
            # unconditionally — there is no durable per-message cookie_mode flag
            # (Batch has no such column, CaptureSession's is mutated per batch),
            # so the text-keyed parse_amazon_verdict inside display_transform is
            # the only correct per-message signal; it is a no-op for any
            # non-verdict reply. NOTE: this canonicalizes EVERY Amazon verdict,
            # so an old verdict stays stripped here even after the tenant
            # switches to a normal gate — whereas the sessionless cockpit reads
            # the mutable live ``cookie_mode`` and would re-show the raw
            # ``⌿ Response`` line for those stale rows (a cockpit bug; see
            # deferred-work). History is the canonical, intended view.
            text=display_transform(redact_reply_text(msg.text), True),
            captured_at=msg.created_at,
            cc=msg.cc,
        )
        if msg.gate_name is None:
            if no_gate is None:
                no_gate = HistoryGate(
                    name=None, display_value=_NO_GATE_DISPLAY, count=0, items=[]
                )
            no_gate.items.append(item)
            no_gate.count += 1
            continue
        group = named.get(msg.gate_name)
        if group is None:
            group = HistoryGate(
                name=msg.gate_name,
                # Fall back to the name if a row somehow lacks the display
                # snapshot — never leak ``gate_value`` either way.
                display_value=msg.gate_display_value or msg.gate_name,
                count=0,
                items=[],
            )
            named[msg.gate_name] = group
        group.items.append(item)
        group.count += 1

    gates: list[HistoryGate] = list(named.values())
    if no_gate is not None:
        gates.append(no_gate)  # "Sin gate" always trails the named gates
    return HistoryOut(gates=gates)


@router.delete("/response/{response_id}")
async def delete_one(
    response_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Delete one message's full revisions + cc rows.

    ``delete_message_group`` resolves the message from ``response_id`` and
    returns ``-1`` for a missing / foreign-tenant / out-of-int4 id — all map to
    the SAME 404 (no existence leak). Commits here (the request owns the txn).
    """
    if not 0 < response_id <= _PG_INT_MAX:
        raise history_response_not_found()
    deleted = await responses_repo.delete_message_group(
        session, user.tenant_id, response_id
    )
    if deleted < 0:
        raise history_response_not_found()
    await session.commit()
    return {"deleted": deleted}


@router.delete("/gate")
async def delete_gate(
    name: str = Query(..., max_length=80),
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Delete every responses row of the tenant whose batch's ``gate_name``
    matches ``name``. An unknown name deletes 0 (200, ``{deleted: 0}``).
    Tenant-scoped; commits here (the request owns the txn)."""
    deleted = await responses_repo.delete_by_gate(session, user.tenant_id, name)
    await session.commit()
    return {"deleted": deleted}


@router.delete("")
async def delete_all(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    """Delete EVERY responses row of the acting tenant (the "borrar todo").
    Another tenant's rows are untouched. Commits here (the request owns the
    txn)."""
    deleted = await responses_repo.delete_all_for_tenant(session, user.tenant_id)
    await session.commit()
    return {"deleted": deleted}
