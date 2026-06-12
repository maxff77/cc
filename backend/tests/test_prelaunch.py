"""Tests for the pre-launch gate harnesses (Story 4.4, AC1+AC2).

Pure simulation — no DB, no ASGI app, no Telegram. Exercises the two
repo-local launch gates:

- ``scripts.load_test_gmin``: the scheduling contract (``G = max(G_min,
  P(n)/n)``, round-robin fairness, bounded owner priority, paused-tenant
  exclusion) holds at ``G_min = 3.0s`` against the fake gateway, and FloodWait
  handling retries the same line while the governor raises ``G_min``.
- ``scripts.attribution_volume_test``: the reply_to attribution rule keeps
  unmatched ≈ 0 at volume, edits never double-attribute, and a reply is never
  attributed across tenants.

Run (from backend/, venv active):  pytest tests/test_prelaunch.py
"""

import pytest
from scripts.attribution_volume_test import run_volume_test
from scripts.load_test_gmin import (
    FakeGateway,
    FloodWaitSimulated,
    ReferenceScheduler,
    build_senders,
    run_load_test,
)

G_MIN = 3.0


def _default_gateway() -> FakeGateway:
    return FakeGateway()


# --- load test: pacing, fairness, priority, pause ---------------------------


def test_gaps_never_below_g_min_and_no_floodwait() -> None:
    """AC1 core: at G_min=3.0s the gateway never flood-waits and pacing holds."""
    senders = build_senders(n_clients=8, lines_per_client=50)
    report = run_load_test(senders, _default_gateway(), g_min=G_MIN)
    assert report.floodwaits == 0
    assert report.min_gap >= G_MIN - 1e-9
    assert report.total_sent == 8 * 50
    assert report.passed


def test_round_robin_is_fair_across_clients() -> None:
    """No client monopolizes: with equal queues, send counts match exactly."""
    senders = build_senders(n_clients=5, lines_per_client=30)
    report = run_load_test(senders, _default_gateway(), g_min=G_MIN)
    client_counts = {
        sid: n for sid, n in report.sends_per_sender.items() if sid != "owner"
    }
    assert set(client_counts.values()) == {30}
    # Interleaved, not sequential: every client's first send happens within
    # the first full rotation.
    first_sends = sorted(s.sent_at[0] for s in senders)
    assert first_sends[-1] - first_sends[0] < report.avg_gap * len(senders)


def test_per_client_cadence_stays_in_the_band() -> None:
    """n=3 active clients: cadence ≈ P(3)=15s, inside the 10–20s band."""
    senders = build_senders(n_clients=3, lines_per_client=40)
    report = run_load_test(senders, _default_gateway(), g_min=G_MIN)
    for cadence in report.per_client_cadence.values():
        assert 10.0 <= cadence <= 20.0
        assert abs(cadence - 15.0) < 1.0


def test_owner_jumps_rotation_but_capped_at_half_slots() -> None:
    """Owner priority is bounded: at most 50% of slots while clients are active."""
    senders = build_senders(n_clients=4, lines_per_client=40, owner_lines=200)
    report = run_load_test(senders, _default_gateway(), g_min=G_MIN)
    assert report.owner_slot_share <= 0.5 + 1e-9
    # Owner is not starved either: every owner line went out.
    assert report.sends_per_sender["owner"] == 200
    assert report.floodwaits == 0


def test_paused_client_is_excluded_from_n() -> None:
    """A paused tenant sends nothing and does not inflate everyone's interval."""
    senders = build_senders(
        n_clients=5, lines_per_client=20, paused_clients=("client-5",)
    )
    report = run_load_test(senders, _default_gateway(), g_min=G_MIN)
    assert report.sends_per_sender["client-5"] == 0
    # n=4 active -> G = P(4)/4 = 17.5/4 = 4.375s, NOT P(5)/5 = 4.0s.
    assert abs(report.avg_gap - 4.375) < 0.2
    assert report.passed


