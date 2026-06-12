"""Pre-launch gate: attribution volume test against a fake bot (Story 4.4, AC2).

Validates the architecture's critical assumption A1 — *the bot always replies
with ``reply_to``* — at volume: thousands of interleaved sends across many
tenants, replies delivered out of order, edit revisions re-delivered (the
legacy ``MessageEdited`` ❌→✅ pattern), and the attribution rule (match
``reply_to_msg_id`` against the ``send_log``) must keep **unmatched replies
≈ 0** and never leak a reply across tenants. Nothing here talks to real
Telegram, the production VPS, or the database — pure simulation, runs in
milliseconds.

Two layers of validation:

1. **This script (automated, repo-local):** the attribution rule itself is
   sound at volume — out-of-order delivery, edits, and tenant interleaving do
   not create unmatched or cross-tenant attributions; replies genuinely
   missing ``reply_to`` land in the unmatched bucket (never misattributed).
2. **The binding gate (manual, owner, staging):** whether the REAL bot
   replies with ``reply_to`` for every real gate (prefix) command at volume
   is only observable against the real bot. Procedure and pass criteria live
   in ``docs/runbooks/gates-de-lanzamiento.md``.

Story 3.1 builds the production attribution pipeline (``send_log`` table,
Story 2.5); it is not merged at the time this gate ships, so ``Attributor``
encodes the SAME contract: ``message_id → (tenant, batch, line)`` recorded
before the send, replies matched solely on ``reply_to_msg_id``, edits deduped
per reply id, everything else into the unmatched bucket.

Usage (from backend/, venv active):
    python -m scripts.attribution_volume_test            # 50 tenants x 100 lines = 5000
    python -m scripts.attribution_volume_test --tenants 10 --lines 1000
    python -m scripts.attribution_volume_test --missing-reply-to-rate 0.02   # gate fails
    python -m scripts.attribution_volume_test --json

Exit code 0 = gate passed (unmatched ratio <= threshold, zero cross-tenant
errors); 1 = gate failed.
"""

import argparse
import json
import random
import sys
from dataclasses import dataclass

DEFAULT_TENANTS = 50
DEFAULT_LINES_PER_TENANT = 100
DEFAULT_EDIT_RATE = 0.15
DEFAULT_SHUFFLE_WINDOW = 40
DEFAULT_MAX_UNMATCHED_RATIO = 0.0
DEFAULT_SEED = 20260612


@dataclass(frozen=True)
class SendRecord:
    """``send_log`` contract (Story 2.5): intent recorded before the send."""

    message_id: int
    tenant_id: str
    batch_id: str
    line: str


@dataclass(frozen=True)
class BotReply:
    """One bot message (or edit revision) as the capture pipeline sees it."""

    reply_id: int
    reply_to_msg_id: int | None
    text: str
    is_edit: bool
    # Ground truth, known only to the simulation — used to verify that the
    # attribution result matches reality (never available in production).
    true_tenant_id: str | None


class FakeBot:
    """Produces replies for sent commands, with configurable pathologies.

    - ``missing_reply_to_rate``: fraction of replies arriving WITHOUT
      ``reply_to`` (the failure mode assumption A1 worries about).
    - ``edit_rate``: fraction of replies later re-delivered as an edit
      revision of the same message (❌→✅), which must not double-attribute.
    """

    def __init__(
        self,
        rng: random.Random,
        *,
        missing_reply_to_rate: float = 0.0,
        edit_rate: float = DEFAULT_EDIT_RATE,
    ) -> None:
        self._rng = rng
        self.missing_reply_to_rate = missing_reply_to_rate
        self.edit_rate = edit_rate
        self._next_reply_id = 1

    def replies_for(self, record: SendRecord) -> list[BotReply]:
        reply_id = self._next_reply_id
        self._next_reply_id += 1
        drops_reply_to = self._rng.random() < self.missing_reply_to_rate
        reply_to = None if drops_reply_to else record.message_id
        first = BotReply(
            reply_id=reply_id,
            reply_to_msg_id=reply_to,
            text=f"❌ processing {record.line}",
            is_edit=False,
            true_tenant_id=record.tenant_id,
        )
        out = [first]
        if self._rng.random() < self.edit_rate:
            out.append(
                BotReply(
                    reply_id=reply_id,  # same message, edited in place
                    reply_to_msg_id=reply_to,
                    text=f"✅ done {record.line}",
                    is_edit=True,
                    true_tenant_id=record.tenant_id,
                )
            )
        return out


