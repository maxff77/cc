"""Batch service: gate application + WS snapshot/progress builders (Story 2.2).

``apply_gate`` is an English port of legacy ``core.agregar_prefijo``
(core.py:43) — EXACT behavior: split lines, strip, skip blanks, prepend
``f"{gate_value} "`` unless the line already starts with it, dedup preserving
order (in-batch dedup is an AC).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redact import redact_reply_text
from app.core.scheduler import scheduler
from app.core.watchdog import watchdog
from app.db.models import Batch, BatchLine
from app.db.repos import batches as batches_repo
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import responses as responses_repo
from app.db.repos import send_log as send_log_repo
from app.db.repos import tenants as tenants_repo

# Cap on the rows each snapshot list ships (Story 3.2) — a module constant,
# NOT a setting (2.5 rule: pipeline internals are never configuration). The
# TOTALS stay real even when the lists are trimmed (badges never lie); the
# full data belongs to Historial (3.3) and export (3.5).
_SNAPSHOT_ROWS = 200


def apply_gate(text: str, gate_value: str) -> list[str]:
    """Prefix every non-blank line with the gate; dedup preserving order."""
    lines = text.strip().split("\n")
    result: list[str] = []
    seen: set[str] = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        message = line if line.startswith(gate_value + " ") else f"{gate_value} {line}"
        if message not in seen:
            result.append(message)
            seen.add(message)
    return result


def eta_seconds(queued: int, n_eff: int) -> float:
    """Honest ETA derived from ``G×n`` (UX-DR14), recomputed per emission.

    A tenant's turn comes every ``G×n``, so draining ``queued`` lines takes
    ≈ ``queued × n_eff × interval(n_eff)`` (architecture: "UI must show honest
    ETA derived from G×n so degradation is visible, not mysterious"). ``G`` is
    now a constant (``g_min``) and ``interval`` ignores its argument, so
    ``n_eff`` enters only through this ``×n_eff`` factor — that is what makes
    the ETA grow as clients join. Recorded decision: owner priority does NOT
    adjust client ETAs — the approximation is recomputed on every event; no
    falsely-precise queueing math (UX-DR14).
    """
    return queued * n_eff * scheduler.interval(n_eff)


async def _n_effective(session: AsyncSession, batch: Batch) -> int:
    """The ``n`` this batch's ETA should assume.

    A paused batch is excluded from ``count_active_senders`` — for ITS
    "ETA on resume" it is re-included as if it resumed right now (n + 1).
    """
    n = await batches_repo.count_active_senders(session)
    n_eff = n if batch.state == batches_repo.STATE_SENDING else n + 1
    return max(1, n_eff)


async def awaiting_reply_count(
    session: AsyncSession, capture_session_id: int | None
) -> int:
    """Lines delivered in this capture session that have NO ✅/❌ reply yet.

    ``delivered − distinct-answered`` over the whole session (counters never
    reset between batches). Clamped at ≥0: the subtrahend counts only
    message_ids that also exist in send_log (attribution links a reply back to
    a delivered line), so it can never exceed the minuend — the clamp is a
    belt-and-suspenders guard, not expected to trigger. ``None`` (a batch with
    no session bound yet) ⇒ 0. A line the bot never answers stays counted
    (honest "still waiting") — intended, not a leak."""
    if capture_session_id is None:
        return 0
    sent = await send_log_repo.sent_count_for_session(session, capture_session_id)
    responded = await responses_repo.responded_message_count(
        session, capture_session_id
    )
    return max(0, sent - responded)


def state_data(
    batch: Batch, state: str, *, queue_position: int | None = None
) -> dict:
    """``batch.state`` event payload — full context, single source of truth.

    ``state`` is the SURFACE state (``idle | sending | paused | stopping |
    waiting``): DB terminals ``completed``/``stopped`` both travel as
    ``"idle"`` (2.2 pattern). Carrying the gate fields fixes the 2.2 review
    finding where a second tab never learned the gate of a batch started
    elsewhere. ``session_id`` (Story 3.2) propagates the capture-session
    binding to every tab the moment the batch starts — the UI reducer needs
    it to tell "new session → clear the panels" from "late reply of an old
    session → ignore". ``queue_position`` (Story 4.2) travels in EVERY
    ``batch.state`` — ``None`` unless the surface state is ``waiting`` — so
    the reducer assigns instead of guessing.
    """
    return {
        "batch_id": batch.id,
        "state": state,
        "gate_name": batch.gate_name,
        # Client-visible "Comando visible" snapshot — clients render this; the
        # real gate_value (owner-only) is never sent over the client WS.
        "gate_display_value": batch.gate_display_value,
        "session_id": batch.capture_session_id,
        "queue_position": queue_position,
    }


def lines_queued_data(batch_id: int, lines: list[BatchLine]) -> dict:
    """``batch.lines_queued`` event payload — the lines just added to the queue.

    Fires on create AND append (``api.batches``) so the cockpit's "Pendientes"
    list grows live. Draining needs NO new event: the existing per-line
    ``batch.line_sent`` / ``batch.line_failed`` (both already carry
    ``position``) remove a line as it leaves the queue. Capped at
    ``_SNAPSHOT_ROWS`` for parity with the snapshot — the count badge reads the
    authoritative ``queued`` total, never this (possibly trimmed) list.
    """
    return {
        "batch_id": batch_id,
        "lines": [
            {"position": line.position, "text": line.text}
            for line in lines[:_SNAPSHOT_ROWS]
        ],
    }


async def progress_data(session: AsyncSession, batch: Batch) -> dict:
    """``batch.progress`` event payload for a batch.

    ``total`` includes ``failed`` (2.5) — the size of the pasted lote must
    not shrink when a line fails (UX honesty); the ring's % stays truthful
    because the denominator keeps counting the failed lines.
    """
    sent, queued, failed = await batches_repo.counts(session, batch.id)
    n_eff = await _n_effective(session, batch)
    return {
        "batch_id": batch.id,
        "sent": sent,
        "queued": queued,
        "failed": failed,
        "total": sent + queued + failed,
        "eta_seconds": eta_seconds(queued, n_eff),
        # Session-scoped "esperando respuesta" — fires on every send/fail
        # (this event does), so the badge climbs live as lines go out. The
        # frontend ASSIGNS this authoritative number (no client deltas).
        "awaiting_reply": await awaiting_reply_count(
            session, batch.capture_session_id
        ),
    }


async def active_session_data(session: AsyncSession, tenant_id: int) -> dict:
    """Snapshot slice for the tenant's ACTIVE capture session (Story 3.2).

    Carries ``session_id`` + the rows that rebuild the Completa/Filtrada
    panels (capped at ``_SNAPSHOT_ROWS`` per list) and the REAL totals —
    ``cc_new`` (Story 3.1's metric, the same number as the event's
    ``cc_total``) and ``responses_total``. Recorded decision: none of this
    resets between batches (legacy "counters never reset" — the session, not
    the batch, owns it). Everything is materialized inside the session (the
    2.3 MissingGreenlet lesson).

    Public since Story 3.4: the continue handler emits this VERBATIM as the
    ``session.active`` payload — a tab that misses the event reconciles with
    its next snapshot without any shape difference.
    """
    active = await capture_sessions_repo.get_active(session, tenant_id)
    if active is None:
        return {
            "session_id": None,
            # Session identity for the cockpit's "active session" strip — null
            # when no session is active (the strip stays hidden). DISTINCT keys
            # (``session_gate_*``) so this spread into ``snapshot`` never
            # collides with the live batch's top-level ``gate_name``/value.
            "session_name": None,
            "session_gate_name": None,
            "session_gate_display_value": None,
            "cc_new": 0,
            "responses_total": 0,
            "responses_ok_total": 0,
            # No active session ⇒ nothing can be awaiting a reply (the cockpit
            # badge stays hidden).
            "awaiting_reply": 0,
            "responses": [],
            "cc": [],
        }
    return {
        "session_id": active.id,
        # Identity shown by the cockpit strip (name falls back to created_at
        # client-side, mirroring Historial's `fallbackName`).
        "session_name": active.name,
        "session_gate_name": active.gate_name,
        "session_gate_display_value": active.gate_display_value,
        "cc_new": await responses_repo.cc_count(session, active.id),
        "responses_total": await responses_repo.full_count(session, active.id),
        # "Filtrada con response" badge: only the ✅ revisions (full text).
        "responses_ok_total": await responses_repo.full_count(
            session, active.id, status=responses_repo.STATUS_OK
        ),
        # "Esperando respuesta" — delivered lines without a ✅/❌ yet, session
        # scoped like the totals above (survives the idle reset, never resets
        # between batches). Carried in the snapshot AND session.active so a
        # reconnecting tab rebuilds the badge from the snapshot alone.
        "awaiting_reply": await awaiting_reply_count(session, active.id),
        "responses": [
            {
                "id": row.id,
                "message_id": row.message_id,
                "status": row.status,
                "text": redact_reply_text(row.text),
                "created_at": row.created_at.isoformat(),
            }
            for row in await responses_repo.list_full(
                session, active.id, _SNAPSHOT_ROWS
            )
        ],
        # Filtrada rows carry no timestamp — parity with filtrada.txt: one
        # value per line.
        "cc": [
            {"id": row.id, "text": row.text}
            for row in await responses_repo.list_cc(
                session, active.id, _SNAPSHOT_ROWS
            )
        ],
    }


async def snapshot(session: AsyncSession, tenant_id: int) -> dict:
    """Full state for a tenant's freshly connected tab (snapshot-first, AC 8).

    Since Story 3.2 BOTH branches merge ``active_session_data``: a
    reconnected tab rebuilds Completa/Filtrada/badges from the snapshot ALONE
    (the 2.2 contract — exact precedent: ``failed_lines``, added in 2.5 for
    the same reason).
    """
    # Credit balance (credits feature): carried in EVERY snapshot so a
    # reconnecting cockpit rebuilds the balance display from the snapshot alone
    # (same snapshot-first contract as the panels). The WS ``credits.updated``
    # event keeps it live thereafter.
    credit_balance = await tenants_repo.get_credit_balance(session, tenant_id)
    batch = await batches_repo.get_live_batch(session, tenant_id)
    if batch is None:
        return {
            "state": "idle",
            "batch_id": None,
            "gate_name": None,
            "gate_display_value": None,
            "sent": 0,
            "queued": 0,
            "failed": 0,
            "failed_lines": [],
            # No live batch → no pending queue (snapshot-first parity).
            "pending_lines": [],
            "total": 0,
            "eta_seconds": 0,
            # Watchdog slice (Story 4.1) — a reconnected tab rebuilds the
            # global-pause banner from the snapshot alone (snapshot-first).
            "watchdog": watchdog.status(),
            "queue_position": None,
            "credit_balance": credit_balance,
            **await active_session_data(session, tenant_id),
        }
    sent, queued, failed = await batches_repo.counts(session, batch.id)
    n_eff = await _n_effective(session, batch)
    # Admission queue position (Story 4.2): a tab connecting mid-wait renders
    # its place from the snapshot alone (snapshot-first, 2.2 pattern). None
    # in every other state.
    position = (
        await batches_repo.queue_position(session, batch.id)
        if batch.state == batches_repo.STATE_WAITING
        else None
    )
    return {
        # Passthrough: get_live_batch only returns LIVE_STATES, so this is
        # always one of sending|paused|stopping|waiting — a tab opened
        # mid-pause/mid-wait must render the right surface from the snapshot
        # alone (Story 2.3 AC 1/2; Story 4.2 AC 2).
        "state": batch.state,
        "batch_id": batch.id,
        "gate_name": batch.gate_name,
        "gate_display_value": batch.gate_display_value,
        "sent": sent,
        "queued": queued,
        "failed": failed,
        # A tab reconnecting mid-batch rebuilds the failed panel from the
        # snapshot alone (snapshot-first, 2.2 pattern).
        "failed_lines": [
            {"position": line.position, "text": line.text, "code": line.fail_code or ""}
            for line in await batches_repo.failed_lines(session, batch.id)
        ],
        # Still-queued line texts so a reconnecting tab rebuilds the
        # "Pendientes" list from the snapshot alone (survives a page reload;
        # same precedent as failed_lines). Capped — the badge uses `queued`.
        "pending_lines": [
            {"position": line.position, "text": line.text}
            for line in await batches_repo.queued_lines(
                session, batch.id, _SNAPSHOT_ROWS
            )
        ],
        "total": sent + queued + failed,
        "eta_seconds": eta_seconds(queued, n_eff),
        # Watchdog slice (Story 4.1) — same rationale as the idle branch.
        "watchdog": watchdog.status(),
        "queue_position": position,
        "credit_balance": credit_balance,
        **await active_session_data(session, tenant_id),
    }
