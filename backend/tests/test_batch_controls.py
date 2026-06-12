"""Integration tests for batch controls (Story 2.3):
POST /api/batches/{id}/pause|resume|stop, the pause/stop-aware worker, the
one-live-batch partial unique index and the snapshot passthrough.

Same idiom as the rest of the suite: real ASGI app against the dev Postgres,
self-seeding, self-cleaning (batches/lines die with their tenant via FK
CASCADE in ``cleanup_users``). No real Telegram — the worker paths run
against ``conftest.FakeGateway`` and events are verified by monkeypatching
the broadcaster with a recorder list (2.2 lesson: no socket plumbing).

Run (from backend/, venv active):  pytest tests/test_batch_controls.py
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from app.core import send_worker
from app.core.broadcaster import broadcaster
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine, User
from app.db.repos import batches as batches_repo
from app.main import app
from app.services import batches as batches_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

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


async def _create_batch(http: AsyncClient, gate: dict, text: str = "uno\ndos\ntres") -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate["id"]})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _batch_state(batch_id: int) -> str | None:
    async with async_session_factory() as session:
        return await batches_repo.get_batch_state(session, batch_id)


async def _lines_of(batch_id: int) -> list[BatchLine]:
    async with async_session_factory() as session:
        stmt = (
            select(BatchLine)
            .where(BatchLine.batch_id == batch_id)
            .order_by(BatchLine.position)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _claim_first_line(batch_id: int) -> int:
    """Manually claim the batch's first queued line (simulates the worker)."""
    async with async_session_factory() as session:
        lines = await _lines_of(batch_id)
        line = await session.get(BatchLine, lines[0].id)
        assert line is not None
        await batches_repo.mark_sending(session, line)
        await session.commit()
        return line.id


def _states(events: list[tuple]) -> list[dict]:
    return [data for _, name, data in events if name == "batch.state"]


# --- pause -------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_pause_happy_path(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate)

    res = await http.post(f"/api/batches/{batch_id}/pause")
    assert res.status_code == 204

    assert await _batch_state(batch_id) == "paused"
    # The worker never claims lines of a paused batch.
    assert await send_worker.step() is False
    assert fake_gateway.sent == []


@pytest.mark.asyncio(loop_scope="session")
async def test_resume_happy_path(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate)
    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204

    res = await http.post(f"/api/batches/{batch_id}/resume")
    assert res.status_code == 204

    assert await _batch_state(batch_id) == "sending"
    # Draining resumes: the next step claims and sends the first line.
    assert await send_worker.step() is True
    assert fake_gateway.sent == [f"{gate['value']} uno"]


# --- stop --------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_stop_without_inflight_line_clears_queue(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate)

    res = await http.post(f"/api/batches/{batch_id}/stop")
    assert res.status_code == 204

    assert await _batch_state(batch_id) == "stopped"
    assert await _lines_of(batch_id) == []  # remaining queue cleared (AC 4)

    # The tenant is free again: a new POST creates a FRESH batch.
    second = await http.post(
        "/api/batches", json={"text": "otro", "gate_id": gate["id"]}
    )
    assert second.status_code == 201, second.text
    assert second.json()["appended"] is False
    assert second.json()["id"] != batch_id
    assert second.json()["state"] == "sending"


