"""Pre-launch gate: ``G_min`` load test against a fake Telegram gateway (Story 4.4, AC1).

Exercises the multi-tenant scheduling contract (constant interval ``G = G_min``,
round-robin, owner priority bounded at 50%, paused tenants excluded from ``n``)
at ``G_min = 4.0s`` against a **fake gateway** that models
Telegram-side flood control as a sliding-window rate limit. Nothing here talks
to real Telegram, the production VPS, or the database — the simulation runs on
a virtual clock and finishes in milliseconds.

Two layers of validation:

1. **This script (automated, repo-local):** the scheduling contract never
   paces below ``G_min``, stays fair, bounds owner priority, reacts to
   FloodWait by retrying the same line (never losing or duplicating it) and
   raising ``G_min`` (governor). Run it in CI/dev as often as you like.
2. **The binding gate (manual, owner, staging):** real FloodWait behavior is
   only observable against real Telegram. Procedure and pass criteria live in
   ``docs/runbooks/gates-de-lanzamiento.md`` — never run that part from a dev
   machine against the production account.

Story 2.4 builds the production scheduler; it is not merged at the time this
gate ships, so ``ReferenceScheduler`` encodes the SAME architecture contract
the production scheduler must satisfy. Once 2.4 lands, point the harness at
the real scheduler (seam: anything implementing ``next_sender()`` /
``global_interval()`` over ``SimSender`` state) and keep the assertions.

Usage (from backend/, venv active):
    python -m scripts.load_test_gmin                 # defaults: 8 clients x 100 lines, G_min=4.0
    python -m scripts.load_test_gmin --clients 10 --lines 200 --owner-lines 50
    python -m scripts.load_test_gmin --g-min 1.0     # demonstrates an UNSAFE G_min (gate fails)
    python -m scripts.load_test_gmin --json          # machine-readable report

Exit code 0 = gate passed (no FloodWait at the tested ``G_min``, pacing never
below it); 1 = gate failed (raise ``G_min`` and re-test).
"""

import argparse
import json
import sys
from collections import deque
from dataclasses import dataclass, field

DEFAULT_G_MIN = 4.0
# Fake-gateway flood model: tolerate up to 22 messages per rolling 60s window.
# G_min=4.0 sustains 15/min (passes with margin); 2.0s pacing -> 30/min (fails).
DEFAULT_WINDOW_SECONDS = 60.0
DEFAULT_MAX_IN_WINDOW = 22
DEFAULT_FLOODWAIT_SECONDS = 30.0
# Governor: every FloodWait raises G_min by this factor (self-tuning upward).
DEFAULT_GOVERNOR_FACTOR = 1.25
# Hard cap on simulation iterations — a misbehaving policy must fail loudly,
# not hang the gate.
MAX_EVENTS_FACTOR = 50


class FloodWaitSimulated(Exception):
    """Fake-gateway analog of Telethon's FloodWaitError."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        super().__init__(f"simulated flood wait: {seconds:.1f}s")


@dataclass(frozen=True)
class SentMessage:
    """One message the fake gateway accepted."""

    at: float
    sender_id: str
    line: str


class FakeGateway:
    """Models Telegram flood control: a sliding-window rate limit.

    Accepts at most ``max_in_window`` messages per rolling ``window_seconds``;
    past that it raises :class:`FloodWaitSimulated` (and keeps rejecting until
    the cooldown elapses), mirroring how Telegram answers a too-fast sender.
    """

    def __init__(
        self,
        *,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        max_in_window: int = DEFAULT_MAX_IN_WINDOW,
        floodwait_seconds: float = DEFAULT_FLOODWAIT_SECONDS,
    ) -> None:
        self.window_seconds = window_seconds
        self.max_in_window = max_in_window
        self.floodwait_seconds = floodwait_seconds
        self.sent: list[SentMessage] = []
        self.floodwaits = 0
        self._window: deque[float] = deque()
        self._cooldown_until = 0.0

    def send(self, now: float, sender_id: str, line: str) -> int:
        """Accept the message or raise :class:`FloodWaitSimulated`.

        Returns the gateway-assigned ``message_id`` (1-based, monotonic).
        """
        if now < self._cooldown_until:
            self.floodwaits += 1
            raise FloodWaitSimulated(self._cooldown_until - now)
        while self._window and self._window[0] <= now - self.window_seconds:
            self._window.popleft()
        if len(self._window) + 1 > self.max_in_window:
            self.floodwaits += 1
            self._cooldown_until = now + self.floodwait_seconds
            raise FloodWaitSimulated(self.floodwait_seconds)
        self._window.append(now)
        self.sent.append(SentMessage(at=now, sender_id=sender_id, line=line))
        return len(self.sent)


@dataclass
class SimSender:
    """One tenant's send queue inside the simulation."""

    sender_id: str
    role: str  # "owner" | "client"
    pending: deque[str]
    paused: bool = False
    sent_at: list[float] = field(default_factory=list)