class Attributor:
    """Attribution contract: match ``reply_to_msg_id`` against ``send_log``.

    Edits re-attribute to the SAME record (deduped per ``reply_id``) and never
    inflate the attributed count. Replies without ``reply_to`` or pointing at
    an unknown ``message_id`` land in the unmatched bucket — they are NEVER
    guessed onto a tenant (multi-tenant isolation is sacred).
    """

    def __init__(self, send_log: dict[int, SendRecord]) -> None:
        self.send_log = send_log
        self.attributed_by_tenant: dict[str, int] = {}
        self.unmatched: list[BotReply] = []
        self.cross_tenant_errors = 0
        self.edit_revisions = 0
        self._seen_reply_ids: dict[int, int] = {}  # reply_id -> message_id

    def handle(self, reply: BotReply) -> SendRecord | None:
        if reply.reply_id in self._seen_reply_ids:
            # Edit revision of an already-attributed reply: same record, no
            # second attribution.
            self.edit_revisions += 1
            message_id = self._seen_reply_ids[reply.reply_id]
            return self.send_log.get(message_id)
        if reply.reply_to_msg_id is None:
            self.unmatched.append(reply)
            return None
        record = self.send_log.get(reply.reply_to_msg_id)
        if record is None:
            self.unmatched.append(reply)
            return None
        self._seen_reply_ids[reply.reply_id] = reply.reply_to_msg_id
        self.attributed_by_tenant[record.tenant_id] = (
            self.attributed_by_tenant.get(record.tenant_id, 0) + 1
        )
        if reply.true_tenant_id is not None and reply.true_tenant_id != record.tenant_id:
            self.cross_tenant_errors += 1
        return record


@dataclass
class AttributionReport:
    """Outcome of one simulated attribution volume run."""

    sends_total: int
    reply_events_total: int
    unique_replies: int
    attributed: int
    unmatched: int
    edit_revisions: int
    cross_tenant_errors: int
    per_tenant_attributed: dict[str, int]
    max_unmatched_ratio: float = DEFAULT_MAX_UNMATCHED_RATIO

    @property
    def unmatched_ratio(self) -> float:
        return self.unmatched / self.unique_replies if self.unique_replies else 0.0

    @property
    def passed(self) -> bool:
        """Gate verdict: unmatched ≈ 0 and zero cross-tenant attributions."""
        return (
            self.cross_tenant_errors == 0
            and self.unmatched_ratio <= self.max_unmatched_ratio + 1e-12
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "sends_total": self.sends_total,
            "reply_events_total": self.reply_events_total,
            "unique_replies": self.unique_replies,
            "attributed": self.attributed,
            "unmatched": self.unmatched,
            "unmatched_ratio": round(self.unmatched_ratio, 6),
            "edit_revisions": self.edit_revisions,
            "cross_tenant_errors": self.cross_tenant_errors,
            "tenants": len(self.per_tenant_attributed),
            "max_unmatched_ratio": self.max_unmatched_ratio,
            "passed": self.passed,
        }


