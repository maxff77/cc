"""Story 3.1 capture/attribution tests: session binding at batch start, reply
mapping (``reply_to_msg_id`` → send_log → tenant/batch/line/session), the
edit state machine, per-message CC dedup, the unmatched-replies bucket,
cross-tenant isolation (which must fail), the DB-down reply buffer and the
boot-reconciliation intent fix (deferred 2-5 :616).

Same idiom as the rest of the suite: real ASGI app against the dev Postgres,
self-seeding, self-cleaning, ``FakeGateway`` (incremental message ids populate
``send_log``), events verified by monkeypatching the broadcaster with a
recorder list (2.2 lesson — never sockets). ASGITransport does not run the
lifespan, so capture calls go DIRECT to ``capture.process_incoming`` (the
``step()`` idiom) — no telethon anywhere.

Run (from backend/, venv active):  pytest tests/test_attribution.py
"""

import asyncio
import contextlib
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, send_worker
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.core.cc_extract import extract_cc
from app.db.base import async_session_factory
from app.db.models import Batch, BatchLine, CaptureSession, Response, SendLog, User
from app.db.repos import batches as batches_repo
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import responses as responses_repo
from app.main import app
from app.services import batches as batches_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

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


async def _drain() -> None:
    """Run worker steps until the queue is empty (FakeGateway ids 1..n)."""
    while await send_worker.step():
        pass


async def _get_batch(batch_id: int) -> Batch:
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None
        return batch


