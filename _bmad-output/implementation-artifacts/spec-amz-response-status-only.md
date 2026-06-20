---
title: 'Amazon gate: collapse Approved/Declined replies to the bare status line'
type: 'feature'
created: '2026-06-20'
status: 'done'
route: 'one-shot'
---

# Amazon gate: collapse Approved/Declined replies to the bare status line

## Intent

**Problem:** The Amazon ("amz") gate display transform rendered Approved/Declined cookie-mode replies as a branded `◈ Aprobada … ▸ TARJETA VINCULADA LIVE` block carrying CC + Time + bot Response copy. The owner wants a minimal client-facing view: just the verdict.

**Approach:** For cookie-mode (Amazon) replies, reduce an Approved/Declined reply to the `☇ CC: <card>` line + the canonical `⌿ Status: …` line, dropping the bot's `Response`/`Time`/`Removed` copy (a Declined reply with no CC line collapses to just the status line); any other reply (cookie errors, format help, plain edits) passes through unchanged. Verdict classification is delegated to the owner-locked `parse_amazon_verdict` (the same binding token-exact rule the capture engine stores by) so the displayed status can never disagree with the engine — replacing the transform's own weaker `startswith` parser. The transform is gated on the session `cookie_mode` flag (`capture_session.cookie_mode`, threaded to all four display surfaces) — NOT the gate name. This is load-bearing: the prior branded transform keyed off `"amz" in gate_name.lower()`, which the real gate name does not contain, so it was a silent no-op in production (the client saw raw bot text). Display-only: stored DB text is never mutated; the admin cross-tenant view stays raw.

## Suggested Review Order

1. [`../../backend/app/core/display_transform.py`](../../backend/app/core/display_transform.py) — the whole change: delegate verdict to `parse_amazon_verdict`, emit the two canonical bare lines, raw passthrough otherwise. Confirm the gate-name guard and the `VERDICT_APPROVED`/`VERDICT_DECLINED` branches.
2. [`../../backend/tests/test_display_transform.py`](../../backend/tests/test_display_transform.py) — the invariant. Note the adversarial near-miss cases (`Approvedance`, `Declinedxyz`) that must pass raw, and the trailing-junk case that must stay bare.
3. [`./deferred-work.md`](./deferred-work.md) — the one deferred edge (gate-name substring vs `cookie_mode`), last section.