def run_volume_test(
    *,
    n_tenants: int = DEFAULT_TENANTS,
    lines_per_tenant: int = DEFAULT_LINES_PER_TENANT,
    missing_reply_to_rate: float = 0.0,
    edit_rate: float = DEFAULT_EDIT_RATE,
    shuffle_window: int = DEFAULT_SHUFFLE_WINDOW,
    max_unmatched_ratio: float = DEFAULT_MAX_UNMATCHED_RATIO,
    seed: int = DEFAULT_SEED,
) -> AttributionReport:
    """Interleave sends across tenants, deliver replies out of order, attribute."""
    rng = random.Random(seed)
    bot = FakeBot(
        rng, missing_reply_to_rate=missing_reply_to_rate, edit_rate=edit_rate
    )

    # Round-robin interleaved sends, mirroring the multi-tenant scheduler.
    send_log: dict[int, SendRecord] = {}
    message_id = 0
    reply_stream: list[BotReply] = []
    for line_idx in range(lines_per_tenant):
        for tenant_idx in range(n_tenants):
            message_id += 1
            record = SendRecord(
                message_id=message_id,
                tenant_id=f"tenant-{tenant_idx + 1}",
                batch_id=f"batch-{tenant_idx + 1}",
                line=f"line-{line_idx + 1}",
            )
            send_log[record.message_id] = record
            reply_stream.extend(bot.replies_for(record))

    # Out-of-order delivery: shuffle within a sliding window (Telegram never
    # reorders arbitrarily far, but nearby replies do interleave).
    shuffled: list[BotReply] = list(reply_stream)
    if shuffle_window > 1:
        for start in range(0, len(shuffled), shuffle_window):
            chunk = shuffled[start : start + shuffle_window]
            rng.shuffle(chunk)
            shuffled[start : start + shuffle_window] = chunk

    attributor = Attributor(send_log)
    for reply in shuffled:
        attributor.handle(reply)

    unique_replies = len({r.reply_id for r in reply_stream})
    # An edit revision of an unmatched reply is a second unmatched EVENT for
    # the same reply — count unique replies, not events, in the ratio.
    unmatched_unique = len({r.reply_id for r in attributor.unmatched})
    return AttributionReport(
        sends_total=len(send_log),
        reply_events_total=len(reply_stream),
        unique_replies=unique_replies,
        attributed=sum(attributor.attributed_by_tenant.values()),
        unmatched=unmatched_unique,
        edit_revisions=attributor.edit_revisions,
        cross_tenant_errors=attributor.cross_tenant_errors,
        per_tenant_attributed=dict(attributor.attributed_by_tenant),
        max_unmatched_ratio=max_unmatched_ratio,
    )


def _print_report(report: AttributionReport) -> None:
    print(f"commands sent:        {report.sends_total}")
    print(f"reply events:         {report.reply_events_total} "
          f"({report.edit_revisions} edit revisions)")
    print(f"unique replies:       {report.unique_replies}")
    print(f"attributed:           {report.attributed}")
    print(f"unmatched:            {report.unmatched} "
          f"(ratio {report.unmatched_ratio:.4%}, max {report.max_unmatched_ratio:.4%})")
    print(f"cross-tenant errors:  {report.cross_tenant_errors}")
    if report.passed:
        print("\nGATE PASSED — attribution held at volume (unmatched ≈ 0).")
    else:
        print(
            "\nGATE FAILED — the reply_to assumption did not hold; do NOT "
            "onboard clients until the unmatched bucket is explained."
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pre-launch attribution volume test against a fake bot."
    )
    parser.add_argument("--tenants", type=int, default=DEFAULT_TENANTS)
    parser.add_argument("--lines", type=int, default=DEFAULT_LINES_PER_TENANT,
                        help="lines per tenant")
    parser.add_argument("--missing-reply-to-rate", type=float, default=0.0,
                        help="fraction of replies arriving without reply_to")
    parser.add_argument("--edit-rate", type=float, default=DEFAULT_EDIT_RATE)
    parser.add_argument("--shuffle-window", type=int, default=DEFAULT_SHUFFLE_WINDOW,
                        help="out-of-order delivery window (1 = in order)")
    parser.add_argument("--max-unmatched-ratio", type=float,
                        default=DEFAULT_MAX_UNMATCHED_RATIO)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.tenants < 1 or args.lines < 1:
        parser.error("--tenants and --lines must be >= 1")

    report = run_volume_test(
        n_tenants=args.tenants,
        lines_per_tenant=args.lines,
        missing_reply_to_rate=args.missing_reply_to_rate,
        edit_rate=args.edit_rate,
        shuffle_window=args.shuffle_window,
        max_unmatched_ratio=args.max_unmatched_ratio,
        seed=args.seed,
    )

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        _print_report(report)
    sys.exit(0 if report.passed else 1)


if __name__ == "__main__":
    main()
