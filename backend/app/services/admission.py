"""Admission control policy (Story 4.2): the owner-configurable cap on
concurrent active senders.

The cap lives in ``system_settings`` (key ``max_active_senders``) — DB, not
env, ON PURPOSE: "owner-configurable" means hot from the UI, surviving
restarts, no redeploy. ``"0"``/missing row = DISABLED: every batch is
admitted immediately and behavior is pure adaptive-interval degradation
(Epic 2 semantics, AC 4).

Two consumers decide admissions, both under the cap row's FOR UPDATE lock
(``get_cap_locked``) so they serialize and never overshoot:
- ``POST /api/batches`` (new-batch branch) — admit or queue at creation;
- the send worker's promotion sweep (``send_worker._admit_waiting``) — fill
  freed slots FIFO.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import system_settings as system_settings_repo

CAP_KEY = "max_active_senders"
CAP_DISABLED = 0
# Upper bound on the knob; guards fat-finger values (PLAN_DAYS_MAX idiom).
CAP_MAX = 1000


def _parse_cap(raw: str | None) -> int:
    """Defensive parse: missing/garbage/negative all mean disabled (0)."""
    if raw is None:
        return CAP_DISABLED
    try:
        cap = int(raw)
    except ValueError:
        return CAP_DISABLED
    return cap if cap > 0 else CAP_DISABLED


async def get_cap(session: AsyncSession) -> int:
    """Current cap; ``0`` = admission control disabled."""
    return _parse_cap(await system_settings_repo.get_value(session, CAP_KEY))


async def get_cap_locked(session: AsyncSession) -> int:
    """Cap read holding its row FOR UPDATE until commit (the admission lock).

    A missing row takes no lock — and means disabled, where concurrent
    admissions need no serialization (everyone gets in anyway). The row is
    persisted even for ``0`` once the owner touches the knob, so the lock
    exists from the first configuration on.
    """
    return _parse_cap(
        await system_settings_repo.get_value_for_update(session, CAP_KEY)
    )


async def set_cap(session: AsyncSession, cap: int) -> None:
    """Persist the cap (flush; caller commits). Bounds belong to the route."""
    await system_settings_repo.set_value(session, CAP_KEY, str(cap))


def has_capacity(cap: int, admitted: int) -> bool:
    """Pure admission policy: disabled cap admits everyone; else strict <."""
    return cap <= CAP_DISABLED or admitted < cap
