"""Tests for the Story 2.4 scheduler: fairness (round-robin), bounded owner
priority, the adaptive interval formula, the FloodWait governor and the
paused-tenant exclusion (AC 5) — plus the deadline pacing fixes absorbed from
the 2.3 review (foreign ``wake()`` must not shorten waits).

Two layers, per the story's testing standards:
- UNIT: pure ``Scheduler``/``ActiveSender`` built by hand, no DB; the
  injectable clock makes the governor deterministic.
- INTEGRATION: real ASGI app + dev Postgres (conftest idiom — self-seeding,
  self-cleaning, ``FakeGateway``, events via monkeypatched broadcaster).
  The conftest's autouse ``reset_scheduler`` wipes the singleton per test.

Run (from backend/, venv active):  pytest tests/test_scheduler.py
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from app.core import send_worker
from app.core.broadcaster import broadcaster
from app.core.scheduler import Scheduler, scheduler
from app.db.base import async_session_factory
from app.db.models import User
from app.db.repos import batches as batches_repo
from app.db.repos.batches import ActiveSender
from app.main import app
from app.services import batches as batches_service
from httpx import ASGITransport, AsyncClient
from telethon.errors import FloodWaitError

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

# --- Unit helpers ------------------------------------------------------------


class FakeClock:
    """Deterministic stand-in for ``time.monotonic`` (governor decay tests)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _sender(
    tenant_id: int, *, owner: bool = False, admin: bool = False
) -> ActiveSender:
    priority = 2 if owner else 1 if admin else 0
    return ActiveSender(
        tenant_id=tenant_id, batch_id=tenant_id * 10, priority=priority
    )


def _pick(sched: Scheduler, active: list[ActiveSender]) -> ActiveSender:
    pick = sched.pick_next(active)
    assert pick is not None
    return pick


# --- Unit: constant interval (owner decision 2026-06-13) ----------------------


def test_interval_is_constant_g_min_regardless_of_n() -> None:
    """Flat interval: G = g_min (default 4.0s) for ANY n — no adaptive band."""
    sched = Scheduler()
    for n in (1, 2, 3, 4, 5, 6, 7, 50):
        assert sched.interval(n) == 4.0


def test_per_client_turn_is_g_times_n() -> None:
    """Round-robin spreads the constant slot: each client's turn is G×n."""
    sched = Scheduler()
    for n in (1, 2, 3, 4, 5, 6, 7, 50):
        assert sched.interval(n) * n == pytest.approx(4.0 * n)


def test_interval_handles_n_zero_defensively() -> None:
    sched = Scheduler()
    assert sched.interval(0) == 4.0  # n is ignored; constant floor either way


# --- Unit: FloodWait governor (AC 4) ------------------------------------------


def test_governor_raises_floor_and_caps_at_ceiling() -> None:
    clock = FakeClock()
    sched = Scheduler(now=clock)
    assert sched.g_min == 4.0
    sched.note_flood_wait(0.0)
    assert sched.g_min == 6.0  # ×1.5
    # The raised floor shows through interval() — the constant rises with it.
    assert sched.interval(7) == 6.0
    for _ in range(20):
        sched.note_flood_wait(0.0)
    assert sched.g_min == 30.0  # ceiling — never beyond


def test_governor_decays_one_step_per_quiet_window() -> None:
    clock = FakeClock()
    sched = Scheduler(now=clock)
    sched.note_flood_wait(0.0)  # 6.0
    sched.note_flood_wait(0.0)  # 9.0
    assert sched.g_min == 9.0

    clock.advance(599.0)
    assert sched.interval(7) == 9.0  # window not over yet — no decay
    clock.advance(1.0)
    assert sched.interval(7) == 6.0  # one ÷1.5 step…
    assert sched.interval(7) == 6.0  # …and only one per window
    clock.advance(600.0)
    assert sched.interval(7) == 4.0  # second window, second step
    clock.advance(600.0)
    assert sched.interval(7) == 4.0  # never below the configured floor


# --- Unit: configurable floor (set_floor) -------------------------------------


def test_set_floor_snaps_to_value_with_no_active_flood() -> None:
    """Steady state (no live FloodWait): raising or lowering snaps instantly."""
    sched = Scheduler()
    sched.set_floor(2.0)
    assert sched.floor == 2.0
    assert sched.g_min == 2.0
    assert sched.interval(1) == 2.0  # applies on the very next send
    sched.set_floor(8.0)
    assert sched.g_min == 8.0  # raise snaps too


