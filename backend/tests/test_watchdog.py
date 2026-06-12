"""Story 4.1 watchdog tests: reply-rate collapse over the sliding window
(AC 1), session-loss latching via ``SessionLostError`` (AC 2), the manual-
resume-only contract incl. the owner endpoint (AC 3), the structured logs
(AC 4), the worker/API gates while latched, and the durable latch round-trip.

Same idiom as the rest of the suite: real ASGI app against the dev Postgres,
self-seeding, self-cleaning, ``FakeGateway`` (no real Telegram), events
verified by monkeypatching the broadcaster with a recorder list (2.2 lesson).
Window arithmetic runs on fresh ``Watchdog(now=FakeClock())`` instances
(idiom: the Scheduler unit tests); the persistence test self-skips while the
``watchdog_state`` migration is unapplied (the merge step applies it — the
shared dev Postgres must not be mutated outside Alembic).

Run (from backend/, venv active):  pytest tests/test_watchdog.py
"""

import logging

import pytest
from app.core import capture, send_worker
from app.core import watchdog as watchdog_module
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.core.telegram import SessionLostError
from app.core.watchdog import (
    REASON_REPLY_RATE,
    REASON_SESSION_LOST,
    Watchdog,
    watchdog,
)
from app.db.base import async_session_factory
from app.db.models import BatchLine, User
from app.services import batches as batches_service
from httpx import AsyncClient
from sqlalchemy import select, text

from tests.conftest import FakeGateway

# --- Local helpers -----------------------------------------------------------


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Record every broadcaster emission as ``(tenant_id|None, event, data)``."""
    recorded: list[tuple] = []

    async def emit(tenant_id: int, event: str, data: dict) -> None:
        recorded.append((tenant_id, event, data))

    async def emit_global(event: str, data: dict) -> None:
        recorded.append((None, event, data))

    monkeypatch.setattr(broadcaster, "emit", emit)
    monkeypatch.setattr(broadcaster, "emit_global", emit_global)
    return recorded


async def _noop_persist() -> None:
    """Stand-in for ``Watchdog._persist`` — the ``watchdog_state`` table may
    not exist on the shared dev Postgres until the merge applies the
    migration; persistence has its own dedicated (self-skipping) test."""
    return None


class FakeClock:
    """Deterministic stand-in for ``time.monotonic`` (window unit tests)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _fresh(clock: FakeClock, monkeypatch: pytest.MonkeyPatch) -> Watchdog:
    """A fresh latch with an injectable clock and quiet persistence."""
    w = Watchdog(now=clock)
    monkeypatch.setattr(w, "_persist", _noop_persist)
    return w


