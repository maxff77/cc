"""Batch service: gate application + WS snapshot/progress builders (Story 2.2).

``apply_gate`` is an English port of legacy ``core.agregar_prefijo``
(core.py:43) — EXACT behavior: split lines, strip, skip blanks, prepend
``f"{gate_value} "`` unless the line already starts with it, dedup preserving
order (in-batch dedup is an AC).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scheduler import scheduler
from app.db.models import Batch
from app.db.repos import batches as batches_repo


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
    ETA derived from G×n so degradation is visible, not mysterious").
    Recorded decision: owner priority does NOT adjust client ETAs — the
    approximation is recomputed on every event; no falsely-precise queueing
    math (UX-DR14).
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


def state_data(batch: Batch, state: str) -> dict:
    """``batch.state`` event payload — full context, single source of truth.

    ``state`` is the SURFACE state (``idle | sending | paused | stopping``):
    DB terminals ``completed``/``stopped`` both travel as ``"idle"`` (2.2
    pattern). Carrying the gate fields fixes the 2.2 review finding where a
    second tab never learned the gate of a batch started elsewhere.
    """
    return {
        "batch_id": batch.id,
        "state": state,
        "gate_name": batch.gate_name,
        "gate_value": batch.gate_value,
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
    }


async def snapshot(session: AsyncSession, tenant_id: int) -> dict:
    """Full state for a tenant's freshly connected tab (snapshot-first, AC 8).

    ``cc_new`` is hardcoded 0 until Epic 3 — the metric slot must exist for
    the UI.
    """
    batch = await batches_repo.get_live_batch(session, tenant_id)
    if batch is None:
        return {
            "state": "idle",
            "batch_id": None,
            "gate_name": None,
            "gate_value": None,
            "sent": 0,
            "queued": 0,
            "failed": 0,
            "failed_lines": [],
            "total": 0,
            "eta_seconds": 0,
            "cc_new": 0,
        }
    sent, queued, failed = await batches_repo.counts(session, batch.id)
    n_eff = await _n_effective(session, batch)
    return {
        # Passthrough: get_live_batch only returns LIVE_STATES, so this is
        # always one of sending|paused|stopping — a tab opened mid-pause must
        # render "En pausa" from the snapshot alone (Story 2.3 AC 1/2).
        "state": batch.state,
        "batch_id": batch.id,
        "gate_name": batch.gate_name,
        "gate_value": batch.gate_value,
        "sent": sent,
        "queued": queued,
        "failed": failed,
        # A tab reconnecting mid-batch rebuilds the failed panel from the
        # snapshot alone (snapshot-first, 2.2 pattern).
        "failed_lines": [
            {"position": line.position, "text": line.text, "code": line.fail_code or ""}
            for line in await batches_repo.failed_lines(session, batch.id)
        ],
        "total": sent + queued + failed,
        "eta_seconds": eta_seconds(queued, n_eff),
        "cc_new": 0,
    }