class ReferenceScheduler:
    """Reference implementation of the architecture's scheduling contract.

    - ``G = G_min`` constant: one send every ``G_min`` regardless of ``n``
      (owner decision 2026-06-13; the old adaptive ``P(n)/n`` band is gone).
      Round-robin spreads the slot, so each client's turn is ``G×n``.
    - Round-robin across active clients (stable rotation).
    - Owner lines jump the rotation but take at most 50% of send slots while
      clients are active; with no active clients the owner sends at ``G_min``.
    - Governor: each FloodWait raises ``G_min`` by ``governor_factor``.
    """

    def __init__(
        self,
        senders: list[SimSender],
        *,
        g_min: float = DEFAULT_G_MIN,
        governor_factor: float = DEFAULT_GOVERNOR_FACTOR,
    ) -> None:
        self.senders = senders
        self.g_min = g_min
        self.initial_g_min = g_min
        self.governor_factor = governor_factor
        self.g_min_raises: list[tuple[float, float]] = []  # (sim time, new G_min)
        self._rotation: deque[SimSender] = deque(
            s for s in senders if s.role == "client"
        )
        self._last_slot_was_owner = False

    def active_client_count(self) -> int:
        return sum(
            1
            for s in self.senders
            if s.role == "client" and not s.paused and s.pending
        )

    def global_interval(self) -> float:
        """Constant interval between sends — flat ``g_min``, n ignored (matches
        production: round-robin spreads the slot, so each client's turn is
        ``G×n``). The governor still raises ``g_min`` on FloodWait.
        """
        return self.g_min

    def _owner_with_pending(self) -> SimSender | None:
        for s in self.senders:
            if s.role == "owner" and not s.paused and s.pending:
                return s
        return None

    def _next_client(self) -> SimSender | None:
        for _ in range(len(self._rotation)):
            sender = self._rotation.popleft()
            self._rotation.append(sender)
            if not sender.paused and sender.pending:
                return sender
        return None

    def next_sender(self) -> SimSender | None:
        """Pick the sender for the next slot (owner-bounded round-robin)."""
        owner = self._owner_with_pending()
        client = self._next_client()
        if owner is not None and (client is None or not self._last_slot_was_owner):
            self._last_slot_was_owner = True
            if client is not None:
                # The slot went to the owner — give the skipped client its
                # turn back so the rotation stays fair.
                self._rotation.rotate(1)
            return owner
        self._last_slot_was_owner = False
        return client

    def on_floodwait(self, now: float) -> None:
        """Governor: raise ``G_min`` toward the safe band."""
        self.g_min = round(self.g_min * self.governor_factor, 3)
        self.g_min_raises.append((now, self.g_min))


