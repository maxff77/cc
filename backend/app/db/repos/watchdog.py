"""Data access for the watchdog's durable pause latch (Story 4.1).

DELIBERATELY UNSCOPED — like ``repos/send_log.py``, this is NOT the
gates/users global exception nor a handler-facing module: the single row is
written only by the ``core/watchdog.py`` singleton (which runs outside any
request and guards the whole shared account) and read once at boot by
``load_persisted()``.

Pure ORM, flush not commit — callers own the transaction.
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import WatchdogState

# The single row's id — app-enforced singleton (no second row is ever
# created: ``save_state`` is get-or-create on this id).
_ROW_ID = 1


async def get_state(session: AsyncSession) -> WatchdogState | None:
    """The latch row, or ``None`` when no pause was ever persisted."""
    return await session.get(WatchdogState, _ROW_ID)


async def save_state(
    session: AsyncSession,
    *,
    paused: bool,
    reason: str | None,
    detail: str | None,
    paused_at: datetime | None,
    resumed_at: datetime | None,
) -> WatchdogState:
    """Get-or-create the single latch row and overwrite its fields."""
    state = await session.get(WatchdogState, _ROW_ID)
    if state is None:
        state = WatchdogState(id=_ROW_ID)
        session.add(state)
    state.paused = paused
    state.reason = reason
    state.detail = detail
    state.paused_at = paused_at
    state.resumed_at = resumed_at
    await session.flush()
    return state