async def _lines_of(batch_id: int) -> list[BatchLine]:
    async with async_session_factory() as session:
        stmt = (
            select(BatchLine)
            .where(BatchLine.batch_id == batch_id)
            .order_by(BatchLine.position)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _sessions_of(tenant_id: int) -> list[CaptureSession]:
    async with async_session_factory() as session:
        stmt = (
            select(CaptureSession)
            .where(CaptureSession.tenant_id == tenant_id)
            .order_by(CaptureSession.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _full_rows(message_id: int) -> list[Response]:
    async with async_session_factory() as session:
        stmt = (
            select(Response)
            .where(Response.message_id == message_id, Response.kind == "full")
            .order_by(Response.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _cc_rows(capture_session_id: int) -> list[Response]:
    async with async_session_factory() as session:
        stmt = (
            select(Response)
            .where(
                Response.capture_session_id == capture_session_id,
                Response.kind == "cc",
            )
            .order_by(Response.id)
        )
        return list((await session.execute(stmt)).scalars().all())


def _captured(events: list[tuple]) -> list[tuple]:
    return [e for e in events if e[1] == "response.captured"]


async def _create_other_gate(ctx: dict[str, object], gate: dict) -> dict:
    """A second active gate in the SAME category (the gate fixture's cleanup
    deletes every gate of its category, so this one is covered too)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": f".o{uuid.uuid4().hex[:6]}",
            "name": "Otro Lote",
            "display_value": "Otro Lote Visible",
            "category_id": gate["category_id"],
        },
    )
    assert res.status_code == 201, res.text
    body: dict = res.json()
    return body


# --- extract_cc (exact port of legacy extraer_cc / RE_CC) --------------------


def test_extract_cc_truncates_at_literal_status() -> None:
    # 🔒 intentional parsing: each value is cut at the literal "Status".
    assert extract_cc("✅ ok CC: 4111 Status aprobada") == ["4111"]
    assert extract_cc("CC: abc Statusxyz") == ["abc"]


def test_extract_cc_case_insensitive_multiple_and_order() -> None:
    text = "cc: uno Status a\nalgo\nCC : dos\nCc:tres Status b"
    assert extract_cc(text) == ["uno", "dos", "tres"]


def test_extract_cc_discards_empty_values() -> None:
    assert extract_cc("CC: Status nada") == []
    assert extract_cc("sin datos") == []


# --- Binding at batch start (AC 3) -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_new_batch_binds_active_capture_session_with_snapshots(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])

    batch = await _get_batch(batch_id)
    assert batch.capture_session_id is not None
    sessions = await _sessions_of(user.tenant_id)
    assert [s.id for s in sessions] == [batch.capture_session_id]
    assert sessions[0].is_active is True
    # Gate strings snapshotted verbatim (no FK to gates — history immutable).
    assert (sessions[0].gate_value, sessions[0].gate_name) == (
        gate["value"],
        gate["name"],
    )
    assert sessions[0].name is None  # friendly name arrives in Story 3.3


@pytest.mark.asyncio(loop_scope="session")
async def test_every_batch_reuses_the_one_perpetual_session(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Sessionless cockpit (PR-1): a tenant has exactly ONE ever-living capture
    session. Same gate OR a different gate both REUSE it — a different gate just
    refreshes the gate snapshot in place (no second row, no is_active/id churn).
    The ≤1-active partial unique index is preserved trivially."""
    http, user = client_user

    first_id = await _post_batch(http, "uno", gate["id"])
    session_id = (await _get_batch(first_id)).capture_session_id
    assert session_id is not None
    await _drain()  # batch completes — the next POST starts a NEW batch

    second_id = await _post_batch(http, "dos", gate["id"])
    assert second_id != first_id
    assert (await _get_batch(second_id)).capture_session_id == session_id
    await _drain()

    other = await _create_other_gate(ctx, gate)
    third_id = await _post_batch(http, "tres", other["id"])
    # Different gate → the SAME session, snapshot refreshed in place.
    assert (await _get_batch(third_id)).capture_session_id == session_id

    sessions = await _sessions_of(user.tenant_id)
    assert [s.id for s in sessions] == [session_id]  # still exactly one
    assert sessions[0].is_active is True
    assert sessions[0].gate_value == other["value"]  # snapshot refreshed


# --- Reply mapping (AC 4 + 6 + 8) ---------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reply_maps_to_exact_tenant_batch_line_and_saves_cc(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # FakeGateway → send_log.message_id == 1
    line = (await _lines_of(batch_id))[0]
    session_id = (await _get_batch(batch_id)).capture_session_id
    assert session_id is not None

    text = "✅ Aprobada CC: 4111 Status aprobada"
    await capture.process_incoming(
        IncomingReply(message_id=1001, reply_to_msg_id=1, text=text, edited=False)
    )

    fulls = await _full_rows(1001)
    assert len(fulls) == 1
    assert fulls[0].status == "ok"
    assert fulls[0].text == text
    assert fulls[0].tenant_id == user.tenant_id
    assert fulls[0].capture_session_id == session_id
    assert fulls[0].batch_id == batch_id
    assert fulls[0].line_id == line.id

    ccs = await _cc_rows(session_id)
    assert [c.text for c in ccs] == ["4111"]  # truncated at literal "Status"
    assert ccs[0].status is None

    captured = _captured(events)
    assert len(captured) == 1
    tenant_id, _, data = captured[0]
    assert tenant_id == user.tenant_id
    assert data["session_id"] == session_id
    assert data["batch_id"] == batch_id
    assert data["message_id"] == 1001
    assert data["status"] == "ok"
    assert data["previous_status"] is None
    assert data["edited"] is False
    assert data["text"] == text
    assert data["new_cc"] == ["4111"]
    assert data["cc_total"] == 1
    assert data["captured_at"]  # ISO-8601 timestamp present


@pytest.mark.asyncio(loop_scope="session")
async def test_first_intermediate_revision_without_emoji_is_not_persisted(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Legacy parity (recorded decision): the first ⏳ produces no row and no
    event — its later ✅ edit arrives with reply_to intact and attributes."""
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    await capture.process_incoming(
        IncomingReply(
            message_id=4001, reply_to_msg_id=1, text="⏳ Procesando", edited=False
        )
    )
    assert await _full_rows(4001) == []
    assert _captured(events) == []
    assert capture._unmatched_total == 0  # attributed — NOT the bucket

    await capture.process_incoming(
        IncomingReply(
            message_id=4001,
            reply_to_msg_id=1,
            text="✅ Aprobada CC: 7777 Status x",
            edited=True,
        )
    )
    fulls = await _full_rows(4001)
    assert [f.status for f in fulls] == ["ok"]
    captured = _captured(events)
    assert len(captured) == 1
    assert captured[0][2]["previous_status"] is None


# --- Edits (AC 5) -------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_appends_revision_and_only_new_cc(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = (await _get_batch(batch_id)).capture_session_id
    assert session_id is not None

    await capture.process_incoming(
        IncomingReply(
            message_id=1002,
            reply_to_msg_id=1,
            text="✅ CC: 4111 Status a",
            edited=False,
        )
    )
    # The edit deliberately carries NO reply_to: attribution must hold via the
    # previous responses row (AC 5 "message_id is preserved").
    await capture.process_incoming(
        IncomingReply(
            message_id=1002,
            reply_to_msg_id=None,
            text="✅ CC: 4111 Status a\nCC: 5500 Status b",
            edited=True,
        )
    )

    fulls = await _full_rows(1002)
    assert [f.status for f in fulls] == ["ok", "ok"]
    assert fulls[1].batch_id == batch_id  # attribution reused from revision 1
    ccs = await _cc_rows(session_id)
    assert [c.text for c in ccs] == ["4111", "5500"]  # only the new one added

    captured = _captured(events)
    assert len(captured) == 2
    assert captured[1][2]["edited"] is True
    assert captured[1][2]["new_cc"] == ["5500"]
    assert captured[1][2]["cc_total"] == 2
    assert captured[1][2]["previous_status"] == "ok"


@pytest.mark.asyncio(loop_scope="session")
async def test_identical_edit_is_a_total_noop(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    reply = IncomingReply(
        message_id=1003, reply_to_msg_id=1, text="✅ CC: 4111 Status a", edited=False
    )
    await capture.process_incoming(reply)
    # catch_up replay / edition with no real change: zero rows, zero events.
    await capture.process_incoming(
        IncomingReply(
            message_id=1003, reply_to_msg_id=1, text=reply.text, edited=True
        )
    )

    assert len(await _full_rows(1003)) == 1
    assert len(_captured(events)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_then_ok_transition(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """❌ first (persisted — conscious deviation from the legacy disk), then
    the ✅ edit: the transition emits with previous_status and the CURRENT
    state (latest revision per message_id) is 'ok' — counters are derived."""
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    await capture.process_incoming(
        IncomingReply(
            message_id=1004, reply_to_msg_id=1, text="❌ Rechazada", edited=False
        )
    )
    fulls = await _full_rows(1004)
    assert [f.status for f in fulls] == ["rejected"]
    captured = _captured(events)
    assert len(captured) == 1
    assert captured[0][2]["status"] == "rejected"
    assert captured[0][2]["previous_status"] is None
    assert captured[0][2]["new_cc"] == []

    await capture.process_incoming(
        IncomingReply(
            message_id=1004,
            reply_to_msg_id=1,
            text="✅ Aprobada CC: 8888 Status z",
            edited=True,
        )
    )
    fulls = await _full_rows(1004)
    assert [f.status for f in fulls] == ["rejected", "ok"]
    captured = _captured(events)
    assert len(captured) == 2
    assert captured[1][2]["status"] == "ok"
    assert captured[1][2]["previous_status"] == "rejected"
    # The per-message current state is the LATEST revision.
    async with async_session_factory() as session:
        latest = await responses_repo.last_full_revision(
            session, chat_id=0, message_id=1004
        )
        assert latest is not None and latest.status == "ok"


@pytest.mark.asyncio(loop_scope="session")
async def test_ok_edit_without_new_cc_persists_without_emitting(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Legacy emission parity: ok→ok with new text but no new CC saves the
    revision silently."""
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    await capture.process_incoming(
        IncomingReply(
            message_id=1005, reply_to_msg_id=1, text="✅ Aprobada", edited=False
        )
    )
    await capture.process_incoming(
        IncomingReply(
            message_id=1005, reply_to_msg_id=1, text="✅ Aprobada ya", edited=True
        )
    )

    assert len(await _full_rows(1005)) == 2  # both revisions persisted
    assert len(_captured(events)) == 1  # only the ok transition emitted


# --- CC dedup is PER-MESSAGE (Datos CC mirrors Aprobadas) ---------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cc_dedup_is_per_message_not_tenant_lifetime(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Datos CC mirrors Aprobadas one-row-per-approved-card: dedup is
    PER-MESSAGE (uq_responses_session_msg_cc), not tenant-lifetime — the same CC
    value approved on two distinct messages lands TWICE (even across gates that
    reuse the one perpetual session)."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno\ndos", gate["id"])
    await _drain()  # message ids 1 and 2
    session_id = (await _get_batch(batch_id)).capture_session_id
    assert session_id is not None

    await capture.process_incoming(
        IncomingReply(
            message_id=1006, reply_to_msg_id=1, text="✅ CC: 4111 Status a",
            edited=False,
        )
    )
    await capture.process_incoming(
        IncomingReply(
            message_id=1007, reply_to_msg_id=2, text="✅ CC: 4111 Status b",
            edited=False,
        )
    )

    # TWO rows now — a second approved message with the same CC is NOT collapsed.
    assert [c.text for c in await _cc_rows(session_id)] == ["4111", "4111"]
    captured = _captured(events)
    assert len(captured) == 2  # the second is an ok transition …
    assert captured[1][2]["new_cc"] == ["4111"]  # … and brings its own CC
    assert captured[1][2]["cc_total"] == 2

    # A different gate REUSES the one session; a third approved message with the
    # same value still lands its own row (no cross-message collapse).
    other = await _create_other_gate(ctx, gate)
    other_batch_id = await _post_batch(http, "tres", other["id"])
    await _drain()  # message id 3
    assert (await _get_batch(other_batch_id)).capture_session_id == session_id
    await capture.process_incoming(
        IncomingReply(
            message_id=1008, reply_to_msg_id=3, text="✅ CC: 4111 Status c",
            edited=False,
        )
    )
    assert [c.text for c in await _cc_rows(session_id)] == ["4111", "4111", "4111"]
    assert _captured(events)[2][2]["new_cc"] == ["4111"]
    assert _captured(events)[2][2]["cc_total"] == 3


@pytest.mark.asyncio(loop_scope="session")
async def test_oversized_cc_value_is_truncated_never_poison(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Review 3-1 MEDIUM: a CC value longer than the btree index row cap would
    make the INSERT fail identically forever (uq_responses_session_cc) — the
    value is truncated to CC_MAX_CHARS BEFORE insert (uq_responses_session_msg_cc),
    and per-message dedup operates on the truncated value."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = (await _get_batch(batch_id)).capture_session_id
    assert session_id is not None

    big = "x" * 4000  # Telegram allows 4096 chars/message; raw, this insert
    await capture.process_incoming(  # would exceed the ~2704-byte btree cap
        IncomingReply(
            message_id=9201, reply_to_msg_id=1, text=f"✅ CC: {big}", edited=False
        )
    )
    ccs = await _cc_rows(session_id)
    assert len(ccs) == 1
    assert len(ccs[0].text) == responses_repo.CC_MAX_CHARS

    # Same message edited: same first CC_MAX_CHARS chars, different tail →
    # truncates to the SAME value → per-message dedup keeps it one row.
    await capture.process_incoming(
        IncomingReply(
            message_id=9201,
            reply_to_msg_id=1,
            text="✅ CC: " + "x" * 700 + "TAIL",
            edited=True,
        )
    )
    assert len(await _cc_rows(session_id)) == 1


def test_transient_classification() -> None:
    """Connectivity-shaped errors retry forever; data errors are bounded."""
    from sqlalchemy.exc import OperationalError

    assert capture._is_transient(ConnectionRefusedError("refused"))
    assert capture._is_transient(TimeoutError("pool"))
    assert capture._is_transient(OperationalError("stmt", None, Exception("x")))
    assert not capture._is_transient(ValueError("poison"))


# --- Unmatched replies bucket (AC 7) -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_unmatched_replies_are_logged_and_counted_never_saved(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    caplog: pytest.LogCaptureFixture,
) -> None:
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    # No reply_to at all, and a reply_to that matches no send_log row. On the
    # FINAL attribution attempt (earlier attempts re-enqueue — review 3-1).
    last = capture._ATTRIBUTION_ATTEMPTS - 1
    await capture.process_incoming(
        IncomingReply(
            message_id=5001, reply_to_msg_id=None, text="✅ CC: 1 Status",
            edited=False, attempts=last,
        )
    )
    await capture.process_incoming(
        IncomingReply(
            message_id=5002, reply_to_msg_id=999_999_999, text="✅ CC: 2 Status",
            edited=False, attempts=last,
        )
    )

    assert await _full_rows(5001) == []
    assert await _full_rows(5002) == []
    assert _captured(events) == []
    assert capture._unmatched_total == 2
    assert "event=unmatched_reply" in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_reply_racing_the_record_phase_retries_and_attributes(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review 3-1 HIGH: a reply processed BEFORE the worker commits
    send_log.message_id is NOT terminally unmatched — it re-enqueues (with
    attempts+1, no counter bump) and attributes once the record phase lands."""
    http, _ = client_user
    monkeypatch.setattr(capture, "_ATTRIBUTION_RETRY_SECONDS", 0.0)
    await _post_batch(http, "uno", gate["id"])
    # The race shape: the reply references message_id 1 while NO send_log row
    # carries it yet (the batch hasn't been drained — record phase pending).
    await capture.process_incoming(
        IncomingReply(
            message_id=7001, reply_to_msg_id=1, text="✅ CC: 7777 Status a",
            edited=False,
        )
    )
    assert capture._unmatched_total == 0  # NOT bucketed
    assert await _full_rows(7001) == []

    # The delayed requeue lands the same reply back on the queue, attempts+1.
    deadline = time.monotonic() + 5.0
    while capture._queue.empty():
        assert time.monotonic() < deadline, "retry was never re-enqueued"
        await asyncio.sleep(0.01)
    retried = capture._queue.get_nowait()
    assert retried.attempts == 1
    assert retried.message_id == 7001

    await _drain()  # the worker's record phase commits message_id=1 …
    await capture.process_incoming(retried)  # … and the retry attributes
    fulls = await _full_rows(7001)
    assert len(fulls) == 1 and fulls[0].status == "ok"
    assert capture._unmatched_total == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_poison_reply_is_quarantined_and_queue_keeps_flowing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Review 3-1 MEDIUM: a NON-transient failure (same error on every
    attempt) is quarantined after a bounded number of retries instead of
    wedging the single global consumer; the item behind it still processes."""
    monkeypatch.setattr(capture, "_RETRY_SECONDS", 0.0)
    processed: list[int] = []

    async def fake_process(reply: IncomingReply) -> None:
        if reply.message_id == 9001:
            raise ValueError("poison")  # non-transient: fails identically
        processed.append(reply.message_id)

    monkeypatch.setattr(capture, "process_incoming", fake_process)
    capture.enqueue(
        IncomingReply(message_id=9001, reply_to_msg_id=1, text="x", edited=False)
    )
    capture.enqueue(
        IncomingReply(message_id=9002, reply_to_msg_id=2, text="y", edited=False)
    )

    task = asyncio.create_task(capture.run_capture())
    try:
        deadline = time.monotonic() + 5.0
        while processed != [9002]:
            assert time.monotonic() < deadline, "queue stayed wedged"
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert "event=capture_quarantined" in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_capture_consumer_waits_for_boot_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review 3-1 HIGH (boot variant): with the boot gate held, catch_up
    replays buffer unconsumed; ``_boot_recovery`` releases the gate even on
    its no-work path and the consumer flushes."""
    processed: list[int] = []

    async def fake_process(reply: IncomingReply) -> None:
        processed.append(reply.message_id)

    monkeypatch.setattr(capture, "process_incoming", fake_process)
    capture.hold_until_boot()
    capture.enqueue(
        IncomingReply(message_id=9101, reply_to_msg_id=1, text="x", edited=False)
    )

    task = asyncio.create_task(capture.run_capture())
    try:
        await asyncio.sleep(0.05)
        assert processed == []  # held: nothing consumed yet
        await send_worker._boot_recovery()  # finally-releases the gate
        assert capture._boot_gate.is_set()
        deadline = time.monotonic() + 5.0
        while processed != [9101]:
            assert time.monotonic() < deadline, "gate never released"
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# --- Cross-tenant isolation (AC 4 + 9: must fail) --------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reply_saves_only_to_its_own_tenant(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http_a, user_a = client_user
    batch_a = await _post_batch(http_a, "uno", gate["id"])
    await _drain()  # A's line → message_id 1

    user_b = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http_b, user_b.email)
        await _post_batch(http_b, "dos", gate["id"])
        await _drain()  # B's line → message_id 2

        async with async_session_factory() as session:
            a_message_id = (
                await session.execute(
                    select(SendLog.message_id).where(
                        SendLog.tenant_id == user_a.tenant_id
                    )
                )
            ).scalar_one()
        assert a_message_id is not None

        await capture.process_incoming(
            IncomingReply(
                message_id=2001,
                reply_to_msg_id=a_message_id,
                text="✅ CC: 9999 Status x",
                edited=False,
            )
        )

        # Rows land ONLY under A's tenant, batch and session.
        fulls = await _full_rows(2001)
        assert len(fulls) == 1
        assert fulls[0].tenant_id == user_a.tenant_id
        assert fulls[0].batch_id == batch_a
        # The event reaches ONLY tenant A's sockets.
        captured = _captured(events)
        assert [tenant for tenant, _, _ in captured] == [user_a.tenant_id]

        # Tenant-scoped repo access from B can NEVER reach A's session.
        async with async_session_factory() as session:
            session_a = await capture_sessions_repo.get_active(
                session, user_a.tenant_id
            )
            session_b = await capture_sessions_repo.get_active(
                session, user_b.tenant_id
            )
            assert session_a is not None and session_b is not None
            assert session_a.id != session_b.id
            assert fulls[0].capture_session_id == session_a.id
        assert await _cc_rows(session_b.id) == []
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


# --- Backfill is read-only over the one perpetual session (PR-1) -----------------


@pytest.mark.asyncio(loop_scope="session")
async def test_backfill_reuses_the_perpetual_session_read_only(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """A late reply to an unbound batch (capture_session_id SET-NULLed) resolves
    via the READ-ONLY backfill: it returns the tenant's ONE perpetual active
    session and rebinds the batch to it — never minting/activating a session
    (that would risk the partial-index IntegrityError → capture poison-drop).
    The live session is never deactivated; there is always exactly one."""
    http, user = client_user
    batch_a = await _post_batch(http, "uno", gate["id"])  # binds the perpetual S
    await _drain()  # message_id 1
    other = await _create_other_gate(ctx, gate)
    batch_b = await _post_batch(http, "dos", other["id"])  # REUSES S (gate B snap)
    await _drain()  # message_id 2

    sessions = await _sessions_of(user.tenant_id)
    assert len(sessions) == 1  # one perpetual session, regardless of gate
    s = sessions[0]
    assert s.is_active is True

    # Sever batch A's binding (the pre-PR-1 / SET-NULL unbound shape).
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_a)
        assert batch is not None
        batch.capture_session_id = None
        await session.commit()

    # Late reply to batch A → read-only backfill returns the perpetual S.
    await capture.process_incoming(
        IncomingReply(
            message_id=8001, reply_to_msg_id=1, text="✅ CC: 8888 Status x",
            edited=False,
        )
    )

    sessions = await _sessions_of(user.tenant_id)
    assert len(sessions) == 1  # NO new session minted
    assert sessions[0].id == s.id and sessions[0].is_active is True
    assert (await _get_batch(batch_a)).capture_session_id == s.id  # rebacked
    assert (await _get_batch(batch_b)).capture_session_id == s.id  # untouched
    fulls = await _full_rows(8001)
    assert len(fulls) == 1 and fulls[0].capture_session_id == s.id

    # Severing another batch and replaying still resolves to the SAME S.
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_b)
        assert batch is not None
        batch.capture_session_id = None
        await session.commit()
    await capture.process_incoming(
        IncomingReply(
            message_id=8002, reply_to_msg_id=2, text="✅ CC: 9999 Status y",
            edited=False,
        )
    )
    assert (await _get_batch(batch_b)).capture_session_id == s.id
    assert len(await _sessions_of(user.tenant_id)) == 1


# --- DB-down buffer (deferred 2-5 :28, absorbed by design) -----------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_capture_buffers_replies_while_db_down_and_flushes_in_order(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The blocked-queue + retry-forever consumer IS the in-memory buffer:
    with the DB down both enqueued replies wait (the first retried, the second
    in the queue) and BOTH persist in order once the DB returns."""
    http, _ = client_user
    await _post_batch(http, "uno\ndos", gate["id"])
    await _drain()  # message ids 1 and 2

    monkeypatch.setattr(capture, "_RETRY_SECONDS", 0.0)
    real_factory = async_session_factory
    fail = {"remaining": 2}

    def flaky_factory():  # type: ignore[no-untyped-def]
        if fail["remaining"] > 0:
            fail["remaining"] -= 1
            # OSError subclass — classified TRANSIENT (retry forever), the
            # honest shape of a down Postgres (review 3-1: non-transient
            # errors are bounded + quarantined instead).
            raise ConnectionRefusedError("db down")
        return real_factory()

    monkeypatch.setattr(capture, "async_session_factory", flaky_factory)

    capture.enqueue(
        IncomingReply(
            message_id=3001, reply_to_msg_id=1, text="✅ CC: 1111 Status a",
            edited=False,
        )
    )
    capture.enqueue(
        IncomingReply(
            message_id=3002, reply_to_msg_id=2, text="✅ CC: 2222 Status b",
            edited=False,
        )
    )

    task = asyncio.create_task(capture.run_capture())
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if await _full_rows(3001) and await _full_rows(3002):
                break
            await asyncio.sleep(0.01)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    first = await _full_rows(3001)
    second = await _full_rows(3002)
    assert len(first) == 1 and len(second) == 1
    assert first[0].id < second[0].id  # flushed IN ORDER
    assert fail["remaining"] == 0  # both injected failures were consumed
    assert "event=db_unreachable phase=capture" in caplog.text


# --- Boot reconciliation records the intent (deferred 2-5 :616) -------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_recovery_confirm_creates_missing_intent_row(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A line left 'sending' WITHOUT a send_log row (pre-2.5 crash shape) that
    reconciliation confirms must end 'sent' WITH a send_log record — otherwise
    3.1 could never attribute its replies."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    async with async_session_factory() as session:
        line = (
            await session.execute(
                select(BatchLine).where(BatchLine.batch_id == batch_id)
            )
        ).scalar_one()
        await batches_repo.mark_sending(session, line)  # NO record_intent
        await session.commit()
        line_id = line.id
    fake_gateway.outgoing = [(0, 99, f"{gate['value']} uno")]

    await send_worker._boot_recovery()

    lines = await _lines_of(batch_id)
    assert [li.state for li in lines] == ["sent"]
    async with async_session_factory() as session:
        row = (
            await session.execute(
                select(SendLog).where(SendLog.line_id == line_id)
            )
        ).scalar_one()
        assert row.message_id == 99


# --- Snapshot cc_new is real (Task 8) ---------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_cc_new_counts_the_active_session(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()
    await capture.process_incoming(
        IncomingReply(
            message_id=6001, reply_to_msg_id=1, text="✅ CC: 4111 Status a",
            edited=False,
        )
    )

    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["cc_new"] == 1  # no longer hardcoded — and it survives the
    # batch's completion (counters never reset, legacy parity)

    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    try:
        async with async_session_factory() as session:
            snap_other = await batches_service.snapshot(session, other.tenant_id)
        assert snap_other["cc_new"] == 0  # someone else's captures never leak
    finally:
        await cleanup_users({other.email})


# --- Snapshot rebuilds the dual views (Story 3.2) ---------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_carries_active_session_rows_and_totals(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """After a ✅ capture with CC the snapshot ALONE rebuilds both panels:
    session_id, the full revision (Completa), the truncated CC value
    (Filtrada) and the honest totals."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = (await _get_batch(batch_id)).capture_session_id
    assert session_id is not None

    text = "✅ Aprobada CC: 4111 Status aprobada"
    await capture.process_incoming(
        IncomingReply(message_id=9301, reply_to_msg_id=1, text=text, edited=False)
    )

    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["session_id"] == session_id
    assert snap["responses_total"] == 1
    assert snap["cc_new"] == 1
    (row,) = snap["responses"]
    assert row["message_id"] == 9301
    assert row["status"] == "ok"
    assert row["text"] == text
    assert row["created_at"]  # ISO-8601 timestamp present
    (cc_row,) = snap["cc"]
    assert cc_row["text"] == "4111"  # truncated at the literal "Status"


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_rejected_revision_travels_with_status(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """A ❌ revision ships with status='rejected' — AC 2's ❌ glyph in the
    Completa panel comes from here."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    await capture.process_incoming(
        IncomingReply(
            message_id=9302, reply_to_msg_id=1, text="❌ Rechazada", edited=False
        )
    )

    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    (row,) = snap["responses"]
    assert row["status"] == "rejected"
    assert (snap["cc"], snap["cc_new"]) == ([], 0)  # ❌ extracts nothing


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_rows_capped_but_totals_stay_honest(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the cap at 1 and two captures, the list ships ONLY the latest
    revision but responses_total keeps the REAL count (badges never lie)."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()
    monkeypatch.setattr(batches_service, "_SNAPSHOT_ROWS", 1)

    await capture.process_incoming(
        IncomingReply(
            message_id=9303, reply_to_msg_id=1, text="✅ Primera", edited=False
        )
    )
    await capture.process_incoming(
        IncomingReply(
            message_id=9304, reply_to_msg_id=1, text="✅ Segunda", edited=False
        )
    )

    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["responses_total"] == 2
    (row,) = snap["responses"]  # capped to the LAST revision only
    assert row["message_id"] == 9304


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_collapses_revisions_to_one_row_per_message(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Two revisions of the SAME (chat_id, message_id) collapse to ONE Completa
    row (the LATEST) and responses_total counts the MESSAGE once — not every edit
    (cockpit-completa-one-row-per-message). Storage keeps both rows; the COLLAPSE
    is a read."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1
    await capture.process_incoming(
        IncomingReply(
            message_id=7701, reply_to_msg_id=1, text="✅ Primera", edited=False
        )
    )
    await capture.process_incoming(  # EDIT of the SAME bot message
        IncomingReply(
            message_id=7701, reply_to_msg_id=1, text="✅ Segunda", edited=True
        )
    )

    # Storage still holds BOTH revisions — the collapse never deletes.
    async with async_session_factory() as session:
        full = (
            await session.execute(
                select(Response).where(
                    Response.tenant_id == user.tenant_id,
                    Response.kind == "full",
                    Response.message_id == 7701,
                )
            )
        ).scalars().all()
        assert len(full) == 2

        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["responses_total"] == 1  # one MESSAGE, not two revisions
    assert snap["responses_ok_total"] == 1
    (row,) = snap["responses"]  # one row...
    assert row["text"] == "✅ Segunda"  # ...the LATEST revision


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_session_rows_are_tenant_isolated(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Another tenant's snapshot keeps a null session and empty panels even
    while this tenant's session holds captured rows."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain()
    await capture.process_incoming(
        IncomingReply(
            message_id=9305, reply_to_msg_id=1, text="✅ CC: 4111 Status a",
            edited=False,
        )
    )

    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    try:
        async with async_session_factory() as session:
            snap = await batches_service.snapshot(session, user.tenant_id)
            snap_other = await batches_service.snapshot(session, other.tenant_id)
        assert snap["session_id"] is not None and snap["responses_total"] == 1
        assert snap_other["session_id"] is None
        assert (snap_other["responses"], snap_other["cc"]) == ([], [])
        assert (snap_other["responses_total"], snap_other["cc_new"]) == (0, 0)
    finally:
        await cleanup_users({other.email})
