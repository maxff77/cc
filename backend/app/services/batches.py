"""Batch service: gate application + WS snapshot/progress builders (Story 2.2).

``apply_gate`` is an English port of legacy ``core.agregar_prefijo``
(core.py:43) — EXACT behavior: split lines, strip, skip blanks, prepend
``f"{gate_value} "`` unless the line already starts with it, dedup preserving
order (in-batch dedup is an AC).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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


def eta_seconds(queued: int) -> float:
    """Honest ETA: ``queued × interval`` (UX-DR14), recomputed per emission.

    The adaptive ``G×n`` version is Story 2.4.
    """
    return queued * settings.send_interval_seconds


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
    """``batch.progress`` event payload for a batch."""
    sent, queued = await batches_repo.counts(session, batch.id)
    return {
        "batch_id": batch.id,
        "sent": sent,
        "queued": queued,
        "total": sent + queued,
        "eta_seconds": eta_seconds(queued),
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
            "total": 0,
            "eta_seconds": 0,
            "cc_new": 0,
        }
    sent, queued = await batches_repo.counts(session, batch.id)
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
        "total": sent + queued,
        "eta_seconds": eta_seconds(queued),
        "cc_new": 0,
    }
