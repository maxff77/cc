"""Observability router (Story 4.3 + owner monitoring panel): the ban
guardrail's read-only surface plus the live deployment dashboard.

One owner-only GET exposing what the structured logs already record, live:
per-tenant send activity (live since restart + durable today/24h from
``send_log``, with human labels), the Telegram connection state, FloodWait
events + the governor's current ``g_min`` and raise count, the
unmatched-replies bucket, the watchdog latch and the admission queue depth.
Strictly READ — the endpoint reads singletons and counts rows, never writes.

Owner-only is the multi-tenant boundary, not a convenience: the payload
exposes ``tenant_id``s and cross-tenant volumes — exactly the class of data
isolation forbids clients/admins. Global system state, no tenant scoping
(same documented exception class as the gates catalog / watchdog).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_role
from app.core import alerts, capture, send_worker
from app.core.scheduler import scheduler
from app.core.telegram import gateway
from app.core.watchdog import watchdog
from app.db.base import get_session
from app.db.models import User
from app.db.repos import batches as batches_repo
from app.db.repos import send_log as send_log_repo
from app.db.repos import tenants as tenants_repo
from app.services import admission as admission_service

router = APIRouter(prefix="/api/observability", tags=["observability"])

require_owner = require_role("owner")

# ponytail: tz hardcoded. Mexico abolished DST in 2022 (fixed UTC-6), so the
# now.replace(hour=0,...) "local midnight" below is offset-stable — load-bearing:
# revisit the midnight math (and this constant) if it ever serves a DST region.
_PANEL_TZ = ZoneInfo("America/Mexico_City")


# --- Schemas (inline, codebase convention) ---------------------------------


class TelegramSlice(BaseModel):
    authorized: bool  # session is authed
    ready: bool  # authed AND has resolved targets — can actually deliver
    targets_resolved: int  # live count of resolved send destinations


class TenantActivity(BaseModel):
    tenant_id: int
    name: str
    email: str | None
    sent_live: int  # process counter — resets on restart
    sent_today: int  # send_log, since local midnight — survives restart
    sent_24h: int  # send_log, rolling 24h


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
    tenants: list[TenantActivity]
    sent_total: int  # live total (sends since restart)
    sent_today_total: int
    sent_24h_total: int
    telegram: TelegramSlice
    flood: FloodSlice
    unmatched: UnmatchedSlice
    watchdog: WatchdogSlice
    admission: AdmissionSlice


@router.get("", response_model=ObservabilityOut)
async def get_observability(
    user: User = Depends(require_owner),
    session: AsyncSession = Depends(get_session),
) -> ObservabilityOut:
    """The monitoring dashboard slice — counters read where they live."""
    live = send_worker.sent_by_tenant()  # {tenant_id: count} since restart

    now = datetime.now(_PANEL_TZ)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_ago = now - timedelta(hours=24)
    windowed = await send_log_repo.sent_counts_by_window(
        session, today_start=today_start, day_ago=day_ago
    )  # {tenant_id: (sent_today, sent_24h)}

    ids = sorted(set(live) | set(windowed))
    labels = await tenants_repo.labels(session, ids)
    tenants: list[TenantActivity] = []
    for tid in ids:
        live_n = live.get(tid, 0)
        today_n, h24_n = windowed.get(tid, (0, 0))
        if live_n == 0 and today_n == 0 and h24_n == 0:
            continue  # label but no activity in any window — omit
        name, email = labels.get(tid, ("", None))
        tenants.append(
            TenantActivity(
                tenant_id=tid,
                name=name or f"Tenant {tid}",
                email=email,
                sent_live=live_n,
                sent_today=today_n,
                sent_24h=h24_n,
            )
        )
    tenants.sort(key=lambda t: (t.sent_24h, t.sent_live), reverse=True)

    return ObservabilityOut(
        tenants=tenants,
        sent_total=sum(live.values()),
        sent_today_total=sum(t.sent_today for t in tenants),
        sent_24h_total=sum(t.sent_24h for t in tenants),
        telegram=TelegramSlice(
            authorized=gateway.authorized,
            ready=gateway.ready,
            targets_resolved=len(gateway.resolved_ids()),
        ),
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