def _quiet_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Quiet persistence on the module singleton (integration tests)."""
    monkeypatch.setattr(watchdog, "_persist", _noop_persist)


def _paused_events(events: list[tuple]) -> list[tuple]:
    return [e for e in events if e[1] == "watchdog.paused"]


def _resumed_events(events: list[tuple]) -> list[tuple]:
    return [e for e in events if e[1] == "watchdog.resumed"]


async def _post_batch(http: AsyncClient, text_body: str, gate_id: int):
    return await http.post(
        "/api/batches", json={"text": text_body, "gate_id": gate_id}
    )


async def _lines_of(batch_id: int) -> list[BatchLine]:
    async with async_session_factory() as session:
        stmt = (
            select(BatchLine)
            .where(BatchLine.batch_id == batch_id)
            .order_by(BatchLine.position)
        )
        return list((await session.execute(stmt)).scalars().all())


# --- Reply-rate collapse (AC 1, AC 4) ----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_collapse_pauses_alerts_and_logs(
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    w = _fresh(clock, monkeypatch)

    # Four sends 15s apart with zero replies: below the 5-send threshold.
    for _ in range(4):
        await w.note_sent()
        assert not w.is_paused
        clock.advance(15.0)
    # Fifth send: 5 sends in window, silence spans 60s — collapse.
    with caplog.at_level(logging.WARNING, logger="app.core.watchdog"):
        await w.note_sent()
    assert w.is_paused
    status = w.status()
    assert status["paused"] is True
    assert status["reason"] == REASON_REPLY_RATE
    assert status["paused_at"] is not None
    paused = _paused_events(events)
    assert len(paused) == 1
    assert paused[0][0] is None  # GLOBAL emit (idiom flood.wait)
    assert paused[0][2]["reason"] == REASON_REPLY_RATE
    assert "event=watchdog_paused" in caplog.text  # AC 4


@pytest.mark.asyncio(loop_scope="session")
async def test_replies_in_window_keep_it_calm_until_they_age_out(
    events: list[tuple], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    w = _fresh(clock, monkeypatch)

    # A reply lands early; seven spaced sends never trigger while it is in
    # the window.
    w.note_reply()
    for _ in range(7):
        clock.advance(15.0)
        await w.note_sent()
    assert not w.is_paused

    # Everything slides out of the 300s window; a fresh silent run collapses.
    clock.advance(301.0)
    for _ in range(4):
        await w.note_sent()
        clock.advance(20.0)
    assert not w.is_paused
    await w.note_sent()  # 5 sends, oldest 80s old, zero replies in window
    assert w.is_paused
    assert len(_paused_events(events)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_burst_without_time_span_never_triggers(
    events: list[tuple], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Many sends in the same instant (also how fast test loops look): the
    # silence has no SPAN yet — replies lag sends, a batch's first seconds
    # must not pause every tenant.
    clock = FakeClock()
    w = _fresh(clock, monkeypatch)
    for _ in range(20):
        await w.note_sent()
    assert not w.is_paused
    assert _paused_events(events) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_trigger_is_idempotent_no_duplicate_alerts(
    events: list[tuple], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock()
    w = _fresh(clock, monkeypatch)
    await w.trigger(REASON_SESSION_LOST, detail="first")
    await w.trigger(REASON_REPLY_RATE, detail="second — must be ignored")
    assert len(_paused_events(events)) == 1
    assert w.status()["reason"] == REASON_SESSION_LOST  # the first one holds


# --- Session loss (AC 2, AC 4) ------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_session_lost_latches_immediately(
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    w = _fresh(clock, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="app.core.watchdog"):
        await w.session_lost("AuthKeyUnregisteredError: the key is not registered")
    assert w.is_paused
    assert w.status()["reason"] == REASON_SESSION_LOST
    paused = _paused_events(events)
    assert len(paused) == 1 and paused[0][2]["reason"] == REASON_SESSION_LOST
    assert "event=watchdog_paused reason=session_lost" in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_session_lost_error_releases_line_and_latches(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker path: gateway raises SessionLostError mid-send → the
    claimed line goes back to 'queued' intact (NOT 'failed' — it never went
    out), the global pause latches, and nothing else sends."""
    _quiet_singleton(monkeypatch)
    http, _user = client_user
    res = await _post_batch(http, "uno", gate["id"])
    assert res.status_code == 201
    batch_id = res.json()["id"]

    fake_gateway.errors = [SessionLostError("SessionRevokedError: revoked")]
    assert await send_worker.step() is False
    assert fake_gateway.sent == []

    [line] = await _lines_of(batch_id)
    assert line.state == "queued"  # released, not failed
    assert line.fail_code is None
    assert watchdog.is_paused
    assert watchdog.status()["reason"] == REASON_SESSION_LOST
    assert len(_paused_events(events)) == 1

    # Latched: the next step claims nothing — not even the released line.
    assert await send_worker.step() is False
    assert fake_gateway.sent == []


# --- Manual resume only (AC 3, AC 4) -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_replies_never_unpause_and_resume_resets_the_window(
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = FakeClock()
    w = _fresh(clock, monkeypatch)
    await w.session_lost("dead")

    # Bot life while latched NEVER unpauses (resume is the owner's, AC 3).
    for _ in range(10):
        w.note_reply()
    assert w.is_paused

    with caplog.at_level(logging.INFO, logger="app.core.watchdog"):
        assert await w.resume() is True
    assert not w.is_paused
    assert w.status() == {
        "paused": False,
        "reason": None,
        "detail": None,
        "paused_at": None,
    }
    assert len(_resumed_events(events)) == 1
    assert "event=watchdog_resumed" in caplog.text  # AC 4

    # Fresh window: the pre-pause timestamps are gone — spaced silent sends
    # need the full threshold again before re-triggering.
    for _ in range(4):
        await w.note_sent()
        clock.advance(20.0)
    assert not w.is_paused
    await w.note_sent()
    assert w.is_paused  # and it CAN re-trigger once the signal is real again

    # Second resume after a no-op resume: idempotent, no duplicate event.
    assert await w.resume() is True
    assert await w.resume() is False
    assert len(_resumed_events(events)) == 2


