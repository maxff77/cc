"""Observability router (Story 4.3): the ban guardrail's read-only surface.

One owner-only GET exposing what the structured logs already record, live:
per-tenant send counts, FloodWait events + the governor's current ``g_min``
and raise count, the unmatched-replies bucket, the watchdog latch and the
admission queue depth. Strictly READ — the endpoint reads singletons and
counts rows, never writes.

Owner-only is the multi-tenant boundary, not a convenience: the payload
exposes ``tenant_id``s and cross-tenant volumes — exactly the class of data
isolation forbids clients/admins. Global system state, no tenant scoping
(same documented exception class as the gates catalog / watchdog).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core import alerts, capture, send_worker
from app.core.scheduler import scheduler
from app.core.watchdog import watchdog
from app.db.base import get_session
from app.db.models import User
from app.db.repos import batches as batches_repo
from app.services import admission as admission_service

router = APIRouter(prefix="/api/observability", tags=["observability"])

require_owner = require_role("owner")


# --- Schemas (inline, codebase convention) ---------------------------------


class FloodSlice(BaseModel):
    events_total: int
    governor_raises: int
    g_min: float
    events_in_window: int
    alert_active: bool


class UnmatchedSlice(BaseModel):
    total: int
    events_in_window: int
    alert_active: bool


class WatchdogSlice(BaseModel):
    # Mirror of ``watchdog.status()`` — the same slice /api/watchdog serves.
    paused: bool
    reason: str | None
    detail: str | None
    paused_at: str | None


class AdmissionSlice(BaseModel):
    max_active_senders: int  # 0 = disabled
    admitted: int
    waiting: int


class ObservabilityOut(BaseModel):
    # JSON object keys are strings on the wire — pydantic coerces back.
    sent_by_tenant: dict[int, int]
    sent_total: int
    flood: FloodSlice
    unmatched: UnmatchedSlice
    watchdog: WatchdogSlice
    admission: AdmissionSlice


@router.get("", response_model=ObservabilityOut)
async def get_observability(
    user: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> ObservabilityOut:
    """The guardrail dashboard slice (AC 2) — counters read where they live."""
    sent = send_worker.sent_by_tenant()
    return ObservabilityOut(
        sent_by_tenant=sent,
        sent_total=sum(sent.values()),
        flood=FloodSlice(
            events_total=scheduler.flood_events_total,
            governor_raises=scheduler.governor_raises,
            g_min=scheduler.g_min,
            events_in_window=alerts.flood_alert.count_in_window(),
            alert_active=alerts.flood_alert.is_alerting(),
        ),
        unmatched=UnmatchedSlice(
            total=capture.unmatched_total(),
            events_in_window=alerts.unmatched_alert.count_in_window(),
            alert_active=alerts.unmatched_alert.is_alerting(),
        ),
        watchdog=WatchdogSlice(**watchdog.status()),
        admission=AdmissionSlice(
            max_active_senders=await admission_service.get_cap(session),
            admitted=await batches_repo.count_admitted(session),
            waiting=await batches_repo.count_waiting(session),
        ),
    )
