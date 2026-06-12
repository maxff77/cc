"""Ban-guardrail alerting (Story 4.3): sliding-window alert latches.

Two leading indicators of account trouble get an owner-visible alert when
they repeat inside a window: FloodWaits (the leading ban indicator, AC 1)
and unmatched replies (attribution health, AC 3). Unlike the watchdog
(Story 4.1) these alerts never pause anything — 4.1 protects, 4.3 observes.
The raw counters live where the events are born (scheduler / capture /
send worker); this module only owns the windows and the firing latch.

Firing contract (recorded decision, mirror of the watchdog's idempotent
latch): an alert fires ONCE when the count crosses the threshold; sustained
saturation never re-fires (no spam); once the window drains back below the
threshold the latch re-arms on its own — no owner action required (it is
informative, not a pause). The alert is a structured WARNING log (AC 2,
journald-greppable) plus a GLOBAL broadcaster event (idiom ``flood.wait`` /
``watchdog.paused`` — the owner's tab is one of everyone's; the UI decides
per role what to show).

Thresholds/windows are module constants, NOT settings (the 2.5 rule:
pipeline internals are never configuration). The flood window deliberately
equals the governor's decay window: if the governor could not calm the
account within its own window, the owner must see it.
"""

import logging
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.broadcaster import broadcaster

logger = logging.getLogger(__name__)

KIND_FLOOD_WAIT = "flood_wait"
KIND_UNMATCHED_REPLIES = "unmatched_replies"

# FloodWait alert (AC 1): this many events inside the window alert the owner.
# The window matches the governor's decay window (scheduler) on purpose.
_FLOOD_THRESHOLD = 3
_FLOOD_WINDOW_SECONDS = 600.0
# Unmatched-replies alert (AC 3): healthy operation keeps the bucket at ~0 —
# sustained growth inside the window is abnormal by definition.
_UNMATCHED_THRESHOLD = 5
_UNMATCHED_WINDOW_SECONDS = 600.0


class SlidingAlert:
    """Sliding event window + the once-per-saturation firing latch.

    The clock is injectable so the window arithmetic is deterministic under
    test (idiom ``Scheduler``/``Watchdog``); production uses
    ``time.monotonic``.
    """

    def __init__(
        self,
        kind: str,
        threshold: int,
        window_seconds: float,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._kind = kind
        self._threshold = threshold
        self._window_seconds = window_seconds
        self._now = now
        self._events: deque[float] = deque()
        self._alerting = False

    def reset(self) -> None:
        """Wipe window + latch (tests — module-level state trap)."""
        self._events.clear()
        self._alerting = False

    def _prune(self, now: float) -> None:
        """Drop timestamps that slid out of the window."""
        horizon = now - self._window_seconds
        while self._events and self._events[0] < horizon:
            self._events.popleft()

    def count_in_window(self) -> int:
        """Events currently inside the window (observability slice)."""
        self._prune(self._now())
        return len(self._events)

    def is_alerting(self) -> bool:
        """True while the window stays saturated past the last firing.

        Reading re-arms a drained latch — the observability GET reports an
        honest ``alert_active`` even when no new event pruned the window.
        """
        if self.count_in_window() < self._threshold:
            self._alerting = False
        return self._alerting

    async def note(self, detail: str) -> None:
        """Record one event; fire when the count CROSSES the threshold.

        Below threshold → re-arm (the window drained). At/over threshold
        with the latch set → silent (sustained saturation never spams).
        """
        now = self._now()
        self._events.append(now)
        self._prune(now)
        if len(self._events) < self._threshold:
            self._alerting = False
            return
        if self._alerting:
            return
        self._alerting = True
        count = len(self._events)
        # Structured log (AC 2) — journald-greppable alongside the raw
        # event=flood_wait / event=unmatched_reply lines it summarizes.
        logger.warning(
            "event=guardrail_alert kind=%s count=%s window_seconds=%s detail=%s",
            self._kind,
            count,
            int(self._window_seconds),
            detail,
        )
        await broadcaster.emit_global(
            "guardrail.alert",
            {
                "kind": self._kind,
                "count": count,
                "window_seconds": self._window_seconds,
                "detail": detail,
                "at": datetime.now(UTC).isoformat(),
            },
        )


# Module-level singletons (same idiom as scheduler / watchdog / broadcaster).
flood_alert = SlidingAlert(KIND_FLOOD_WAIT, _FLOOD_THRESHOLD, _FLOOD_WINDOW_SECONDS)
unmatched_alert = SlidingAlert(
    KIND_UNMATCHED_REPLIES, _UNMATCHED_THRESHOLD, _UNMATCHED_WINDOW_SECONDS
)


async def note_flood_wait() -> None:
    """A FloodWait happened (send worker) — feed the alert window."""
    await flood_alert.note(
        "repeated FloodWaits inside the alert window — leading ban indicator"
    )


async def note_unmatched() -> None:
    """A reply was FINALLY bucketed unmatched (capture) — feed the window.

    Attribution retries (the send→record race) must NOT call this — only the
    final attempt that really counts toward the bucket does.
    """
    await unmatched_alert.note(
        "unmatched replies growing abnormally — attribution health degraded"
    )


def reset() -> None:
    """Wipe both alert windows (tests)."""
    flood_alert.reset()
    unmatched_alert.reset()
