"""Send-pacing configuration (configurable interval): the owner-tunable
constant interval between sends on the shared Telegram account.

The interval is the scheduler's floor ``G`` (``scheduler.set_floor``). It
lives in ``system_settings`` (key ``send_interval_seconds``) — DB, not env,
ON PURPOSE: "owner-configurable" means hot from the UI, surviving restarts,
no redeploy (same rationale as the admission cap). A missing/garbage row
falls back to the env default ``settings.scheduler_g_min_seconds`` (4.0s),
so the system always has a safe interval even before the owner touches it.

The value is owner-set through a guarded admin endpoint — NEVER derived from
a client's send request (FR12). The FloodWait governor still self-tunes the
live pace UP from this floor; this knob only moves the floor it returns to.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.scheduler import scheduler
from app.db.repos import system_settings as system_settings_repo

INTERVAL_KEY = "send_interval_seconds"
# Range for the shared account. The 2.0s anti-ban floor was REMOVED on owner
# request (testing): the lower bound is now 0.0s, so the owner can set any
# interval down to zero. WARNING: below ~2s FloodWaits/bans escalate on the
# shared MTProto account and a ban hits every tenant — this is a deliberate
# owner override, not a safe default. 30.0s still mirrors the governor ceiling.
INTERVAL_MIN = 0.0
INTERVAL_MAX = 30.0


def _parse_interval(raw: str | None) -> float | None:
    """Defensive parse: missing/garbage/out-of-range all mean "use default".

    Returns ``None`` (not the default) so callers can tell a configured value
    apart from an absent one — the boot loader leaves the env default in place
    on ``None`` instead of forcing the scheduler floor.
    """
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not INTERVAL_MIN <= seconds <= INTERVAL_MAX:
        return None
    return seconds


async def get_interval(session: AsyncSession) -> float:
    """Configured interval, or the env default when unset/garbage."""
    parsed = _parse_interval(await system_settings_repo.get_value(session, INTERVAL_KEY))
    return parsed if parsed is not None else settings.scheduler_g_min_seconds


async def set_interval(session: AsyncSession, seconds: float) -> None:
    """Persist the interval (flush; caller commits). Bounds belong to the route."""
    await system_settings_repo.set_value(session, INTERVAL_KEY, str(seconds))


async def apply_persisted(session: AsyncSession) -> None:
    """Push a persisted interval into the scheduler floor at boot.

    No-op when no valid row exists — ``reset()`` already seeded the floor from
    the env default, so the scheduler stays safe.
    """
    parsed = _parse_interval(await system_settings_repo.get_value(session, INTERVAL_KEY))
    if parsed is not None:
        scheduler.set_floor(parsed)