@dataclass
class LoadReport:
    """Outcome of one simulated load run."""

    g_min_tested: float
    final_g_min: float
    total_sent: int
    duration_seconds: float
    min_gap: float
    avg_gap: float
    floodwaits: int
    g_min_raises: int
    per_client_cadence: dict[str, float]
    owner_slot_share: float
    sends_per_sender: dict[str, int]
    lines_lost: int
    lines_duplicated: int

    @property
    def passed(self) -> bool:
        """Gate verdict: G_min held (no FloodWait, pacing never below it)."""
        return (
            self.floodwaits == 0
            and self.lines_lost == 0
            and self.lines_duplicated == 0
            and (self.total_sent < 2 or self.min_gap >= self.g_min_tested - 1e-9)
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "g_min_tested": self.g_min_tested,
            "final_g_min": self.final_g_min,
            "total_sent": self.total_sent,
            "duration_seconds": round(self.duration_seconds, 3),
            "min_gap": round(self.min_gap, 3),
            "avg_gap": round(self.avg_gap, 3),
            "floodwaits": self.floodwaits,
            "g_min_raises": self.g_min_raises,
            "per_client_cadence": {
                k: round(v, 3) for k, v in self.per_client_cadence.items()
            },
            "owner_slot_share": round(self.owner_slot_share, 4),
            "sends_per_sender": self.sends_per_sender,
            "lines_lost": self.lines_lost,
            "lines_duplicated": self.lines_duplicated,
            "passed": self.passed,
        }


def build_senders(
    *,
    n_clients: int,
    lines_per_client: int,
    owner_lines: int = 0,
    paused_clients: tuple[str, ...] = (),
) -> list[SimSender]:
    """Build the simulation tenants: ``client-1..n`` plus an optional owner."""
    senders = [
        SimSender(
            sender_id=f"client-{i + 1}",
            role="client",
            pending=deque(f"client-{i + 1}/line-{j + 1}" for j in range(lines_per_client)),
            paused=f"client-{i + 1}" in paused_clients,
        )
        for i in range(n_clients)
    ]
    if owner_lines > 0:
        senders.append(
            SimSender(
                sender_id="owner",
                role="owner",
                pending=deque(f"owner/line-{j + 1}" for j in range(owner_lines)),
            )
        )
    return senders


def run_load_test(
    senders: list[SimSender],
    gateway: FakeGateway,
    *,
    g_min: float = DEFAULT_G_MIN,
    governor_factor: float = DEFAULT_GOVERNOR_FACTOR,
) -> LoadReport:
    """Drain every non-paused sender through the gateway on a virtual clock."""
    scheduler = ReferenceScheduler(
        senders, g_min=g_min, governor_factor=governor_factor
    )
    expected_lines = [
        line for s in senders if not s.paused for line in s.pending
    ]
    clock = 0.0
    gaps: list[float] = []
    last_send_at: float | None = None
    owner_sends_interleaved = 0
    slots_interleaved = 0
    max_events = max(1, len(expected_lines)) * MAX_EVENTS_FACTOR

    for _ in range(max_events):
        sender = scheduler.next_sender()
        if sender is None:
            break
        line = sender.pending[0]
        clients_active = scheduler.active_client_count() > 0
        try:
            gateway.send(clock, sender.sender_id, line)
        except FloodWaitSimulated as fw:
            # Same semantics as the production pipeline: sleep the requested
            # duration, raise the governor, retry the SAME line (still at the
            # head of the sender's queue).
            clock += fw.seconds
            scheduler.on_floodwait(clock)
            continue
        sender.pending.popleft()
        sender.sent_at.append(clock)
        if sender.role == "owner" and clients_active:
            owner_sends_interleaved += 1
        if clients_active:
            slots_interleaved += 1
        if last_send_at is not None:
            gaps.append(clock - last_send_at)
        last_send_at = clock
        clock += scheduler.global_interval()
    else:
        raise RuntimeError(
            "simulation exceeded the event cap — scheduling policy is stuck"
        )

    sent_lines = [m.line for m in gateway.sent]
    lines_lost = len(set(expected_lines) - set(sent_lines))
    lines_duplicated = len(sent_lines) - len(set(sent_lines))

    cadence: dict[str, float] = {}
    for s in senders:
        if s.role == "client" and len(s.sent_at) >= 2:
            diffs = [b - a for a, b in zip(s.sent_at, s.sent_at[1:], strict=False)]
            cadence[s.sender_id] = sum(diffs) / len(diffs)

    return LoadReport(
        g_min_tested=g_min,
        final_g_min=scheduler.g_min,
        total_sent=len(gateway.sent),
        duration_seconds=last_send_at or 0.0,
        min_gap=min(gaps) if gaps else 0.0,
        avg_gap=sum(gaps) / len(gaps) if gaps else 0.0,
        floodwaits=gateway.floodwaits,
        g_min_raises=len(scheduler.g_min_raises),
        per_client_cadence=cadence,
        owner_slot_share=(
            owner_sends_interleaved / slots_interleaved if slots_interleaved else 0.0
        ),
        sends_per_sender={s.sender_id: len(s.sent_at) for s in senders},
        lines_lost=lines_lost,
        lines_duplicated=lines_duplicated,
    )


