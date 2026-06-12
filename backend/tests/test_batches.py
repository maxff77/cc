"""Integration tests for batches (Story 2.2): POST /api/batches, the send
worker and the WS snapshot/handshake helpers.

Same shape as the rest of the suite: real ASGI app against the dev Postgres,
self-seeding, self-cleaning (batches/lines die with their tenant via FK
CASCADE in ``cleanup_users``; gates/categories are removed explicitly). No
real Telegram anywhere — the route only persists, and the worker tests run
against ``conftest.FakeGateway``.

Run (from backend/, venv active):  pytest tests/test_batches.py
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.config import settings
from app.core import send_worker
from app.core.telegram import gateway
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine, User
from app.db.repos import batches as batches_repo
from app.main import app
from app.services import batches as batches_service
from app.services.batches import apply_gate
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from telethon.errors import FloodWaitError

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

# The `gate`, `client_user`, `fake_gateway` and (autouse) `authorized_gateway`
# fixtures were promoted to conftest.py in Story 2.3 — shared with
# test_batch_controls.py.


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> object:
    return await http.post("/api/batches", json={"text": text, "gate_id": gate_id})


async def _lines_of(batch_id: int) -> list[BatchLine]:
    async with async_session_factory() as session:
        stmt = (
            select(BatchLine)
            .where(BatchLine.batch_id == batch_id)
            .order_by(BatchLine.position)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _batch_count(tenant_id: int) -> int:
    async with async_session_factory() as session:
        stmt = select(Batch.id).where(Batch.tenant_id == tenant_id)
        return len(list((await session.execute(stmt)).scalars().all()))


# --- apply_gate (exact port of legacy agregar_prefijo) ----------------------


def test_apply_gate_prefixes_strips_and_dedups() -> None:
    text = "  abc  \n\n.zo def\nabc\n   \nghi"
    assert apply_gate(text, ".zo") == [".zo abc", ".zo def", ".zo ghi"]


def test_apply_gate_empty_and_whitespace_only() -> None:
    assert apply_gate("", ".zo") == []
    assert apply_gate("   \n  \n\t", ".zo") == []


def test_apply_gate_no_double_prefix() -> None:
    # A line already carrying the gate (with the separating space) is verbatim.
    assert apply_gate(".zo 123", ".zo") == [".zo 123"]
    # Same content with and without the prefix collapses to one line.
    assert apply_gate(".zo 123\n123", ".zo") == [".zo 123"]


# --- POST /api/batches -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_anonymous_post_is_401(gate: dict) -> None:
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        res = await _post_batch(anon, "abc", gate["id"])
        assert res.status_code == 401
    finally:
        await anon.aclose()


@pytest.mark.asyncio(loop_scope="session")
async def test_expired_plan_client_is_403(gate: dict) -> None:
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http, user.email)
        async with async_session_factory() as session:
            row = await session.get(User, user.id)
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()
        res = await _post_batch(http, "abc", gate["id"])
        assert res.status_code == 403
        assert res.json()["code"] == "plan_expired"
    finally:
        await http.aclose()
        await cleanup_users({user.email})


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("text", ["", "   \n \n\t  "], ids=["empty", "whitespace"])
async def test_empty_paste_is_rejected_and_creates_nothing(
    client_user: tuple[AsyncClient, User], gate: dict, text: str
) -> None:
    http, user = client_user
    res = await _post_batch(http, text, gate["id"])
    assert res.status_code == 400
    assert res.json()["code"] == "empty_batch"
    assert await _batch_count(user.tenant_id) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_and_retired_gate_are_404(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    res = await _post_batch(http, "abc", 999999999)
    assert res.status_code == 404
    assert res.json()["code"] == "gate_not_found"

    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    assert (
        await owner_client.delete(f"/api/admin/gates/{gate['id']}")
    ).status_code == 204
    res = await _post_batch(http, "abc", gate["id"])
    assert res.status_code == 404
    assert res.json()["code"] == "gate_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_new_batch_applies_gate_dedups_and_orders(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, user = client_user
    value = gate["value"]
    text = f"abc\n{value} def\nabc\n\n  ghi  "
    res = await _post_batch(http, text, gate["id"])
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["appended"] is False
    assert body["added"] == 3
    assert body["state"] == "sending"
    assert body["gate_value"] == value
    assert body["gate_name"] == gate["name"]
    assert (body["sent"], body["queued"], body["total"]) == (0, 3, 3)

    lines = await _lines_of(body["id"])
    assert [line.text for line in lines] == [
        f"{value} abc",
        f"{value} def",
        f"{value} ghi",
    ]
    assert [line.position for line in lines] == [0, 1, 2]
    assert all(line.state == "queued" for line in lines)
    assert all(line.tenant_id == user.tenant_id for line in lines)

    async with async_session_factory() as session:
        batch = await session.get(Batch, body["id"])
        assert batch is not None
        assert batch.state == "sending"
        assert batch.is_owner_priority is False


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_batch_is_flagged_owner_priority(
    ctx: dict[str, object], gate: dict
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await _post_batch(owner_client, "abc", gate["id"])
    assert res.status_code == 201, res.text
    batch_id = res.json()["id"]
    try:
        async with async_session_factory() as session:
            batch = await session.get(Batch, batch_id)
            assert batch is not None
            assert batch.is_owner_priority is True
    finally:
        async with async_session_factory() as session:
            await session.execute(delete(Batch).where(Batch.id == batch_id))
            await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_no_artificial_batch_size_cap(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    text = "\n".join(f"linea {i}" for i in range(300))
    res = await _post_batch(http, text, gate["id"])
    assert res.status_code == 201, res.text
    assert res.json()["added"] == 300


@pytest.mark.asyncio(loop_scope="session")
async def test_append_to_live_batch(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
) -> None:
    """AC 10: while live, POST appends — same batch, pending-only dedup, the
    LIVE batch's gate applied even when a different valid gate_id arrives."""
    http, user = client_user
    value = gate["value"]
    first = await _post_batch(http, "uno\ndos", gate["id"])
    assert first.status_code == 201
    batch_id = first.json()["id"]

    # Simulate the worker having sent "uno" already.
    async with async_session_factory() as session:
        lines = await _lines_of(batch_id)
        row = await session.get(BatchLine, lines[0].id)
        assert row is not None
        row.state = "sent"
        row.sent_at = datetime.now(UTC)
        await session.commit()

    # A second, different-but-valid gate is validated yet IGNORED on append.
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    other = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": f".o{uuid.uuid4().hex[:6]}",
            "name": "Otro",
            "category_id": gate["category_id"],
        },
    )
    assert other.status_code == 201

    second = await _post_batch(http, "uno\ndos\ntres", other.json()["id"])
    assert second.status_code == 201, second.text
    body = second.json()
    assert body["appended"] is True
    assert body["id"] == batch_id
    # "dos" is still queued → deduped; "uno" was SENT → re-queued; "tres" new.
    assert body["added"] == 2
    assert await _batch_count(user.tenant_id) == 1

    lines = await _lines_of(batch_id)
    texts = [line.text for line in lines]
    # Appended lines carry the LIVE batch's gate, not the submitted one.
    assert texts == [
        f"{value} uno",
        f"{value} dos",
        f"{value} uno",
        f"{value} tres",
    ]
    assert [line.position for line in lines] == [0, 1, 2, 3]

    # Appending only-duplicates is NOT an error: added == 0.
    third = await _post_batch(http, "dos", gate["id"])
    assert third.status_code == 201
    assert third.json()["added"] == 0

    # … but a whitespace-only paste still is (AC 4).
    blank = await _post_batch(http, "   ", gate["id"])
    assert blank.status_code == 400
    assert blank.json()["code"] == "empty_batch"


