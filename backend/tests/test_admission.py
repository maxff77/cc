"""Tests for Story 4.2 admission control: the owner-configurable cap, the
durable FIFO waiting queue, queue positions (POST response + events +
snapshot), automatic promotion when a slot frees, and the disabled-cap
fallback to pure Epic 2 semantics.

Conftest idiom: real ASGI app + dev Postgres, self-seeding/self-cleaning,
``FakeGateway``, the worker driven via ``step()`` directly, events verified
by monkeypatching the broadcaster with a recording list.

The cap rows in ``system_settings`` are wiped around every test by the local
autouse fixture — the knob is global state shared across tenants.

Run (from backend/, venv active):  pytest tests/test_admission.py
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.core import send_worker
from app.core.broadcaster import broadcaster
from app.db.base import async_session_factory
from app.db.models import SystemSetting, User
from app.db.repos import batches as batches_repo
from app.main import app
from app.services import admission as admission_service
from app.services import batches as batches_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

# --- Local fixtures -----------------------------------------------------------


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_cap() -> AsyncIterator[None]:
    """Wipe the admission cap around every test (global knob, shared DB)."""
    async with async_session_factory() as session:
        await session.execute(
            delete(SystemSetting).where(SystemSetting.key == admission_service.CAP_KEY)
        )
        await session.commit()
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SystemSetting).where(SystemSetting.key == admission_service.CAP_KEY)
        )
        await session.commit()


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str, dict]]:
    """Record tenant-scoped emits instead of touching sockets (2.2 lesson)."""
    recorded: list[tuple[int, str, dict]] = []

    async def record(tenant_id: int, event: str, data: dict) -> None:
        recorded.append((tenant_id, event, data))

    monkeypatch.setattr(broadcaster, "emit", record)
    return recorded


def _states_for(
    events: list[tuple[int, str, dict]], tenant_id: int
) -> list[dict]:
    return [
        data
        for evt_tenant, name, data in events
        if name == "batch.state" and evt_tenant == tenant_id
    ]


async def _set_cap(owner_client: AsyncClient, cap: int) -> None:
    res = await owner_client.put(
        "/api/admin/admission", json={"max_active_senders": cap}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"max_active_senders": cap}


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> dict:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    body: dict = res.json()
    return body


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


# --- Owner knob: GET/PUT /api/admin/admission -----------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_admission_defaults_to_disabled(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.get("/api/admin/admission")
    assert res.status_code == 200
    assert res.json() == {"max_active_senders": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_admission_put_persists_and_zero_disables(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 7)
    res = await owner_client.get("/api/admin/admission")
    assert res.json() == {"max_active_senders": 7}

    # 0 persists too (the row stays — the admission lock exists from the
    # first touch of the knob on).
    await _set_cap(owner_client, 0)
    res = await owner_client.get("/api/admin/admission")
    assert res.json() == {"max_active_senders": 0}


@pytest.mark.asyncio(loop_scope="session")
async def test_admission_put_rejects_out_of_bounds(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for bad in (-1, 1001):
        res = await owner_client.put(
            "/api/admin/admission", json={"max_active_senders": bad}
        )
        assert res.status_code == 400
        assert res.json()["code"] == "invalid_admission_cap"


@pytest.mark.asyncio(loop_scope="session")
async def test_admission_endpoints_are_owner_only(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    client_http, _ = client_user
    for http in (admin_client, client_http):
        assert (await http.get("/api/admin/admission")).status_code == 403
        res = await http.put(
            "/api/admin/admission", json={"max_active_senders": 1}
        )
        assert res.status_code == 403


# --- AC 1: over-cap batches queue instead of degrading the active senders -------


@pytest.mark.asyncio(loop_scope="session")
async def test_batch_over_cap_enters_fifo_queue_with_position(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, user_a = client_user
    http_b, user_b = await _second_client()
    http_c, user_c = await _second_client()
    try:
        first = await _post_batch(http_a, "a1\na2", gate["id"])
        assert first["state"] == "sending"
        assert first["queue_position"] is None

        second = await _post_batch(http_b, "b1", gate["id"])
        assert second["state"] == "waiting"
        assert second["queue_position"] == 1

        third = await _post_batch(http_c, "c1", gate["id"])
        assert third["state"] == "waiting"
        assert third["queue_position"] == 2

        # Waiting batches do NOT weigh on the adaptive formula's n — the
        # active sender keeps its n=1 cadence/ETA (that IS the point, AC 1).
        async with async_session_factory() as session:
            assert await batches_repo.count_active_senders(session) == 1
            snap = await batches_service.snapshot(session, user_a.tenant_id)
        assert snap["eta_seconds"] == 20.0  # 2 × 1 × interval(1)=10.0
    finally:
        await http_b.aclose()
        await http_c.aclose()
        await cleanup_users({user_b.email, user_c.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_waiting_snapshot_and_creation_event_carry_position(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    events: list[tuple[int, str, dict]],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        await _post_batch(http_a, "a1", gate["id"])
        events.clear()
        waiting = await _post_batch(http_b, "b1", gate["id"])

        # The creation event reports the position (AC 2)…
        states = _states_for(events, user_b.tenant_id)
        assert len(states) == 1
        assert states[0]["state"] == "waiting"
        assert states[0]["queue_position"] == 1
        assert states[0]["batch_id"] == waiting["id"]

        # …and a tab connecting mid-wait rebuilds it from the snapshot alone.
        async with async_session_factory() as session:
            snap = await batches_service.snapshot(session, user_b.tenant_id)
        assert snap["state"] == "waiting"
        assert snap["queue_position"] == 1
        assert snap["batch_id"] == waiting["id"]
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


# --- AC 3: a freed slot starts the next waiting batch automatically -------------


@pytest.mark.asyncio(loop_scope="session")
async def test_completed_batch_frees_slot_and_promotes_fifo(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple[int, str, dict]],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    http_c, user_c = await _second_client()
    try:
        await _post_batch(http_a, "a-única", gate["id"])
        batch_b = await _post_batch(http_b, "b-única", gate["id"])
        batch_c = await _post_batch(http_c, "c-única", gate["id"])
        assert (batch_b["queue_position"], batch_c["queue_position"]) == (1, 2)

        # Drain A — its completion frees the slot.
        assert await send_worker.step() is True
        assert fake_gateway.sent == [f"{gate['value']} a-única"]

        events.clear()
        # The NEXT step's sweep promotes B (oldest first, FIFO) and sends it.
        assert await send_worker.step() is True
        assert fake_gateway.sent[-1] == f"{gate['value']} b-única"
        assert await _batch_state(batch_b["id"]) in ("sending", "completed")

        # Events: B got 'sending', C got re-numbered to position 1 (AC 2/3).
        b_states = _states_for(events, user_b.tenant_id)
        assert any(s["state"] == "sending" for s in b_states)
        c_states = _states_for(events, user_c.tenant_id)
        assert {"state": "waiting", "queue_position": 1} == {
            "state": c_states[0]["state"],
            "queue_position": c_states[0]["queue_position"],
        }
    finally:
        await http_b.aclose()
        await http_c.aclose()
        await cleanup_users({user_b.email, user_c.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_stopped_batch_frees_slot_and_promotes(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        batch_a = await _post_batch(http_a, "a1\na2\na3", gate["id"])
        batch_b = await _post_batch(http_b, "b1", gate["id"])
        assert batch_b["state"] == "waiting"

        # Direct stop (no line in flight) frees the slot immediately.
        res = await http_a.post(f"/api/batches/{batch_a['id']}/stop")
        assert res.status_code == 204

        assert await send_worker.step() is True  # sweep promotes B, sends b1
        assert fake_gateway.sent == [f"{gate['value']} b1"]
        assert await _batch_state(batch_b["id"]) == "completed"
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_never_serves_waiting_batch_lines(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        await _post_batch(http_a, "a1\na2", gate["id"])
        await _post_batch(http_b, "b1", gate["id"])

        # Both of A's lines go out before anything of B's — the waiting batch
        # is invisible to the scheduler's rotation while the cap is full.
        assert await send_worker.step() is True
        assert await send_worker.step() is True
        assert fake_gateway.sent == [
            f"{gate['value']} a1",
            f"{gate['value']} a2",
        ]
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


# --- AC 4: disabled cap = pure Epic 2 semantics ----------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_disabled_cap_admits_everyone_directly(
    client_user: tuple[AsyncClient, User],
    gate: dict,
) -> None:
    # No cap row at all (the autouse fixture wiped it): Epic 2 behavior.
    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        first = await _post_batch(http_a, "a1", gate["id"])
        second = await _post_batch(http_b, "b1", gate["id"])
        assert first["state"] == "sending"
        assert second["state"] == "sending"
        assert second["queue_position"] is None
        async with async_session_factory() as session:
            assert await batches_repo.count_active_senders(session) == 2
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_disabling_cap_rescues_already_waiting_batches(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        await _post_batch(http_a, "a1\na2", gate["id"])
        batch_b = await _post_batch(http_b, "b1", gate["id"])
        assert batch_b["state"] == "waiting"

        # The owner turns the knob off — the sweep promotes EVERY waiter.
        await _set_cap(owner_client, 0)
        assert await send_worker.step() is True
        assert await _batch_state(batch_b["id"]) in ("sending", "completed")
        async with async_session_factory() as session:
            assert await batches_repo.waiting_batches(session) == []
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_raising_cap_promotes_only_freed_slots_fifo(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    http_c, user_c = await _second_client()
    try:
        await _post_batch(http_a, "a1\na2", gate["id"])
        batch_b = await _post_batch(http_b, "b1\nb2", gate["id"])
        batch_c = await _post_batch(http_c, "c1", gate["id"])

        await _set_cap(owner_client, 2)  # one extra slot → B only (FIFO)
        assert await send_worker.step() is True
        assert await _batch_state(batch_b["id"]) == "sending"
        assert await _batch_state(batch_c["id"]) == "waiting"
        async with async_session_factory() as session:
            assert await batches_repo.queue_position(session, batch_c["id"]) == 1
    finally:
        await http_b.aclose()
        await http_c.aclose()
        await cleanup_users({user_b.email, user_c.email})


# --- Controls + append on a waiting batch ----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_stop_leaves_queue_and_renumbers_those_behind(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    events: list[tuple[int, str, dict]],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    http_c, user_c = await _second_client()
    try:
        await _post_batch(http_a, "a1", gate["id"])
        batch_b = await _post_batch(http_b, "b1", gate["id"])
        batch_c = await _post_batch(http_c, "c1", gate["id"])
        assert (batch_b["queue_position"], batch_c["queue_position"]) == (1, 2)

        events.clear()
        res = await http_b.post(f"/api/batches/{batch_b['id']}/stop")
        assert res.status_code == 204
        assert await _batch_state(batch_b["id"]) == "stopped"

        # B got the terminal idle; C (behind the leaver) got position 1.
        b_states = _states_for(events, user_b.tenant_id)
        assert b_states[-1]["state"] == "idle"
        c_states = _states_for(events, user_c.tenant_id)
        assert len(c_states) == 1
        assert c_states[0]["state"] == "waiting"
        assert c_states[0]["queue_position"] == 1
    finally:
        await http_b.aclose()
        await http_c.aclose()
        await cleanup_users({user_b.email, user_c.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_pause_and_resume_on_waiting_batch_409(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        await _post_batch(http_a, "a1", gate["id"])
        batch_b = await _post_batch(http_b, "b1", gate["id"])

        for action in ("pause", "resume"):
            res = await http_b.post(f"/api/batches/{batch_b['id']}/{action}")
            assert res.status_code == 409, action
            assert res.json()["code"] == "batch_waiting"
        # Still waiting, still position 1 — nothing moved.
        assert await _batch_state(batch_b["id"]) == "waiting"
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_append_to_waiting_batch_queues_lines_and_reports_position(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        await _post_batch(http_a, "a1", gate["id"])
        batch_b = await _post_batch(http_b, "b1", gate["id"])

        appended = await _post_batch(http_b, "b2\nb3", gate["id"])
        assert appended["id"] == batch_b["id"]
        assert appended["appended"] is True
        assert appended["added"] == 2
        assert appended["state"] == "waiting"
        assert appended["queue_position"] == 1
        assert appended["queued"] == 3  # b1 + the two appended lines
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


# --- Paused batches keep their admission slot (recorded decision) ----------------


@pytest.mark.asyncio(loop_scope="session")
async def test_paused_batch_keeps_slot_no_promotion(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_cap(owner_client, 1)

    http_a, _ = client_user
    http_b, user_b = await _second_client()
    try:
        batch_a = await _post_batch(http_a, "a1\na2", gate["id"])
        batch_b = await _post_batch(http_b, "b1", gate["id"])

        res = await http_a.post(f"/api/batches/{batch_a['id']}/pause")
        assert res.status_code == 204

        # Paused A still occupies its slot: nothing is promoted, nothing is
        # served (resume must never have to re-queue behind a usurper).
        assert await send_worker.step() is False
        assert await _batch_state(batch_b["id"]) == "waiting"
        assert fake_gateway.sent == []
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})