@pytest.mark.asyncio(loop_scope="session")
async def test_stop_with_inflight_line_stopping_then_worker_aborts(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, user = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")
    line_id = await _claim_first_line(batch_id)

    res = await http.post(f"/api/batches/{batch_id}/stop")
    assert res.status_code == 204
    assert await _batch_state(batch_id) == "stopping"

    # The worker's per-iteration re-check abandons the claimed line…
    result = await send_worker._send_with_retries(
        user.tenant_id, batch_id, f"{gate['value']} uno"
    )
    assert result == "abort"
    assert fake_gateway.sent == []

    # …and the abort path discards it and finalizes the batch.
    await send_worker._abort_line(user.tenant_id, batch_id, line_id)
    assert await _lines_of(batch_id) == []
    assert await _batch_state(batch_id) == "stopped"


@pytest.mark.asyncio(loop_scope="session")
async def test_pause_releases_claimed_line_back_to_queue(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, user = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")
    line_id = await _claim_first_line(batch_id)
    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204

    result = await send_worker._send_with_retries(
        user.tenant_id, batch_id, f"{gate['value']} uno"
    )
    assert result == "release"
    assert fake_gateway.sent == []  # nothing went out

    await send_worker._release_line(user.tenant_id, batch_id, line_id)
    lines = await _lines_of(batch_id)
    assert lines[0].id == line_id
    assert lines[0].state == "queued"  # back in the queue, intact


# --- idempotency + invalid transitions ----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_repeated_controls_are_noops_without_duplicate_events(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    events: list[tuple],
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate)

    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204
    events.clear()
    # pause on 'paused' → 204 no-op, no event (two-tabs idempotency).
    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204
    assert _states(events) == []

    assert (await http.post(f"/api/batches/{batch_id}/resume")).status_code == 204
    events.clear()
    # resume on 'sending' → 204 no-op, no event.
    assert (await http.post(f"/api/batches/{batch_id}/resume")).status_code == 204
    assert _states(events) == []

    # stop on 'stopping' → 204 no-op, no event.
    await _claim_first_line(batch_id)
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert await _batch_state(batch_id) == "stopping"
    events.clear()
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert _states(events) == []
    assert await _batch_state(batch_id) == "stopping"


@pytest.mark.asyncio(loop_scope="session")
async def test_controls_on_terminal_batch_are_409_not_live(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user

    # completed (drained by the worker)…
    completed_id = await _create_batch(http, gate, text="solo")
    assert await send_worker.step() is True
    assert await _batch_state(completed_id) == "completed"
    for action in ("pause", "resume", "stop"):
        res = await http.post(f"/api/batches/{completed_id}/{action}")
        assert res.status_code == 409
        assert res.json()["code"] == "batch_not_live"

    # …and stopped behave the same.
    stopped_id = await _create_batch(http, gate, text="otro")
    assert (await http.post(f"/api/batches/{stopped_id}/stop")).status_code == 204
    for action in ("pause", "resume", "stop"):
        res = await http.post(f"/api/batches/{stopped_id}/{action}")
        assert res.status_code == 409
        assert res.json()["code"] == "batch_not_live"


@pytest.mark.asyncio(loop_scope="session")
async def test_pause_resume_during_stopping_are_409_stopping(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")
    await _claim_first_line(batch_id)
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert await _batch_state(batch_id) == "stopping"

    for action in ("pause", "resume"):
        res = await http.post(f"/api/batches/{batch_id}/{action}")
        assert res.status_code == 409
        assert res.json()["code"] == "batch_stopping"


# --- scoping ------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_controls_scoping_anonymous_other_tenant_and_huge_id(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http_a, _ = client_user
    batch_id = await _create_batch(http_a, gate)

    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        assert (
            await anon.post(f"/api/batches/{batch_id}/pause")
        ).status_code == 401
    finally:
        await anon.aclose()

    # Tenant B acting on tenant A's batch: 404, existence never leaked (AC 1).
    user_b = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http_b, user_b.email)
        for action in ("pause", "resume", "stop"):
            res = await http_b.post(f"/api/batches/{batch_id}/{action}")
            assert res.status_code == 404
            assert res.json()["code"] == "batch_not_found"
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})

    # Out-of-int4 id → 404, not a 500 from asyncpg (2.1 review lesson).
    res = await http_a.post(f"/api/batches/{2**31}/pause")
    assert res.status_code == 404
    assert res.json()["code"] == "batch_not_found"

    # The batch itself was never touched.
    assert await _batch_state(batch_id) == "sending"