def _print_report(report: LoadReport) -> None:
    print(f"G_min tested:        {report.g_min_tested:.2f}s")
    print(f"messages sent:       {report.total_sent}")
    print(f"simulated duration:  {report.duration_seconds:.1f}s")
    print(f"min gap between sends: {report.min_gap:.3f}s")
    print(f"avg gap between sends: {report.avg_gap:.3f}s")
    print(f"FloodWaits:          {report.floodwaits}")
    print(
        f"governor raises:     {report.g_min_raises} "
        f"(final G_min: {report.final_g_min:.3f}s)"
    )
    print(f"owner slot share:    {report.owner_slot_share:.1%} (bound: 50%)")
    if report.per_client_cadence:
        worst = max(report.per_client_cadence.values())
        best = min(report.per_client_cadence.values())
        print(f"per-client cadence:  {best:.1f}s .. {worst:.1f}s between own sends")
    print(f"lines lost:          {report.lines_lost}")
    print(f"lines duplicated:    {report.lines_duplicated}")
    if report.passed:
        print(f"\nGATE PASSED — G_min={report.g_min_tested:.2f}s held under this load.")
    else:
        print(
            f"\nGATE FAILED — raise G_min (governor suggests "
            f">= {report.final_g_min:.3f}s) and re-test."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-launch G_min load test against a fake Telegram gateway."
    )
    parser.add_argument("--clients", type=int, default=8, help="concurrent client senders")
    parser.add_argument("--lines", type=int, default=100, help="lines per client")
    parser.add_argument("--owner-lines", type=int, default=0, help="owner lines (priority path)")
    parser.add_argument("--g-min", type=float, default=DEFAULT_G_MIN, help="G_min to validate")
    parser.add_argument(
        "--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS,
        help="fake gateway: flood window size",
    )
    parser.add_argument(
        "--max-in-window", type=int, default=DEFAULT_MAX_IN_WINDOW,
        help="fake gateway: messages tolerated per window",
    )
    parser.add_argument(
        "--floodwait-seconds", type=float, default=DEFAULT_FLOODWAIT_SECONDS,
        help="fake gateway: imposed wait on violation",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    if args.clients < 1 or args.lines < 1:
        parser.error("--clients and --lines must be >= 1")

    senders = build_senders(
        n_clients=args.clients,
        lines_per_client=args.lines,
        owner_lines=args.owner_lines,
    )
    gateway = FakeGateway(
        window_seconds=args.window_seconds,
        max_in_window=args.max_in_window,
        floodwait_seconds=args.floodwait_seconds,
    )
    report = run_load_test(senders, gateway, g_min=args.g_min)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_report(report)
    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