def test_set_floor_keeps_governor_elevation_mid_flood() -> None:
    """Mid-flood the live pace keeps its elevation; only the floor moves."""
    clock = FakeClock()
    sched = Scheduler(now=clock)
    sched.note_flood_wait(0.0)  # g_min 4.0 → 6.0, flood active
    sched.set_floor(2.0)
    assert sched.floor == 2.0
    assert sched.g_min == 6.0  # max(elevated, new floor) — not dropped


def test_decay_converges_to_runtime_floor_not_env_default() -> None:
    """The critical wiring: decay returns to the CONFIGURED floor (2.0),
    never the env constant (4.0) and never below the new floor."""
    clock = FakeClock()
    sched = Scheduler(now=clock)
    sched.set_floor(2.0)
    sched.note_flood_wait(0.0)  # 2.0 → 3.0
    sched.note_flood_wait(0.0)  # 3.0 → 4.5
    assert sched.g_min == 4.5

    clock.advance(600.0)
    assert sched.interval(1) == 3.0  # 4.5 ÷1.5
    clock.advance(600.0)
    assert sched.interval(1) == 2.0  # 3.0 ÷1.5 → the runtime floor
    clock.advance(600.0)
    assert sched.interval(1) == 2.0  # never below it (would be 4.0 if buggy)


# --- Unit: round-robin fairness (AC 1) -----------------------------------------


def test_round_robin_cycles_clients_in_tenant_order() -> None:
    sched = Scheduler()
    active = [_sender(1), _sender(2), _sender(3)]
    picks = [_pick(sched, active).tenant_id for _ in range(6)]
    assert picks == [1, 2, 3, 1, 2, 3]


def test_round_robin_skips_paused_client() -> None:
    sched = Scheduler()
    full = [_sender(1), _sender(2), _sender(3)]
    assert _pick(sched, full).tenant_id == 1
    # Tenant 2 pauses → it simply stops appearing in the listing.
    without_b = [_sender(1), _sender(3)]
    picks = [_pick(sched, without_b).tenant_id for _ in range(4)]
    assert picks == [3, 1, 3, 1]


def test_pick_next_empty_returns_none() -> None:
    assert Scheduler().pick_next([]) is None


# --- Unit: bounded owner priority (AC 3) ----------------------------------------


def test_owner_alternates_with_clients_at_exactly_half() -> None:
    sched = Scheduler()
    active = [_sender(1, owner=True), _sender(2), _sender(3)]
    picks = [_pick(sched, active).tenant_id for _ in range(8)]
    # owner, client, owner, client… — owner exactly 50%, never more.
    assert picks == [1, 2, 1, 3, 1, 2, 1, 3]


def test_owner_alone_takes_every_slot() -> None:
    sched = Scheduler()
    active = [_sender(1, owner=True)]
    assert [_pick(sched, active).tenant_id for _ in range(3)] == [1, 1, 1]


def test_owner_jumps_ahead_of_the_client_rotation() -> None:
    sched = Scheduler()
    clients = [_sender(2), _sender(3), _sender(4)]
    assert _pick(sched, clients).tenant_id == 2  # cursor mid-rotation
    # The owner appears → it takes the very NEXT slot…
    with_owner = [_sender(1, owner=True), *clients]
    assert _pick(sched, with_owner).tenant_id == 1
    # …and the client rotation resumes where it left off.
    assert _pick(sched, with_owner).tenant_id == 3


def test_multiple_owner_batches_rotate_within_their_class() -> None:
    sched = Scheduler()
    active = [_sender(1, owner=True), _sender(2, owner=True), _sender(3)]
    picks = [_pick(sched, active).tenant_id for _ in range(6)]
    assert picks == [1, 3, 2, 3, 1, 3]


# --- Unit: 3-tier priority owner > admin > client (bounded) --------------------


def test_three_tiers_rank_owner_admin_client() -> None:
    """All three active: owner 50% of slots, admin 25%, client 25%."""
    sched = Scheduler()
    active = [_sender(1, owner=True), _sender(2, admin=True), _sender(3)]
    picks = [_pick(sched, active).tenant_id for _ in range(8)]
    # owner, admin, owner, client, … — owner every other slot; the non-owner
    # slots split admin/client at exactly half each.
    assert picks == [1, 2, 1, 3, 1, 2, 1, 3]


