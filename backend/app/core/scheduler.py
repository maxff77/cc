"""Multi-tenant send scheduler (Story 2.4): round-robin, owner priority,
adaptive interval, FloodWait governor.

The send worker is the ONLY consumer: it lists ``active_senders`` from the
repo, asks ``scheduler.pick_next`` whose line goes out next, and paces the
loop with ``scheduler.interval(n)``. REST handlers never touch this module —
the interval is the system's, never a request's (FR12).

Formula (AC 2, architecture Gap Analysis): ``G = max(G_min, P(n)/n)`` with
``P(n)`` linear from 10s (n=1) to 20s (n>=5). The 10–20s band is a PRODUCT
constant (FR13), not configuration; only the floor ``G_min`` is configurable
(``scheduler_g_min_seconds``) and self-tunes on FloodWait.

State is process memory ON PURPOSE (recorded decision): a single worker in a
single asyncio loop consumes it, and neither the rotation cursor nor the
governor is durable state — a restart resets them and fairness re-establishes
itself on the first full turn. Postgres keeps the queue/batches (NFR6); the
cursor it does not.
"""

import time
from collections.abc import Callable

from app.config import settings
from app.db.repos.batches import ActiveSender

# Product band of the per-client turn (FR13): P(n) = min(CAP, BASE + SLOPE·(n−1)).
# NOT configurable — only the G_min floor is (AC 2).
_P_BASE = 10.0
_P_CAP = 20.0
_P_SLOPE = 2.5

# FloodWait governor (AC 4): every event raises g_min ×1.5 (capped); after a
# full window without FloodWaits it decays one ÷1.5 step per window, never
# below the configured floor — "self-tuning toward the safe band" both ways.
_GOVERNOR_FACTOR = 1.5
_G_MIN_CEIL = 30.0
_GOVERNOR_DECAY_SECONDS = 600.0


def _target_per_client(n: int) -> float:
    """P(n): the per-client turn target, linear 10s→20s, saturated at n>=5."""
    return min(_P_CAP, _P_BASE + _P_SLOPE * (n - 1))


class Scheduler:
    """Rotation cursor + adaptive-interval governor (one instance per worker).

    The clock is injectable so the governor's decay window is deterministic
    under test; production uses ``time.monotonic``.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self.reset()

    def reset(self) -> None:
        """Forget cursor + governor state (tests; equivalent to a restart)."""
        self._g_min: float = settings.scheduler_g_min_seconds
        self._last_flood_at: float | None = None
        self._last_client_tenant_id: int | None = None
        self._last_owner_tenant_id: int | None = None
        self._last_was_owner: bool = False

    # --- adaptive interval (AC 2) + governor (AC 4) -------------------------

    @property
    def g_min(self) -> float:
        """Current governor floor (observability for tests/logs)."""
        return self._g_min

    def interval(self, n: int) -> float:
        """``G = max(g_min, P(n)/n)`` for ``n`` active (non-paused) senders."""
        self._maybe_decay()
        n = max(1, n)
        return max(self._g_min, _target_per_client(n) / n)

    def note_flood_wait(self) -> None:
        """A FloodWait happened: raise the floor toward the safe band."""
        self._g_min = min(self._g_min * _GOVERNOR_FACTOR, _G_MIN_CEIL)
        self._last_flood_at = self._now()

    def _maybe_decay(self) -> None:
        """Lazy decay: one ÷1.5 step per 600s window without FloodWaits."""
        floor = settings.scheduler_g_min_seconds
        if self._last_flood_at is None or self._g_min <= floor:
            return
        if self._now() - self._last_flood_at >= _GOVERNOR_DECAY_SECONDS:
            self._g_min = max(floor, self._g_min / _GOVERNOR_FACTOR)
            # Re-seal so the next step needs a fresh full window.
            self._last_flood_at = self._now()

    # --- rotation + bounded owner priority (AC 1, 3) -------------------------

    def pick_next(self, active: list[ActiveSender]) -> ActiveSender | None:
        """Pick whose line goes out next; ``None`` when nobody is servable.

        Recorded decision: STRICT owner/client alternation implements both
        halves of AC 3 at once — an owner batch "jumps ahead" (it takes the
        very next slot instead of waiting a client-rotation turn) yet is
        bounded to exactly <=50% of slots while clients are active. Owner(s)
        alone take every slot. Within each class, batches rotate cyclically
        by ``tenant_id`` (the repo lists them in that stable order).
        """
        if not active:
            return None
        owners = [sender for sender in active if sender.is_owner_priority]
        clients = [sender for sender in active if not sender.is_owner_priority]
        if owners and (not self._last_was_owner or not clients):
            pick = self._next_cyclic(owners, self._last_owner_tenant_id)
            self._last_owner_tenant_id = pick.tenant_id
            self._last_was_owner = True
            return pick
        pick = self._next_cyclic(clients, self._last_client_tenant_id)
        self._last_client_tenant_id = pick.tenant_id
        self._last_was_owner = False
        return pick

    @staticmethod
    def _next_cyclic(
        senders: list[ActiveSender], last_tenant_id: int | None
    ) -> ActiveSender:
        """Next sender after ``last_tenant_id`` in tenant_id order (wrapping)."""
        if last_tenant_id is not None:
            for sender in senders:
                if sender.tenant_id > last_tenant_id:
                    return sender
        return senders[0]


# Module-level singleton (same idiom as settings / gateway / broadcaster).
scheduler = Scheduler()
