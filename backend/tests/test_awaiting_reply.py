"""Awaiting-reply counter (spec-awaiting-reply-count): delivered lines that
have no ✅/❌ reply yet, session-scoped.

Same idiom as test_attribution: real ASGI app against the dev Postgres,
self-seeding, ``FakeGateway`` (incremental message ids populate ``send_log``),
events verified by a broadcaster recorder, capture driven DIRECTLY through
``capture.process_incoming`` (ASGITransport never runs the lifespan).

Run (from backend/, venv active):  pytest tests/test_awaiting_reply.py
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Batch, User
from app.services import batches as batches_service
from httpx import AsyncClient

from tests.conftest import FakeGateway, cleanup_users, seed_user

# --- Local helpers (mirror test_attribution) ---------------------------------


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
    return int(res.json()["id"])


async def _drain() -> None:
    from app.core import send_worker

    while await send_worker.step():
        pass


async def _awaiting(tenant_id: int) -> int:
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, tenant_id)
    return snap["awaiting_reply"]


async def _capture_session_id(batch_id: int) -> int:
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None and batch.capture_session_id is not None
        return batch.capture_session_id


def _captured(events: list[tuple]) -> list[tuple]:
    return [e for e in events if e[1] == "response.captured"]


def _progress(events: list[tuple]) -> list[tuple]:
    return [e for e in events if e[1] == "batch.progress"]


# --- The I/O matrix ----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delivered_lines_are_awaiting_until_first_reply(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Three lines sent, none answered → 3 esperando; the first ✅/❌ per line
    drops it by one. A no-verdict reply (now a neutral row) does NOT resolve a
    line — esperando stays put (neutral is excluded from _answered_full_exists)."""
    http, user = client_user
    await _post_batch(http, "uno\ndos\ntres", gate["id"])
    await _drain()  # FakeGateway message ids 1, 2, 3
    assert await _awaiting(user.tenant_id) == 3

    # First ✅ for line 1 → 2 awaiting.
    await capture.process_incoming(
        IncomingReply(message_id=1101, reply_to_msg_id=1, text="✅ ok", edited=False)
    )
    assert await _awaiting(user.tenant_id) == 2

    # A ❌ also counts as "answered" → 1 awaiting.
    await capture.process_incoming(
        IncomingReply(
            message_id=1102, reply_to_msg_id=2, text="❌ no", edited=False
        )
    )
    assert await _awaiting(user.tenant_id) == 1

    # A pure ⏳ persists a NEUTRAL row, which does NOT "answer" the line → line 3
    # is still awaiting (count unchanged — neutral ∉ _answered_full_exists).
    await capture.process_incoming(
        IncomingReply(
            message_id=1103, reply_to_msg_id=3, text="⏳ proc", edited=False
        )
    )
    assert await _awaiting(user.tenant_id) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_revision_of_answered_message_does_not_change_count(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A second revision of an already-answered message must NOT drop the count
    again (DISTINCT message_id) — answered once, answered forever."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()
    assert await _awaiting(user.tenant_id) == 1

    await capture.process_incoming(
        IncomingReply(
            message_id=1201, reply_to_msg_id=1, text="❌ Rechazada", edited=False
        )
    )
    assert await _awaiting(user.tenant_id) == 0

    # ❌ → ✅ edit: same message_id, a new revision row — still 0, never -1.
    await capture.process_incoming(
        IncomingReply(
            message_id=1201, reply_to_msg_id=1, text="✅ Aprobada", edited=True
        )
    )
    assert await _awaiting(user.tenant_id) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_count_is_session_scoped_and_survives_batch_completion(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Two batches on the same gate share one session; the count spans both and
    survives each batch draining (counters never reset between batches)."""
    http, user = client_user
    first = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = await _capture_session_id(first)
    assert await _awaiting(user.tenant_id) == 1

    second = await _post_batch(http, "dos", gate["id"])
    await _drain()
    assert await _capture_session_id(second) == session_id  # same session
    assert await _awaiting(user.tenant_id) == 2  # spans both batches

    await capture.process_incoming(
        IncomingReply(message_id=1301, reply_to_msg_id=1, text="✅ ok", edited=False)
    )
    assert await _awaiting(user.tenant_id) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_progress_and_captured_events_carry_authoritative_awaiting(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """The live path is authoritative: batch.progress climbs 1→2 as lines go
    out, and the response.captured frame carries the post-reply value."""
    http, _ = client_user
    await _post_batch(http, "uno\ndos", gate["id"])
    await _drain()  # two sends → two batch.progress frames

    # A progress frame fires at batch start (sent=0 → 0) and after each send;
    # the count only ever climbs as lines go out, ending at 2 (both delivered).
    seq = [p[2]["awaiting_reply"] for p in _progress(events)]
    assert seq == sorted(seq)  # monotonic — never decreases on the send path
    assert seq[-1] == 2

    await capture.process_incoming(
        IncomingReply(message_id=1401, reply_to_msg_id=1, text="✅ ok", edited=False)
    )
    captured = _captured(events)
    assert captured[-1][2]["awaiting_reply"] == 1
    # The emit carries the persisted Response.id (ws.ts dedups response.captured
    # by it — same value as the snapshot's s-${id} row key).
    assert isinstance(captured[-1][2]["id"], int)
    # The emit carries the authoritative ✅-message total (Aprobadas badge) so
    # ws.ts assigns it instead of delta-summing — one ✅ message ⇒ 1.
    assert captured[-1][2]["responses_ok_total"] == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_no_active_session_is_zero_and_tenant_isolated(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A tenant that never sent has 0 awaiting, and one tenant's awaiting count
    never leaks into another's snapshot."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()
    assert await _awaiting(user.tenant_id) == 1

    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    try:
        assert await _awaiting(other.tenant_id) == 0
    finally:
        await cleanup_users({other.email})


@pytest.mark.asyncio(loop_scope="session")
async def test_awaiting_reply_count_none_session_is_zero() -> None:
    """The helper short-circuits a batch with no session bound yet → 0."""
    async with async_session_factory() as session:
        assert await batches_service.awaiting_reply_count(session, None) == 0