# --- append vs paused/stopping --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_append_to_paused_batch_keeps_it_paused(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate, text="uno")
    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204

    res = await http.post(
        "/api/batches", json={"text": "dos", "gate_id": gate["id"]}
    )
    assert res.status_code == 201, res.text
    assert res.json()["appended"] is True
    assert res.json()["id"] == batch_id
    assert await _batch_state(batch_id) == "paused"  # append never flips state


@pytest.mark.asyncio(loop_scope="session")
async def test_append_during_stopping_is_409(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")
    await _claim_first_line(batch_id)
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert await _batch_state(batch_id) == "stopping"

    res = await http.post(
        "/api/batches", json={"text": "tres", "gate_id": gate["id"]}
    )
    assert res.status_code == 409
    assert res.json()["code"] == "batch_stopping"


# --- events carry full context (Task 5) ----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_batch_state_events_carry_batch_and_gate_context(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    events: list[tuple],
) -> None:
    http, _ = client_user

    batch_id = await _create_batch(http, gate)
    # Story 3.2: every batch.state emission carries the capture-session
    # binding (state_data is the single source for all of them).
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None
        session_id = batch.capture_session_id
    assert session_id is not None
    assert _states(events) == [
        {
            "batch_id": batch_id,
            "state": "sending",
            "gate_name": gate["name"],
            "gate_value": gate["value"],
            "session_id": session_id,
            # Story 4.2: admission position — None outside 'waiting'.
            "queue_position": None,
        }
    ]

    events.clear()
    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204
    assert _states(events) == [
        {
            "batch_id": batch_id,
            "state": "paused",
            "gate_name": gate["name"],
            "gate_value": gate["value"],
            "session_id": session_id,
            "queue_position": None,
        }
    ]

    # stop with no in-flight line goes straight to 'stopped' → terminal idle.
    events.clear()
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert _states(events) == [
        {
            "batch_id": batch_id,
            "state": "idle",
            "gate_name": gate["name"],
            "gate_value": gate["value"],
            "session_id": session_id,
            "queue_position": None,
        }
    ]