def test_admin_outranks_client_without_owner() -> None:
    """No owner present: admin takes the owner's old <=50% bound vs clients."""
    sched = Scheduler()
    active = [_sender(1, admin=True), _sender(2), _sender(3)]
    picks = [_pick(sched, active).tenant_id for _ in range(6)]
    assert picks == [1, 2, 1, 3, 1, 2]


def test_admin_alone_takes_every_slot() -> None:
    sched = Scheduler()
    active = [_sender(1, admin=True)]
    assert [_pick(sched, active).tenant_id for _ in range(3)] == [1, 1, 1]


def test_owner_and_admin_alternate_at_half_with_no_clients() -> None:
    sched = Scheduler()
    active = [_sender(1, owner=True), _sender(2, admin=True)]
    picks = [_pick(sched, active).tenant_id for _ in range(4)]
    assert picks == [1, 2, 1, 2]


def test_multiple_admin_batches_rotate_within_their_class() -> None:
    sched = Scheduler()
    active = [_sender(1, admin=True), _sender(2, admin=True), _sender(3)]
    picks = [_pick(sched, active).tenant_id for _ in range(6)]
    assert picks == [1, 3, 2, 3, 1, 3]


# --- Integration helpers ------------------------------------------------------


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    body = res.json()
    batch_id: int = body["id"]
    return batch_id


async def _second_client() -> tuple[AsyncClient, User]:
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    return http, user


async def _batch_state(batch_id: int) -> str | None:
    async with async_session_factory() as session:
        return await batches_repo.get_batch_state(session, batch_id)


async def _count_active() -> int:
    async with async_session_factory() as session:
        return await batches_repo.count_active_senders(session)


# --- Integration: fairness end-to-end (AC 1, 5) --------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_fairness_two_tenants_interleave_strictly(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        batch_a = await _post_batch(http_a, "a1\na2\na3", gate["id"])
        batch_b = await _post_batch(http_b, "b1\nb2\nb3", gate["id"])

        for _ in range(6):
            assert await send_worker.step() is True
        assert await send_worker.step() is False  # both drained → idle

        # Strict alternation between the two tenants, no monopolization.
        value = gate["value"]
        tags = [text.removeprefix(f"{value} ")[0] for text in fake_gateway.sent]
        assert sorted(set(tags)) == ["a", "b"]
        assert tags == [tags[0], tags[1]] * 3
        # Both in-flight batches advanced to completion, interleaved.
        assert await _batch_state(batch_a) == "completed"
        assert await _batch_state(batch_b) == "completed"
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_paused_tenant_is_excluded_then_rejoins(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        await _post_batch(http_a, "a1\na2\na3", gate["id"])
        batch_b = await _post_batch(http_b, "b1\nb2", gate["id"])
        assert await _count_active() == 2

        assert (await http_b.post(f"/api/batches/{batch_b}/pause")).status_code == 204
        # Paused → out of n (AC 2) and out of the rotation: only A is served.
        assert await _count_active() == 1
        assert await send_worker.step() is True
        assert await send_worker.step() is True
        value = gate["value"]
        assert fake_gateway.sent == [f"{value} a1", f"{value} a2"]

        # Resume → back into the rotation: the next slot is B's.
        assert (await http_b.post(f"/api/batches/{batch_b}/resume")).status_code == 204
        assert await _count_active() == 2
        assert await send_worker.step() is True
        assert fake_gateway.sent[-1] == f"{value} b1"
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


# --- Integration: bounded owner priority end-to-end (AC 3) -----------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_priority_alternates_end_to_end(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    http_c, _ = client_user
    await _post_batch(owner_client, "o1\no2", gate["id"])
    await _post_batch(http_c, "c1\nc2", gate["id"])

    for _ in range(4):
        assert await send_worker.step() is True

    value = gate["value"]
    # The owner tier (priority=2) was set by the POST alone; the sequence
    # alternates owner/client — the owner jumps ahead but never exceeds 50%.
    assert fake_gateway.sent == [
        f"{value} o1",
        f"{value} c1",
        f"{value} o2",
        f"{value} c2",
    ]


# --- Integration: selection↔stop race -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_step_idles_when_queue_empties_between_listing_and_claim(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])

    real_listing = batches_repo.active_senders

    async def stale_listing(session: object) -> list[ActiveSender]:
        listed = await real_listing(session)  # type: ignore[arg-type]
        # A stop empties the picked tenant's queue right after the listing.
        async with async_session_factory() as race:
            await batches_repo.delete_queued_lines(race, batch_id)
            await race.commit()
        return listed

    monkeypatch.setattr(batches_repo, "active_senders", stale_listing)

    # No exception, no send: claim finds nothing → idle; next loop rotates.
    assert await send_worker.step() is False
    assert fake_gateway.sent == []


# --- Integration: deadline pacing (2.3 deferred #1) ------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_wait_respecting_state_resleeps_remainder_on_foreign_wake(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    """A wake() belonging to ANOTHER tenant must not shorten the window: the
    state re-read sees the batch still 'sending' and re-sleeps the remainder
    — no early retry against the shared account."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])

    async def foreign_wake() -> None:
        await asyncio.sleep(0.05)
        send_worker.wake()

    start = time.monotonic()
    waker = asyncio.create_task(foreign_wake())
    outcome = await asyncio.wait_for(
        send_worker._wait_respecting_state(batch_id, 0.4), timeout=2.0
    )
    await waker
    assert outcome == "elapsed"
    assert time.monotonic() - start >= 0.4  # full window despite the wake


@pytest.mark.asyncio(loop_scope="session")
async def test_wait_respecting_state_yields_to_own_pause_instantly(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])

    async def pause_soon() -> None:
        await asyncio.sleep(0.05)
        # The pause endpoint flips the state AND fires wake().
        res = await http.post(f"/api/batches/{batch_id}/pause")
        assert res.status_code == 204

    pauser = asyncio.create_task(pause_soon())
    # A 10s window must yield "release" well under a second.
    outcome = await asyncio.wait_for(
        send_worker._wait_respecting_state(batch_id, 10.0), timeout=2.0
    )
    await pauser
    assert outcome == "release"


