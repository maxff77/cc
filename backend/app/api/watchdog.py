"""Watchdog router (Story 4.1): owner-only status + manual resume.

Resuming a watchdog-triggered global pause is an EXPLICIT owner action (AC 3)
— never automatic: this endpoint is the ONLY ``watchdog.resume()`` call site
in the codebase. Both routes are owner-gated (``require_role("owner")``);
clients learn the pause state from the WS snapshot/events, not from here.

The watchdog is GLOBAL system state — no tenant scoping applies (same
documented exception class as the gates catalog).
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import require_role
from app.core import send_worker
from app.core.watchdog import watchdog
from app.db.models import User

router = APIRouter(prefix="/api/watchdog", tags=["watchdog"])

require_owner = require_role("owner")


# --- Schemas (inline, codebase convention) ---------------------------------


class WatchdogStatusOut(BaseModel):
    # Mirror of ``watchdog.status()`` — the same slice the WS snapshot ships.
    paused: bool
    reason: str | None
    detail: str | None
    paused_at: str | None


@router.get("", response_model=WatchdogStatusOut)
async def get_status(user: User = Depends(require_owner)) -> WatchdogStatusOut:
    """Current latch state — the owner's alert surface polls/reads this."""
    return WatchdogStatusOut(**watchdog.status())


@router.post("/resume", status_code=204)
async def resume(user: User = Depends(require_owner)) -> None:
    """Clear the global pause (AC 3). Idempotent: not paused → 204, no event
    (two tabs / double click — the idiom of the 2.3 batch controls)."""
    resumed = await watchdog.resume()
    if resumed:
        # The worker may be mid idle-sleep — re-check the queues right now.
        send_worker.wake()