def test_adaptive_formula_matches_architecture() -> None:
    """G = max(G_min, P(n)/n) with P linear from 10s (n=1) to 20s (n>=5)."""
    assert ReferenceScheduler.per_client_target(1) == 10.0
    assert ReferenceScheduler.per_client_target(3) == 15.0
    assert ReferenceScheduler.per_client_target(5) == 20.0
    assert ReferenceScheduler.per_client_target(50) == 20.0
    senders = build_senders(n_clients=1, lines_per_client=1)
    sched = ReferenceScheduler(senders, g_min=G_MIN)
    assert sched.global_interval() == 10.0  # P(1)/1 > G_min
    big = build_senders(n_clients=10, lines_per_client=1)
    sched_big = ReferenceScheduler(big, g_min=G_MIN)
    assert sched_big.global_interval() == G_MIN  # 20/10 = 2.0 < G_min floor


# --- load test: FloodWait handling and the governor -------------------------


def test_unsafe_g_min_triggers_floodwait_and_governor_raise() -> None:
    """An aggressive G_min is detected: FloodWaits fire and G_min auto-raises."""
    senders = build_senders(n_clients=10, lines_per_client=30)
    report = run_load_test(senders, _default_gateway(), g_min=1.0)
    assert report.floodwaits > 0
    assert report.g_min_raises > 0
    assert report.final_g_min > 1.0
    assert not report.passed


def test_floodwait_retries_same_line_no_loss_no_duplicate() -> None:
    """A flood-waited line is retried, never lost and never double-sent."""
    senders = build_senders(n_clients=10, lines_per_client=30)
    gateway = _default_gateway()
    report = run_load_test(senders, gateway, g_min=1.0)
    assert report.lines_lost == 0
    assert report.lines_duplicated == 0
    assert report.total_sent == 10 * 30


def test_gateway_rejects_above_window_and_recovers() -> None:
    """The fake gateway flood model itself: cap per window, cooldown, recovery."""
    gateway = FakeGateway(window_seconds=60.0, max_in_window=2, floodwait_seconds=30.0)
    assert gateway.send(0.0, "c", "l1") == 1
    assert gateway.send(1.0, "c", "l2") == 2
    with pytest.raises(FloodWaitSimulated) as exc_info:
        gateway.send(2.0, "c", "l3")
    assert exc_info.value.seconds == 30.0
    # After the cooldown AND once the window slides, sending works again.
    assert gateway.send(62.0, "c", "l3") == 3
    assert gateway.floodwaits == 1


# --- attribution volume test -------------------------------------------------


def test_volume_run_attributes_everything() -> None:
    """AC2 core: 5000 interleaved sends, replies out of order — unmatched = 0."""
    report = run_volume_test(n_tenants=50, lines_per_tenant=100)
    assert report.sends_total == 5000
    assert report.attributed == 5000
    assert report.unmatched == 0
    assert report.unmatched_ratio == 0.0
    assert report.passed


def test_attribution_never_crosses_tenants() -> None:
    """Isolation: an attributed reply always lands on its true tenant."""
    report = run_volume_test(n_tenants=20, lines_per_tenant=50, edit_rate=0.3)
    assert report.cross_tenant_errors == 0
    assert set(report.per_tenant_attributed.values()) == {50}


def test_missing_reply_to_lands_in_unmatched_and_fails_gate() -> None:
    """Replies without reply_to are never guessed onto a tenant — they fail the gate."""
    report = run_volume_test(
        n_tenants=10, lines_per_tenant=100, missing_reply_to_rate=0.02
    )
    assert report.unmatched > 0
    assert report.attributed == report.unique_replies - report.unmatched
    assert report.cross_tenant_errors == 0
    assert not report.passed


def test_edits_do_not_double_attribute() -> None:
    """❌→✅ edit revisions re-attribute to the same record, count stays exact."""
    report = run_volume_test(n_tenants=10, lines_per_tenant=100, edit_rate=0.5)
    assert report.edit_revisions > 0
    assert report.reply_events_total > report.unique_replies
    assert report.attributed == report.sends_total  # one attribution per send
    assert report.passed


def test_out_of_order_delivery_still_matches() -> None:
    """A large shuffle window (edits may arrive before originals) changes nothing."""
    report = run_volume_test(
        n_tenants=5, lines_per_tenant=200, edit_rate=0.4, shuffle_window=500
    )
    assert report.unmatched == 0
    assert report.cross_tenant_errors == 0
    assert report.attributed == report.sends_total
    assert report.passed