@pytest.mark.asyncio(loop_scope="session")
async def test_sleep_paced_is_immune_to_wake() -> None:
    """The global pacing sleep (FR12) re-sleeps the remainder unconditionally."""

    async def waker() -> None:
        await asyncio.sleep(0.05)
        send_worker.wake()

    start = time.monotonic()
    task = asyncio.create_task(waker())
    await asyncio.wait_for(send_worker.sleep_paced(0.3), timeout=2.0)
    await task
    assert time.monotonic() - start >= 0.3


# --- Integration: governor + global flood.wait event (AC 4) ----------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_floodwait_raises_governor_and_broadcasts_globally(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[tuple[str, dict]] = []

    async def record_global(event: str, data: dict) -> None:
        recorded.append((event, data))

    monkeypatch.setattr(broadcaster, "emit_global", record_global)

    http, _ = client_user
    await _post_batch(http, "solo", gate["id"])
    fake_gateway.errors.append(FloodWaitError(request=None, capture=0))

    assert scheduler.g_min == 4.0
    assert await send_worker.step() is True  # retried the same line after 0s

    # Both halves of AC 4 together: the governor floor rose ×1.5 …
    assert scheduler.g_min == 6.0
    # … and the FloodWait was explained to EVERYONE (global event).
    assert ("flood.wait", {"seconds": 0}) in recorded
    assert fake_gateway.sent == [f"{gate['value']} solo"]


# --- Integration: honest ETA derived from G×n (AC 2) -----------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_eta_scales_with_active_senders_and_reincludes_paused(
    client_user: tuple[AsyncClient, User],
    gate: dict,
) -> None:
    http_a, user_a = client_user
    batch_a = await _post_batch(http_a, "uno\ndos\ntres", gate["id"])

    # One active sender: 3 × 1 × interval=4.0 (constant).
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user_a.tenant_id)
    assert snap["eta_seconds"] == 12.0

    http_b, user_b = await _second_client()
    try:
        await _post_batch(http_b, "x1\nx2\nx3", gate["id"])

        # Two active senders: 3 × 2 × interval=4.0 (the interval is flat now;
        # ETA grows only through the ×n factor).
        async with async_session_factory() as session:
            snap = await batches_service.snapshot(session, user_a.tenant_id)
        assert snap["eta_seconds"] == 24.0

        # Pause A: it leaves n (B's ETA drops back to n=1) but its OWN
        # "ETA on resume" re-includes it (n_eff = n + 1 = 2).
        assert (await http_a.post(f"/api/batches/{batch_a}/pause")).status_code == 204
        async with async_session_factory() as session:
            snap_a = await batches_service.snapshot(session, user_a.tenant_id)
            snap_b = await batches_service.snapshot(session, user_b.tenant_id)
        assert snap_a["eta_seconds"] == 24.0  # 3 × 2 × 4.0
        assert snap_b["eta_seconds"] == 12.0  # 3 × 1 × 4.0 — paused A excluded
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})
