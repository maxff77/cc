"""Story 2.5 hardening tests: write-ahead ``send_log`` (intent BEFORE
Telegram, ``message_id`` after), retry cap=3 + the 'failed' line state, the
fail-stop without DB, boot reconciliation (confirmed or re-queued — never
double-sent), plan-expiry cancellation mid-batch, the GLOBAL FloodWait window
(2-4 deferred #1) and the structured pipeline logs.

Same idiom as the rest of the suite: real ASGI app against the dev Postgres,
self-seeding, self-cleaning, ``FakeGateway`` (no real Telegram), events
verified by monkeypatching the broadcaster with a recorder list (2.2 lesson).

Run (from backend/, venv active):  pytest tests/test_send_hardening.py
"""

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

import pytest
from app.core import send_worker
from app.core.broadcaster import broadcaster
from app.core.scheduler import Scheduler, scheduler
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine, SendLog, User
from app.db.repos import batches as batches_repo
from app.db.repos import send_log as send_log_repo
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from telethon.errors import FloodWaitError

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

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


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    batch_id: int = res.json()["id"]
    return batch_id


async def _lines_of(batch_id: int) -> list[BatchLine]:
    async with async_session_factory() as session:
        stmt = (
            select(BatchLine)
            .where(BatchLine.batch_id == batch_id)
            .order_by(BatchLine.position)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _batch_state(batch_id: int) -> str | None:
    async with async_session_factory() as session:
        return await batches_repo.get_batch_state(session, batch_id)


async def _send_log_rows(batch_id: int) -> list[SendLog]:
    async with async_session_factory() as session:
        stmt = (
            select(SendLog).where(SendLog.batch_id == batch_id).order_by(SendLog.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _claim_with_intent(batch_id: int, position: int = 0) -> int:
    """Claim the batch's queued line at ``position`` + write its intent (a
    crashed worker's claim transaction, replayed by hand)."""
    async with async_session_factory() as session:
        stmt = select(BatchLine).where(
            BatchLine.batch_id == batch_id,
            BatchLine.state == "queued",
            BatchLine.position == position,
        )
        line = (await session.execute(stmt)).scalars().first()
        assert line is not None
        await batches_repo.mark_sending(session, line)
        await send_log_repo.record_intent(session, line)
        await session.commit()
        return line.id


async def _expire_plan(user_id: int) -> None:
    """Direct-seed idiom: flip the client's plan into the past."""
    async with async_session_factory() as session:
        row = await session.get(User, user_id)
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()


class FakeClock:
    """Deterministic stand-in for ``time.monotonic`` (window unit tests)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# --- Write-ahead send_log (AC 1 + 2) ------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_intent_committed_before_telegram_and_message_id_after(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC 2: the intent row is COMMITTED before gateway.send runs (a crash
    between send and record cannot create orphan replies), and ``message_id``
    is filled in afterwards. Asserts are recorded, never raised in-band — an
    AssertionError inside send would be swallowed by the retry policy."""

    class IntentCheckingGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.intent_committed: list[bool] = []

        async def send(self, text: str) -> int:
            # A SEPARATE session sees only committed state.
            async with async_session_factory() as session:
                line_id = (
                    await session.execute(
                        select(BatchLine.id).where(
                            BatchLine.state == "sending", BatchLine.text == text
                        )
                    )
                ).scalar_one()
                intent = (
                    await session.execute(
                        select(SendLog).where(SendLog.line_id == line_id)
                    )
                ).scalar_one_or_none()
            self.intent_committed.append(
                intent is not None and intent.message_id is None
            )
            return await super().send(text)

    fake = IntentCheckingGateway()
    monkeypatch.setattr(send_worker, "gateway", fake)

    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])

    assert await send_worker.step() is True

    assert fake.intent_committed == [True]
    rows = await _send_log_rows(batch_id)
    assert len(rows) == 1
    assert rows[0].message_id == 1  # filled in AFTER delivery
    # tenant/batch denormalized from the line — attribution needs no join.
    assert rows[0].tenant_id == user.tenant_id
    assert rows[0].batch_id == batch_id


@pytest.mark.asyncio(loop_scope="session")
async def test_record_phase_retries_until_db_returns_no_double_send(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """2-2 deferred #5: the record transaction failing AFTER a successful send
    must retry until the DB takes it — the line lands 'sent' with its
    message_id, delivered exactly ONCE (no zombie 'sending', no double send on
    the next restart)."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    monkeypatch.setattr(send_worker, "_ERROR_RETRY_SECONDS", 0.0)

    real_factory = async_session_factory
    fail = {"remaining": 2}

    def flaky_factory():  # type: ignore[no-untyped-def]
        # Only the post-send record phase fails (the send already happened).
        if fake_gateway.sent and fail["remaining"] > 0:
            fail["remaining"] -= 1
            raise RuntimeError("db down")
        return real_factory()

    monkeypatch.setattr(send_worker, "async_session_factory", flaky_factory)

    assert await send_worker.step() is True

    assert len(fake_gateway.sent) == 1  # exactly one delivery
    assert fail["remaining"] == 0  # both injected failures were consumed
    assert "event=db_unreachable" in caplog.text
    lines = await _lines_of(batch_id)
    assert [line.state for line in lines] == ["sent"]
    rows = await _send_log_rows(batch_id)
    assert rows[0].message_id == 1
    assert await _batch_state(batch_id) == "completed"


# --- Retry cap = 3 + 'failed' (AC 3 + 4) ---------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_line_fails_at_cap_with_event_and_queue_continues(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    events: list[tuple],
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "uno\ndos", gate["id"])
    monkeypatch.setattr(send_worker, "_ERROR_RETRY_SECONDS", 0.0)
    fake_gateway.errors = [RuntimeError("boom")] * 3

    assert await send_worker.step() is True  # 3 real attempts → paced anyway

    value = gate["value"]
    lines = await _lines_of(batch_id)
    assert lines[0].state == "failed"
    assert lines[0].fail_code == "runtime_error"  # snake_case of the class

    # Per-attempt error events kept (never silently dropped) …
    assert len([e for _, e, _ in events if e == "error"]) == 3
    # … plus the terminal tenant-scoped batch.line_failed.
    failed_events = [
        (tenant, data) for tenant, e, data in events if e == "batch.line_failed"
    ]
    assert failed_events == [
        (
            user.tenant_id,
            {
                "batch_id": batch_id,
                "position": 0,
                "text": f"{value} uno",
                "code": "runtime_error",
            },
        )
    ]
    progress = [data for _, e, data in events if e == "batch.progress"][-1]
    assert progress["failed"] == 1
    assert progress["total"] == 2  # the lote's size never shrinks

    # The queue CONTINUES: the next step sends the second line.
    assert await send_worker.step() is True
    assert fake_gateway.sent == [f"{value} dos"]
    assert await _batch_state(batch_id) == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_batch_whose_only_line_fails_still_completes(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    events: list[tuple],
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "solo", gate["id"])
    monkeypatch.setattr(send_worker, "_ERROR_RETRY_SECONDS", 0.0)
    fake_gateway.errors = [RuntimeError("boom")] * 3

    assert await send_worker.step() is True

    assert await _batch_state(batch_id) == "completed"  # drained WITH a failed
    idle_states = [
        data for _, e, data in events if e == "batch.state" and data["state"] == "idle"
    ]
    assert len(idle_states) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_floodwait_does_not_count_toward_the_cap(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FloodWait is account pacing, not a bad line: 1 FloodWait + 2 generic
    errors stays under the cap and the line still goes out."""
    http, _ = client_user
    batch_id = await _post_batch(http, "solo", gate["id"])
    monkeypatch.setattr(send_worker, "_ERROR_RETRY_SECONDS", 0.0)
    fake_gateway.errors = [
        FloodWaitError(request=None, capture=0),
        RuntimeError("x"),
        RuntimeError("y"),
    ]

    assert await send_worker.step() is True

    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"
    assert len(fake_gateway.sent) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_two_errors_then_success_is_sent(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "solo", gate["id"])
    monkeypatch.setattr(send_worker, "_ERROR_RETRY_SECONDS", 0.0)
    fake_gateway.errors = [RuntimeError("x"), RuntimeError("y")]

    assert await send_worker.step() is True

    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"
    assert lines[0].fail_code is None


# --- Fail-stop without DB (AC 5) -----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_db_down_before_claim_sends_nothing(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order of operations IS the fail-stop: with the DB down the claim raises
    BEFORE gateway.send — zero sends without attribution (run_worker's
    log+sleep+retry is the existing net)."""
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])

    def broken_factory():  # type: ignore[no-untyped-def]
        raise RuntimeError("db down")

    monkeypatch.setattr(send_worker, "async_session_factory", broken_factory)

    with pytest.raises(RuntimeError):
        await send_worker.step()
    assert fake_gateway.sent == []


# --- Boot reconciliation (AC 6) --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_reconciliation_confirms_matching_outgoing(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    line_id = await _claim_with_intent(batch_id)  # crash mid-send, line went out
    fake_gateway.outgoing = [(0, 99, f"{gate['value']} uno")]

    await send_worker._boot_recovery()

    lines = await _lines_of(batch_id)
    assert [line.state for line in lines] == ["sent"]
    rows = await _send_log_rows(batch_id)
    assert (rows[0].line_id, rows[0].message_id) == (line_id, 99)
    assert await _batch_state(batch_id) == "completed"  # finalized like a step


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_reconciliation_requeues_without_match(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _claim_with_intent(batch_id)  # crash mid-send, line never went out
    fake_gateway.outgoing = [(0, 99, "otra cosa")]

    await send_worker._boot_recovery()

    lines = await _lines_of(batch_id)
    assert [line.state for line in lines] == ["queued"]  # draining resumes
    rows = await _send_log_rows(batch_id)
    assert rows[0].message_id is None  # the intent row stays, reused on re-claim
    assert await _batch_state(batch_id) == "sending"


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_reconciliation_gateway_down_requeues_with_warning(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Recorded fallback: availability over the rare double-send when Telegram
    itself is down (nothing would send anyway until it returns)."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _claim_with_intent(batch_id)
    fake_gateway.authorized = False  # gateway.ready → False

    await send_worker._boot_recovery()

    lines = await _lines_of(batch_id)
    assert [line.state for line in lines] == ["queued"]
    assert "event=reconcile_unverified" in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_reconciliation_ignores_already_attributed_message_ids(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """An old outgoing message with IDENTICAL text whose id is already in
    send_log must not confirm a NEW line (used_message_pairs filter)."""
    http, _ = client_user
    # Two lines keep the batch LIVE after the first send (a drained batch
    # completes and the append below would start a fresh one).
    batch_id = await _post_batch(http, "uno\ndos", gate["id"])
    assert await send_worker.step() is True  # "uno" sent → send_log id 1

    # Legacy append semantics: an already-SENT text may be re-queued.
    res = await http.post(
        "/api/batches", json={"text": "uno", "gate_id": gate["id"]}
    )
    assert res.status_code == 201, res.text
    assert (res.json()["id"], res.json()["added"]) == (batch_id, 1)
    # Crash mid-send of the re-queued copy (position 2).
    await _claim_with_intent(batch_id, position=2)

    # The only candidate is line 0's already-attributed message.
    fake_gateway.outgoing = [(0, 1, f"{gate['value']} uno")]
    await send_worker._boot_recovery()

    lines = await _lines_of(batch_id)
    # NOT confirmed — the identical-text candidate was already attributed.
    assert [line.state for line in lines] == ["sent", "queued", "queued"]
    rows = await _send_log_rows(batch_id)
    assert [row.message_id for row in rows] == [1, None]


# --- Plan expiry mid-batch (AC 7) -------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_expiry_cancels_queued_lines_and_keeps_sent(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "uno\ndos\ntres", gate["id"])
    assert await send_worker.step() is True  # "uno" goes out while the plan lives

    await _expire_plan(user.id)
    sent_before = list(fake_gateway.sent)

    assert await send_worker.step() is False  # no claim, no send — next loop rotates

    assert fake_gateway.sent == sent_before  # zero sends for the expired tenant
    assert await _batch_state(batch_id) == "cancelled"
    lines = await _lines_of(batch_id)
    # Cancel ≠ stop: the system MARKS the queued rows (honest history), and
    # what was already sent stays attributed for Story 3.1.
    assert [line.state for line in lines] == ["sent", "cancelled", "cancelled"]
    rows = await _send_log_rows(batch_id)
    assert len(rows) == 1 and rows[0].message_id == 1  # untouched
    idle = [
        (tenant, data)
        for tenant, e, data in events
        if e == "batch.state" and data["state"] == "idle"
    ]
    assert (user.tenant_id, batch_id) in [(t, d["batch_id"]) for t, d in idle]
    # 'cancelled' is terminal and NOT live: a renewed plan starts fresh.
    async with async_session_factory() as session:
        assert await batches_repo.get_live_batch(session, user.tenant_id) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_batch_is_never_cancelled_by_expiry(
    ctx: dict[str, object],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """The owner's tenant has no 'client' user ⇒ tenant_plan_expired is False
    ⇒ owner batches always pass the claim-time check."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    batch_id = await _post_batch(owner_client, "uno", gate["id"])
    try:
        assert await send_worker.step() is True
        assert fake_gateway.sent == [f"{gate['value']} uno"]
        assert await _batch_state(batch_id) == "completed"
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Batch).where(Batch.id == batch_id))
            await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_staff_batch_not_cancelled_when_tenant_has_expired_client(
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Regression: a SHARED "house" tenant holds staff (admin/owner) AND a
    client whose plan lapsed. ``tenant_plan_expired`` is tenant-WIDE, so the
    expired client used to poison the admin's own batch (cancelled at claim
    time) — owner/admin send must be exempt (priority >= 1), matching the auth
    gate's ``is_plan_expired``. Without the fix the batch lands 'cancelled' and
    nothing is sent."""
    admin = await seed_user("admin", email_prefix="test-shared")
    expired_client = await seed_user(
        "client",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        email_prefix="test-shared",
    )
    # Co-locate the expired client in the admin's tenant (the seed/house tenant
    # shape: owner + admin + client all on tenant_id 1 in production).
    async with async_session_factory() as session:
        row = await session.get(User, expired_client.id)
        assert row is not None
        row.tenant_id = admin.tenant_id
        await session.commit()

    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http, admin.email)
        batch_id = await _post_batch(http, "uno", gate["id"])  # priority 1 (admin)
        assert await send_worker.step() is True
        assert fake_gateway.sent == [f"{gate['value']} uno"]
        assert await _batch_state(batch_id) == "completed"
    finally:
        await http.aclose()
        async with async_session_factory() as session:
            await session.execute(
                delete(Batch).where(Batch.tenant_id == admin.tenant_id)
            )
            await session.commit()
        await cleanup_users({admin.email, expired_client.email})


# --- Global FloodWait window (2-4 deferred #1) -------------------------------------


def test_flood_window_unit_max_decay_and_reset() -> None:
    clock = FakeClock()
    sched = Scheduler(now=clock)
    assert sched.flood_remaining() == 0.0

    sched.note_flood_wait(5.0)
    assert sched.flood_remaining() == pytest.approx(5.0)
    clock.advance(2.0)
    assert sched.flood_remaining() == pytest.approx(3.0)
    # A shorter overlapping FloodWait never SHRINKS the window (max wins).
    sched.note_flood_wait(1.0)
    assert sched.flood_remaining() == pytest.approx(3.0)
    clock.advance(4.0)
    assert sched.flood_remaining() == 0.0

    sched.note_flood_wait(5.0)
    sched.reset()  # restart equivalence — also keeps the suite uncontaminated
    assert sched.flood_remaining() == 0.0


@pytest.mark.asyncio(loop_scope="session")
async def test_flood_window_survives_release(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """The 2-4 deferred scenario: tenant A's pause cuts ITS FloodWait wait
    (release — lands instantly), but the GLOBAL window stays open so the next
    step cannot send another tenant's line into it."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    fake_gateway.errors = [FloodWaitError(request=None, capture=7)]

    task = asyncio.create_task(send_worker.step())
    deadline = time.monotonic() + 2.0
    # Polling is deliberate: the window opens inside the worker's own task and
    # exposes no event to await (test-only observation point).
    while (  # noqa: ASYNC110
        scheduler.flood_remaining() == 0.0 and time.monotonic() < deadline
    ):
        await asyncio.sleep(0.01)
    assert scheduler.flood_remaining() > 0.0  # window opened by the FloodWait

    # A's own pause still lands instantly (release) …
    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204
    assert await asyncio.wait_for(task, timeout=2.0) is False
    assert fake_gateway.sent == []
    # … but the window survives the release — nobody may claim into it.
    assert scheduler.flood_remaining() > 5.0