@pytest.mark.asyncio(loop_scope="session")
async def test_resume_endpoint_owner_only_and_idempotent(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _quiet_singleton(monkeypatch)
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    client_http, _user = client_user

    await watchdog.session_lost("dead")
    assert watchdog.is_paused

    # Only the owner may resume (AC 3) — admin and client are forbidden.
    res = await admin_client.post("/api/watchdog/resume")
    assert res.status_code == 403 and res.json()["code"] == "forbidden"
    res = await client_http.post("/api/watchdog/resume")
    assert res.status_code == 403 and res.json()["code"] == "forbidden"
    assert watchdog.is_paused  # nothing moved

    res = await owner_client.post("/api/watchdog/resume")
    assert res.status_code == 204
    assert not watchdog.is_paused
    assert len(_resumed_events(events)) == 1

    # Idempotent: a second resume is 204 with NO duplicate event.
    res = await owner_client.post("/api/watchdog/resume")
    assert res.status_code == 204
    assert len(_resumed_events(events)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_status_endpoint_owner_only(
    ctx: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _quiet_singleton(monkeypatch)
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    res = await admin_client.get("/api/watchdog")
    assert res.status_code == 403

    res = await owner_client.get("/api/watchdog")
    assert res.status_code == 200
    assert res.json() == {
        "paused": False,
        "reason": None,
        "detail": None,
        "paused_at": None,
    }

    await watchdog.session_lost("dead")
    res = await owner_client.get("/api/watchdog")
    body = res.json()
    assert body["paused"] is True
    assert body["reason"] == REASON_SESSION_LOST
    assert body["paused_at"] is not None


# --- Latched gates: worker, POST /api/batches, snapshot ------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_latch_blocks_worker_and_new_batches(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _quiet_singleton(monkeypatch)
    http, _user = client_user
    res = await _post_batch(http, "uno\ndos", gate["id"])
    assert res.status_code == 201
    batch_id = res.json()["id"]

    await watchdog.session_lost("dead")

    # The worker claims nothing while latched.
    assert await send_worker.step() is False
    assert fake_gateway.sent == []
    assert all(line.state == "queued" for line in await _lines_of(batch_id))

    # Create AND append are rejected with the Spanish contract error.
    res = await _post_batch(http, "tres", gate["id"])
    assert res.status_code == 503
    assert res.json()["code"] == "sending_paused"


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_carries_the_latch(
    client_user: tuple[AsyncClient, User],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _quiet_singleton(monkeypatch)
    _http, user = client_user
    await watchdog.session_lost("dead")
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["watchdog"]["paused"] is True
    assert snap["watchdog"]["reason"] == REASON_SESSION_LOST
    assert snap["watchdog"]["paused_at"] is not None


# --- Window feeding: worker record phase + capture arrival ---------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_record_sent_feeds_the_window(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real delivery evaluates the collapse right in the record phase —
    with the thresholds floored, one silent send latches and the queue
    freezes mid-batch."""
    _quiet_singleton(monkeypatch)
    monkeypatch.setattr(watchdog_module, "_MIN_SENDS_IN_WINDOW", 1)
    monkeypatch.setattr(watchdog_module, "_MIN_SILENCE_SPAN_SECONDS", 0.0)
    http, _user = client_user
    res = await _post_batch(http, "uno\ndos", gate["id"])
    assert res.status_code == 201

    assert await send_worker.step() is True  # line 1 delivered → evaluation
    assert watchdog.is_paused
    assert watchdog.status()["reason"] == REASON_REPLY_RATE
    assert len(_paused_events(events)) == 1

    assert await send_worker.step() is False  # latched: line 2 never goes out
    assert len(fake_gateway.sent) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_capture_enqueue_proves_bot_life(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reply ARRIVING through the bridge (capture.enqueue) keeps the
    watchdog calm even with the thresholds floored — arrival is the life
    signal, before any attribution or DB work."""
    _quiet_singleton(monkeypatch)
    monkeypatch.setattr(watchdog_module, "_MIN_SENDS_IN_WINDOW", 1)
    monkeypatch.setattr(watchdog_module, "_MIN_SILENCE_SPAN_SECONDS", 0.0)
    http, _user = client_user
    res = await _post_batch(http, "uno", gate["id"])
    assert res.status_code == 201

    capture.enqueue(
        IncomingReply(message_id=999, reply_to_msg_id=None, text="⏳", edited=False)
    )
    assert await send_worker.step() is True
    assert not watchdog.is_paused  # the reply was in the window
    assert _paused_events(events) == []


# --- Durable latch (AC 3 across restarts) --------------------------------------


async def _watchdog_table_exists() -> bool:
    async with async_session_factory() as session:
        result = await session.execute(
            text("SELECT to_regclass('public.watchdog_state')")
        )
        return result.scalar() is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_latch_persists_and_restores_across_restart(
    events: list[tuple],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """trigger → row; reset (the restart) → load_persisted restores the
    latch; resume → row unpaused. Self-skips until the merge applies the
    ``watchdog_state`` migration (shared dev Postgres is never mutated
    outside Alembic)."""
    if not await _watchdog_table_exists():
        pytest.skip("watchdog_state not migrated yet (the merge step applies it)")
    try:
        await watchdog.session_lost("AuthKeyUnregisteredError: dead")
        watchdog.reset()  # simulate the restart's memory loss
        assert not watchdog.is_paused

        with caplog.at_level(logging.WARNING, logger="app.core.watchdog"):
            await watchdog.load_persisted()
        assert watchdog.is_paused
        assert watchdog.status()["reason"] == REASON_SESSION_LOST
        assert "event=watchdog_restored" in caplog.text

        assert await watchdog.resume() is True
        watchdog.reset()
        await watchdog.load_persisted()
        assert not watchdog.is_paused  # the resumed row never re-latches
    finally:
        # Leave the shared row unpaused whatever happened above.
        watchdog.reset()
        await watchdog._persist()