# --- one live batch per tenant (DB invariant) -----------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_partial_unique_index_rejects_second_live_batch(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, user = client_user
    batch_id = await _create_batch(http, gate)

    with pytest.raises(IntegrityError):
        async with async_session_factory() as session:
            await batches_repo.create_batch(
                session,
                tenant_id=user.tenant_id,
                gate_value=gate["value"],
                gate_name=gate["name"],
                priority=0,
            )
            await session.commit()

    # The index also covers 'paused' (still live).
    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204
    with pytest.raises(IntegrityError):
        async with async_session_factory() as session:
            await batches_repo.create_batch(
                session,
                tenant_id=user.tenant_id,
                gate_value=gate["value"],
                gate_name=gate["name"],
                priority=0,
            )
            await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_get_live_batch_finds_paused_and_stopping(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, user = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")

    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204
    async with async_session_factory() as session:
        live = await batches_repo.get_live_batch(session, user.tenant_id)
        assert live is not None and live.id == batch_id

    await _claim_first_line(batch_id)
    assert (await http.post(f"/api/batches/{batch_id}/resume")).status_code == 204
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert await _batch_state(batch_id) == "stopping"
    async with async_session_factory() as session:
        live = await batches_repo.get_live_batch(session, user.tenant_id)
        assert live is not None and live.id == batch_id


@pytest.mark.asyncio(loop_scope="session")
async def test_create_race_integrity_error_converts_to_append(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two tabs racing the live check: the loser's INSERT hits the partial
    unique index and the handler converts it into an append — never a 500."""
    http, _ = client_user
    batch_id = await _create_batch(http, gate, text="uno")

    real = batches_repo.get_live_batch
    calls = {"n": 0}

    async def racy(
        session: object, tenant_id: int, *, for_update: bool = False
    ) -> Batch | None:
        calls["n"] += 1
        if calls["n"] == 1:  # simulate the TOCTOU miss of the second tab
            return None
        return await real(session, tenant_id, for_update=for_update)  # type: ignore[arg-type]

    monkeypatch.setattr(batches_repo, "get_live_batch", racy)

    res = await http.post("/api/batches", json={"text": "dos", "gate_id": gate["id"]})
    assert res.status_code == 201, res.text
    assert res.json()["appended"] is True
    assert res.json()["id"] == batch_id
    assert calls["n"] >= 2


# --- snapshot passthrough -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_passes_paused_and_stopping_through(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, user = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")

    assert (await http.post(f"/api/batches/{batch_id}/pause")).status_code == 204
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["state"] == "paused"
    assert snap["batch_id"] == batch_id
    assert snap["gate_value"] == gate["value"]
    assert (snap["sent"], snap["queued"], snap["total"]) == (0, 2, 2)
    assert snap["cc_new"] == 0  # real since 3.1 — this test captures nothing

    await _claim_first_line(batch_id)
    assert (await http.post(f"/api/batches/{batch_id}/resume")).status_code == 204
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["state"] == "stopping"


# --- worker: stop lands while the send is in flight (line DID go out) ------------


@pytest.mark.asyncio(loop_scope="session")
async def test_stop_mid_send_records_sent_and_finalizes_stopped(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")

    class BlockingGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.unblock = asyncio.Event()

        async def send(self, text: str) -> int:
            self.entered.set()
            await self.unblock.wait()
            return await super().send(text)

    blocking = BlockingGateway()
    monkeypatch.setattr(send_worker, "gateway", blocking)

    task = asyncio.create_task(send_worker.step())
    await asyncio.wait_for(blocking.entered.wait(), timeout=2.0)

    # Stop while gateway.send is in flight → 'stopping' (line is claimed).
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert await _batch_state(batch_id) == "stopping"

    blocking.unblock.set()
    assert await asyncio.wait_for(task, timeout=2.0) is True

    # The line DID go out → recorded 'sent' honestly; batch finalized
    # 'stopped' (NOT 'completed' — drained ≠ detenido).
    assert blocking.sent == [f"{gate['value']} uno"]
    lines = await _lines_of(batch_id)
    assert [line.state for line in lines] == ["sent"]
    assert await _batch_state(batch_id) == "stopped"


# --- boot recovery: a 'stopping' orphaned by a restart must finalize -------------


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_recovery_finalizes_orphaned_stopping_batch(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    """Restart while a stop is in flight: the batch sits in 'stopping' with a
    claimed line nobody holds in-process. Without boot-recovery finalization
    nothing ever touches it again ('stopping' is live ⇒ every control 409s,
    the unique index blocks new batches) — the tenant is bricked (2.3 review
    HIGH). Boot recovery must land it 'stopped', drop the abandoned line and
    free the tenant."""
    http, _ = client_user
    batch_id = await _create_batch(http, gate, text="uno\ndos")
    await _claim_first_line(batch_id)
    assert (await http.post(f"/api/batches/{batch_id}/stop")).status_code == 204
    assert await _batch_state(batch_id) == "stopping"

    # Simulated restart: the worker that claimed the line is gone; only the
    # boot-recovery path runs.
    await send_worker._boot_recovery()

    assert await _batch_state(batch_id) == "stopped"
    assert await _lines_of(batch_id) == []

    # The tenant is unblocked: a fresh batch starts (no 409, no append).
    res = await http.post(
        "/api/batches", json={"text": "otra", "gate_id": gate["id"]}
    )
    assert res.status_code == 201, res.text
    assert res.json()["appended"] is False
    assert res.json()["id"] != batch_id
    assert res.json()["state"] == "sending"


# --- wake() cuts sleeps instantly (AC 3) -----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_sleep_cancelable_returns_early_on_wake() -> None:
    async def waker() -> None:
        await asyncio.sleep(0.05)
        send_worker.wake()

    task = asyncio.create_task(waker())
    # A 10s sleep must return well under 1s once wake() fires.
    await asyncio.wait_for(send_worker.sleep_cancelable(10.0), timeout=1.0)
    await task