@pytest.mark.asyncio(loop_scope="session")
async def test_flood_window_gates_the_next_claim(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Top-of-step gate: an open window delays ANY claim/send until it
    elapses — including the window-owning tenant's (no exemption)."""
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    scheduler.note_flood_wait(0.4)

    start = time.monotonic()
    assert await asyncio.wait_for(send_worker.step(), timeout=2.0) is True
    assert time.monotonic() - start >= 0.4  # slept the window out first
    assert len(fake_gateway.sent) == 1


# --- Structured logs (AC 8) ---------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_structured_logs_line_sent_and_flood_wait(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.core.send_worker")
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    fake_gateway.errors = [FloodWaitError(request=None, capture=0)]

    assert await send_worker.step() is True

    assert "event=flood_wait" in caplog.text
    assert "event=line_sent" in caplog.text
    assert f"tenant={user.tenant_id}" in caplog.text
    assert "tenant_total=" in caplog.text  # the per-tenant send count


@pytest.mark.asyncio(loop_scope="session")
async def test_structured_log_line_failed(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    monkeypatch.setattr(send_worker, "_ERROR_RETRY_SECONDS", 0.0)
    fake_gateway.errors = [RuntimeError("boom")] * 3

    assert await send_worker.step() is True

    assert "event=line_failed" in caplog.text
    assert "code=runtime_error" in caplog.text
