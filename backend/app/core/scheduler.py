"""Multi-tenant send scheduler (Story 2.4): round-robin, owner priority,
constant interval, FloodWait governor.

The send worker is the ONLY consumer: it lists ``active_senders`` from the
repo, asks ``scheduler.pick_next`` whose line goes out next, and paces the
loop with ``scheduler.interval(n)``. REST handlers never touch this module —
the interval is the system's, never a request's (FR12).

Interval: a CONSTANT ``G = G_min`` between sends regardless of how many
clients are active (owner decision 2026-06-13, superseding the old adaptive
``P(n)/n`` 10–20s band). The shared account fires one line every ``G_min``
seconds and round-robin rotates the slot across active tenants, so each
client's turn naturally comes every ``G×n`` — "more clients = each slower"
falls out of the rotation, not the interval. ``G_min`` is the configured
floor (``scheduler_g_min_seconds``, default 4.0s) and still self-tunes UP on
FloodWait (the governor) — it is the real ban protection, not the band.

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

# FloodWait governor (AC 4): every event raises g_min ×1.5 (capped); after a
# full window without FloodWaits it decays one ÷1.5 step per window, never
# below the configured floor — "self-tuning toward the safe band" both ways.
_GOVERNOR_FACTOR = 1.5
_G_MIN_CEIL = 30.0
_GOVERNOR_DECAY_SECONDS = 600.0


class Scheduler:
    """Rotation cursor + constant-interval governor (one instance per worker).

    The clock is injectable so the governor's decay window is deterministic
    under test; production uses ``time.monotonic``.
    """

    def __init__(self, now: Callable[[], float] = time.monotonic) -> None:
        self._now = now
        self.reset()

    def reset(self) -> None:
        """Forget cursor + governor state (tests; equivalent to a restart)."""
        # ``_floor`` is the configured interval (the constant ``G``); ``_g_min``
        # is the LIVE value the governor raises above it on FloodWait and decays
        # back toward. Both start from the env default; ``set_floor`` (owner UI,
        # Story: configurable interval) and the boot loader move the floor at
        # runtime, which is why decay reads ``self._floor`` not the env constant.
        self._floor: float = settings.scheduler_g_min_seconds
        self._g_min: float = settings.scheduler_g_min_seconds
        self._last_flood_at: float | None = None
        self._flood_until: float = 0.0
        self._last_client_tenant_id: int | None = None
        self._last_admin_tenant_id: int | None = None
        self._last_owner_tenant_id: int | None = None
        self._last_was_owner: bool = False
        self._last_was_admin: bool = False
        # Per-tenant antispam cooldown (plan-catalog feature): monotonic
        # timestamp of each tenant's last send. Process memory like the rotation
        # cursor and the governor — a restart clears it and every tenant is
        # immediately eligible (the global g_min floor still paces the account).
        self._last_send_at: dict[int, float] = {}
        # Observability counters (Story 4.3) — process memory like the rest
        # of the governor: the structured logs are the durable series.
        self._flood_events_total: int = 0
        self._governor_raises: int = 0

    # --- constant interval (AC 2) + governor (AC 4) -------------------------

    @property
    def g_min(self) -> float:
        """Current governor floor (observability for tests/logs).

        Decay is LAZY (it runs in ``interval()``): this may read high until
        the worker's next turn — accepted, the read is observability.
        """
        return self._g_min

    @property
    def floor(self) -> float:
        """Configured interval the governor decays back to (owner-tunable)."""
        return self._floor

    def set_floor(self, seconds: float) -> None:
        """Set the configured interval live (owner UI / boot loader).

        Re-baselines without fighting an active FloodWait: in steady state
        (no live FloodWait) the live value snaps straight to the new floor so
        a lowered interval applies on the next send, not after a decay window;
        mid-flood it keeps any governor elevation (``max``) and decays toward
        the new floor afterwards. Always clamped into ``[floor, ceiling]`` —
        a control never makes the account send below its safe floor.
        """
        self._floor = seconds
        if self._last_flood_at is None:
            self._g_min = min(seconds, _G_MIN_CEIL)
        else:
            self._g_min = min(max(self._g_min, seconds), _G_MIN_CEIL)

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
        """Constant ``G = g_min`` between sends; ``n`` no longer affects pacing.

        The shared account fires every ``g_min`` seconds and round-robin
        spreads the slot across the ``n`` active senders, so each client's turn
        comes every ``G×n`` for free. ``n`` is kept in the signature for caller
        compatibility (and ``eta_seconds``' ``G×n`` math) but is ignored here.
        """
        self._maybe_decay()
        return self._g_min

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
        floor = self._floor
        if self._last_flood_at is None or self._g_min <= floor:
            return
        if self._now() - self._last_flood_at >= _GOVERNOR_DECAY_SECONDS:
            self._g_min = max(floor, self._g_min / _GOVERNOR_FACTOR)
            # Re-seal so the next step needs a fresh full window.
            self._last_flood_at = self._now()

    # --- rotation + bounded priority tiers (AC 1, 3) -------------------------

    def pick_next(self, active: list[ActiveSender]) -> ActiveSender | None:
        """Pick whose line goes out next; ``None`` when nobody is servable.

        FIRST, the per-tenant antispam cooldown (plan-catalog feature) filters
        ``active`` to the ELIGIBLE subset — a tenant is skipped until its
        ``antispam_seconds`` has elapsed since its last send. The existing
        priority/rotation logic then runs over the eligible subset alone;
        ``None`` is returned when EVERY active tenant is still cooling down
        (the worker idles and re-polls — same as an empty queue). The cooldown
        only gates RE-PICKING a tenant: it can slow a tenant but never makes
        the shared account send faster than the global ``g_min`` floor, which
        still paces every send (the ban protector is unchanged).

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
        now = self._now()
        self._prune_cooldowns(now, active)
        active = [
            s
            for s in active
            if now - self._last_send_at.get(s.tenant_id, float("-inf"))
            >= s.antispam_seconds
        ]
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

    def _prune_cooldowns(self, now: float, active: list[ActiveSender]) -> None:
        """Drop stale ``_last_send_at`` entries so the map can't leak unbounded.

        The map gains one entry per distinct tenant that has ever sent; without
        pruning it grows for the whole process lifetime (departed/deleted
        tenants never reclaimed) — the slow-leak class ``services.auth._prune``
        guards against. Safe-to-drop = a tenant NOT in the current ``active``
        list whose last send is older than the LARGEST cooldown that could ever
        gate it (the governor ceiling, ``_G_MIN_CEIL`` — also the antispam upper
        bound): past that age its timestamp can no longer skip it, so forgetting
        it is equivalent to "never sent" (worst case it sends one slot early on
        return, which the global ``g_min`` sleep still paces). O(active) to
        build the live set, O(stale) to delete — never a full-map scan per pick.
        """
        if not self._last_send_at:
            return
        live = {s.tenant_id for s in active}
        cutoff = now - _G_MIN_CEIL
        stale = [
            tid
            for tid, last in self._last_send_at.items()
            if tid not in live and last < cutoff
        ]
        for tid in stale:
            del self._last_send_at[tid]

    def note_sent(self, tenant_id: int) -> None:
        """Record a tenant's send so its antispam cooldown starts (plan-catalog).

        Called by the worker after a CONFIRMED send. Until ``antispam_seconds``
        elapses, ``pick_next`` skips this tenant — other tenants are still
        interleaved within the gap. Monotonic clock, process memory like the
        rest of the scheduler state.
        """
        self._last_send_at[tenant_id] = self._now()

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