@pytest.mark.asyncio(loop_scope="session")
async def test_unauthorized_telegram_is_503(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = client_user
    monkeypatch.setattr(gateway, "authorized", False)
    res = await _post_batch(http, "abc", gate["id"])
    assert res.status_code == 503
    assert res.json()["code"] == "telegram_unauthorized"


@pytest.mark.asyncio(loop_scope="session")
async def test_tenant_isolation_of_live_batch_and_snapshot(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http_a, user_a = client_user
    res = await _post_batch(http_a, "abc", gate["id"])
    assert res.status_code == 201

    user_b = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    try:
        async with async_session_factory() as session:
            assert (
                await batches_repo.get_live_batch(session, user_b.tenant_id)
            ) is None
            snap = await batches_service.snapshot(session, user_b.tenant_id)
            assert snap["state"] == "idle"
            assert snap["batch_id"] is None
            # …while tenant A's snapshot sees its own live batch.
            snap_a = await batches_service.snapshot(session, user_a.tenant_id)
            assert snap_a["state"] == "sending"
            assert snap_a["batch_id"] == res.json()["id"]
    finally:
        await cleanup_users({user_b.email})


# --- Send worker (FakeGateway — no real Telegram) ---------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_drains_batch_to_completed(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    res = await _post_batch(http, "uno\ndos", gate["id"])
    assert res.status_code == 201
    batch_id = res.json()["id"]

    assert await send_worker.step() is True
    assert await send_worker.step() is True
    assert await send_worker.step() is False  # queue empty → idle

    value = gate["value"]
    assert fake_gateway.sent == [f"{value} uno", f"{value} dos"]
    lines = await _lines_of(batch_id)
    assert all(line.state == "sent" for line in lines)
    assert all(line.sent_at is not None for line in lines)
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None
        assert batch.state == "completed"


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_floodwait_retries_same_line_once(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """AC 7: FloodWait → wait the requested duration, retry the SAME line —
    delivered exactly once, nothing lost."""
    http, _ = client_user
    res = await _post_batch(http, "solo", gate["id"])
    assert res.status_code == 201
    fake_gateway.errors.append(FloodWaitError(request=None, capture=0))

    assert await send_worker.step() is True

    value = gate["value"]
    assert fake_gateway.sent == [f"{value} solo"]
    lines = await _lines_of(res.json()["id"])
    assert lines[0].state == "sent"


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_generic_error_retries_line_not_lost(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = client_user
    res = await _post_batch(http, "solo", gate["id"])
    assert res.status_code == 201
    monkeypatch.setattr(send_worker, "_ERROR_RETRY_SECONDS", 0.0)
    fake_gateway.errors.append(RuntimeError("boom"))

    assert await send_worker.step() is True

    lines = await _lines_of(res.json()["id"])
    assert lines[0].state == "sent"  # retried, never marked failed/lost
    assert len(fake_gateway.sent) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_requeue_stuck_sending_on_boot(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, _ = client_user
    res = await _post_batch(http, "uno", gate["id"])
    assert res.status_code == 201
    batch_id = res.json()["id"]
    async with async_session_factory() as session:
        lines = await _lines_of(batch_id)
        row = await session.get(BatchLine, lines[0].id)
        assert row is not None
        row.state = "sending"  # simulate a crash mid-send
        await session.commit()

    async with async_session_factory() as session:
        requeued = await batches_repo.requeue_stuck_sending(session)
        await session.commit()
    assert requeued >= 1
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"


# --- WS snapshot + handshake helper ------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_idle_shape(client_user: tuple[AsyncClient, User]) -> None:
    _, user = client_user
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap == {
        "state": "idle",
        "batch_id": None,
        "gate_name": None,
        "gate_value": None,
        "sent": 0,
        "queued": 0,
        "total": 0,
        "eta_seconds": 0,
        "cc_new": 0,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_live_shape_and_eta_math(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, user = client_user
    monkeypatch.setattr(settings, "send_interval_seconds", 2.0)
    res = await _post_batch(http, "uno\ndos\ntres", gate["id"])
    assert res.status_code == 201

    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["state"] == "sending"
    assert snap["batch_id"] == res.json()["id"]
    assert snap["gate_name"] == gate["name"]
    assert snap["gate_value"] == gate["value"]
    assert (snap["sent"], snap["queued"], snap["total"]) == (0, 3, 3)
    assert snap["eta_seconds"] == 6.0  # queued × interval (UX-DR14)
    assert snap["cc_new"] == 0  # hardcoded until Epic 3


@pytest.mark.asyncio(loop_scope="session")
async def test_ws_handshake_helper_chain(
    client_user: tuple[AsyncClient, User],
) -> None:
    """Mirror of deps._resolve_session_user: valid → user; anything else None."""
    from app.api.ws import resolve_ws_user

    http, user = client_user
    token = http.cookies.get(settings.session_cookie_name)
    assert token

    async with async_session_factory() as session:
        resolved = await resolve_ws_user(session, token)
        assert resolved is not None
        assert resolved.id == user.id
        assert resolved.tenant_id == user.tenant_id

        assert await resolve_ws_user(session, None) is None
        assert await resolve_ws_user(session, "not-a-real-token") is None

    # Blocked → handshake fails (no revocation side effects here).
    async with async_session_factory() as session:
        row = await session.get(User, user.id)
        assert row is not None
        row.is_blocked = True
        await session.commit()
    async with async_session_factory() as session:
        assert await resolve_ws_user(session, token) is None
        row = await session.get(User, user.id)
        assert row is not None
        row.is_blocked = False
        row.must_change_password = True
        await session.commit()
    async with async_session_factory() as session:
        assert await resolve_ws_user(session, token) is None
        row = await session.get(User, user.id)
        assert row is not None
        row.must_change_password = False
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()
    async with async_session_factory() as session:
        assert await resolve_ws_user(session, token) is None
