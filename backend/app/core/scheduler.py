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
        self._flood_until: float = 0.0
        self._last_client_tenant_id: int | None = None
        self._last_admin_tenant_id: int | None = None
        self._last_owner_tenant_id: int | None = None
        self._last_was_owner: bool = False
        self._last_was_admin: bool = False
        # Observability counters (Story 4.3) — process memory like the rest
        # of the governor: the structured logs are the durable series.
        self._flood_events_total: int = 0
        self._governor_raises: int = 0

    # --- adaptive interval (AC 2) + governor (AC 4) -------------------------

    @property
    def g_min(self) -> float:
        """Current governor floor (observability for tests/logs).

        Decay is LAZY (it runs in ``interval()``): this may read high until
        the worker's next turn — accepted, the read is observability.
        """
        return self._g_min

    @property
    def flood_events_total(self) -> int:
        """FloodWaits seen since boot (Story 4.3 observability slice)."""
        return self._flood_events_total

    @property
    def governor_raises(self) -> int:
        """Times a FloodWait actually RAISED ``g_min`` (at the ceiling a
        FloodWait still counts as an event but not as a raise)."""
        return self._governor_raises

    def interval(self, n: int) -> float:
        """``G = max(g_min, P(n)/n)`` for ``n`` active (non-paused) senders."""
        self._maybe_decay()
        n = max(1, n)
        return max(self._g_min, _target_per_client(n) / n)

    def note_flood_wait(self, seconds: float) -> None:
        """A FloodWait happened: raise the floor AND open the global window.

        Besides the governor (×1.5 toward the safe band), the requested wait
        becomes a GLOBAL no-send window (Story 2.5, closing the 2.4 deferred
        release/abort bypass): the worker checks ``flood_remaining()`` before
        claiming ANY tenant's line — including the window-owning tenant's
        (recorded decision, no exemption: retrying inside an open window on
        the shared account escalates everyone's next FloodWait). No default on
        ``seconds`` on purpose — no caller may forget the window.
        """
        before = self._g_min
        self._g_min = min(self._g_min * _GOVERNOR_FACTOR, _G_MIN_CEIL)
        self._flood_events_total += 1
        if self._g_min > before:
            self._governor_raises += 1
        self._last_flood_at = self._now()
        self._flood_until = max(self._flood_until, self._now() + seconds)

    def flood_remaining(self) -> float:
        """Seconds left of the global FloodWait window (0.0 when closed)."""
        return max(0.0, self._flood_until - self._now())

    def _maybe_decay(self) -> None:
        """Lazy decay: one ÷1.5 step per 600s window without FloodWaits."""
        floor = settings.scheduler_g_min_seconds
        if self._last_flood_at is None or self._g_min <= floor:
            return
        if self._now() - self._last_flood_at >= _GOVERNOR_DECAY_SECONDS:
            self._g_min = max(floor, self._g_min / _GOVERNOR_FACTOR)
            # Re-seal so the next step needs a fresh full window.
            self._last_flood_at = self._now()

    # --- rotation + bounded priority tiers (AC 1, 3) -------------------------

    def pick_next(self, active: list[ActiveSender]) -> ActiveSender | None:
        """Pick whose line goes out next; ``None`` when nobody is servable.

        Priority is a 3-tier ranking owner (2) > admin (1) > client (0),
        implemented as NESTED bounded alternation. The owner tier alternates
        against everyone below it (so it "jumps ahead" yet is bounded to
        <=50% of slots while anyone below is active); within the remaining,
        non-owner slots the admin tier alternates the same way against
        clients (<=25% of total when all three are active). A tier alone — or
        the highest non-empty tier when those below are idle — takes every
        slot. Within each tier, batches rotate cyclically by ``tenant_id``
        (the repo lists them in that stable order).

        This generalizes the original binary owner/client alternation: with
        no admins it reduces exactly to the old behaviour.
        """
        if not active:
            return None
        owners = [s for s in active if s.priority >= 2]
        admins = [s for s in active if s.priority == 1]
        clients = [s for s in active if s.priority <= 0]

        # Tier 1: owner vs everyone-below, bounded to <=50% of all slots.
        if owners and (not self._last_was_owner or not (admins or clients)):
            pick = self._next_cyclic(owners, self._last_owner_tenant_id)
            self._last_owner_tenant_id = pick.tenant_id
            self._last_was_owner = True
            return pick
        self._last_was_owner = False

        # Tier 2: admin vs client, bounded to <=50% of the non-owner slots.
        if admins and (not self._last_was_admin or not clients):
            pick = self._next_cyclic(admins, self._last_admin_tenant_id)
            self._last_admin_tenant_id = pick.tenant_id
            self._last_was_admin = True
            return pick
        self._last_was_admin = False

        pick = self._next_cyclic(clients, self._last_client_tenant_id)
        self._last_client_tenant_id = pick.tenant_id
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
