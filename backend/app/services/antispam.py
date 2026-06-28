"""Antispam configuration (antispam-per-user feature): the owner-tunable
per-tenant send cooldown, decoupled from pricing plans.

Two layers resolve a client tenant's scheduler cooldown as
``coalesce(User.antispam_seconds, default_antispam_seconds)``:

- the GLOBAL default lives here — a ``system_settings`` row (key
  ``default_antispam_seconds``), hot from the UI and surviving restarts (same
  rationale as the configurable interval and the admission cap). A
  missing/garbage row falls back to ``settings.scheduler_default_antispam_seconds``
  so the system always has a safe baseline before the owner touches it;
- the per-user OVERRIDE lives on ``User.antispam_seconds`` (owner-set from
  /admin/users), winning over the default when present.

The cooldown only SLOWS a tenant relative to the shared account: the global
``g_min`` floor still paces every send, so a lower value can re-pick a tenant
faster but never push the account past ``1/g_min`` (the ban protector). The
default is read at query time by ``db.repos.batches.active_senders`` (a bind
param), NOT pushed into the scheduler singleton — the cooldown rides on each
``ActiveSender``, so there is nothing to boot-apply.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.repos import system_settings as system_settings_repo

DEFAULT_ANTISPAM_KEY = "default_antispam_seconds"
# Bounds for the GLOBAL default. Lower bound 1.0s (the default always imposes
# SOME per-tenant cooldown); 30.0s mirrors the scheduler governor ceiling
# ``_G_MIN_CEIL`` (the largest cooldown that can ever gate a tenant). A per-user
# override may go as low as 0.0 (no per-tenant cooldown — paced by g_min alone);
# the route enforces that wider 0–30 band, this MIN/MAX is the default's range.
ANTISPAM_MIN = 1.0
ANTISPAM_MAX = 30.0


def _parse_default(raw: str | None) -> float | None:
    """Defensive parse: missing/garbage/out-of-range all mean "use default".

    Returns ``None`` (not the fallback) so callers can tell a configured value
    apart from an absent one.
    """
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    if not ANTISPAM_MIN <= seconds <= ANTISPAM_MAX:
        return None
    return seconds


async def get_default(session: AsyncSession) -> float:
    """Global default cooldown, or the config fallback when unset/garbage."""
    parsed = _parse_default(
        await system_settings_repo.get_value(session, DEFAULT_ANTISPAM_KEY)
    )
    return parsed if parsed is not None else settings.scheduler_default_antispam_seconds


async def set_default(session: AsyncSession, seconds: float) -> None:
    """Persist the global default (flush; caller commits). Bounds belong to the route."""
    await system_settings_repo.set_value(session, DEFAULT_ANTISPAM_KEY, str(seconds))
