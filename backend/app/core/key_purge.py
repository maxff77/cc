"""Daily gift-key purge — keep the admin keys view from accumulating clutter.

The keys catalog (``/admin/keys``) would otherwise list every key forever. This
background task (created in the lifespan, same shape as ``core/reconciler.py``)
runs once a day and hard-deletes the keys that have outlived their usefulness:
an UNCLAIMED key past its shelf life (``created_at + days``) and any REVOKED key.
Claimed keys are never deleted — they are the mint/claim audit trail, and the
frontend merely hides them behind a toggle.

Loop discipline mirrors the reconciler: ``CancelledError`` propagates (clean
shutdown) and every other error is swallowed so a bad pass never kills the loop.
It differs in one way — it purges FIRST, then sleeps. The reconciler sleeps
first (45s interval, so a restart loses almost nothing); this task's interval is
24h and the repo redeploys on every push to main, so a sleep-first pass would be
cancelled mid-sleep on each restart and rarely complete. The pass is cheap and
idempotent, so running it on every boot is fine.
"""

import asyncio
import logging

from app.db.base import async_session_factory
from app.db.repos import gift_keys as gift_keys_repo

logger = logging.getLogger(__name__)

# Daily. Housekeeping, not a hot path — no setting for it (2.5 rule: no new
# configuration for pipeline internals).
_PURGE_INTERVAL_SECONDS = 86_400.0


async def purge_stale_keys() -> int:
    """One purge pass: delete stale keys in a task-owned transaction; return the
    number removed."""
    async with async_session_factory() as session:
        deleted = await gift_keys_repo.delete_stale(session)
        await session.commit()
    return deleted


async def run_key_purge() -> None:
    """Infinite daily purge loop (created as a task in the lifespan).

    Purges immediately on start, then once per interval (purge-first, not
    sleep-first — see the module docstring: a 24h sleep-first task would be
    starved by the repo's deploy-on-every-push restarts). ``CancelledError``
    propagates for a clean shutdown; any other error is logged and the loop
    continues — the housekeeper must never be the thing that dies. The sleep
    runs even after a failed pass so a persistent error can't spin a hot loop.
    """
    while True:
        try:
            deleted = await purge_stale_keys()
            logger.info("event=key_purge deleted=%s", deleted)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("event=key_purge_failed — continuing")
        try:
            await asyncio.sleep(_PURGE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
