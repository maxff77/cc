"""Story 3.3 Historial tests: list (newest first, active flag), detail with
the COMPLETE data (uncapped, vs the capped snapshot), rename (200-char cap,
unguarded by live batches), delete (live-batch guard, FK CASCADE on
responses, SET NULL on batches) and tenant isolation (404 never leaks
existence — three verbs, three bad ids).

Same idiom as test_attribution.py: real ASGI app against the dev Postgres,
self-seeding, self-cleaning, ``FakeGateway``; captures go DIRECT to
``capture.process_incoming`` (ASGITransport never runs the lifespan) and
batches drain via ``send_worker.step()`` — no sockets, no telethon.

Run (from backend/, venv active):  pytest tests/test_sessions.py
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, send_worker
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Batch, CaptureSession, Response, User
from app.main import app
from app.services import batches as batches_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

NOT_FOUND_BODY = {"code": "session_not_found", "message": "Esa sesión no existe."}
IN_USE_BODY = {
    "code": "session_in_use",
    "message": "Detén el lote antes de eliminar esta sesión.",
}

# --- Local helpers -----------------------------------------------------------


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


async def _get_session_row(session_id: int) -> CaptureSession:
    async with async_session_factory() as session:
        row = await session.get(CaptureSession, session_id)
        assert row is not None
        return row


async def _response_rows(capture_session_id: int) -> list[Response]:
    async with async_session_factory() as session:
        stmt = (
            select(Response)
            .where(Response.capture_session_id == capture_session_id)
            .order_by(Response.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _bound_session_id(batch_id: int) -> int:
    session_id = (await _get_batch(batch_id)).capture_session_id
    assert session_id is not None
    return session_id


async def _capture_ok(message_id: int, reply_to: int, text: str) -> None:
    await capture.process_incoming(
        IncomingReply(
            message_id=message_id, reply_to_msg_id=reply_to, text=text, edited=False
        )
    )


async def _create_other_gate(ctx: dict[str, object], gate: dict) -> dict:
    """A second active gate in the SAME category (covered by the gate
    fixture's category-wide cleanup)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": f".h{uuid.uuid4().hex[:6]}",
            "name": "Otro Historial",
            "category_id": gate["category_id"],
        },
    )
    assert res.status_code == 201, res.text
    body: dict = res.json()
    return body


# --- List (AC 1) --------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_list_sessions_newest_first_with_active_flag_and_snapshots(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    first_batch = await _post_batch(http, "uno", gate["id"])
    first_session = await _bound_session_id(first_batch)
    await _drain()  # batch completes — the next POST starts a NEW batch

    other = await _create_other_gate(ctx, gate)
    second_batch = await _post_batch(http, "dos", other["id"])
    second_session = await _bound_session_id(second_batch)
    assert second_session != first_session

    res = await http.get("/api/sessions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == 2
    newest, oldest = body["items"]  # newest FIRST
    assert (newest["id"], oldest["id"]) == (second_session, first_session)
    assert (newest["is_active"], oldest["is_active"]) == (True, False)
    assert (newest["name"], oldest["name"]) == (None, None)
    # Gate strings are session SNAPSHOTS, verbatim from binding time.
    assert (newest["gate_value"], newest["gate_name"]) == (
        other["value"],
        other["name"],
    )
    assert (oldest["gate_value"], oldest["gate_name"]) == (
        gate["value"],
        gate["name"],
    )
    assert newest["created_at"] and oldest["created_at"]


# --- Detail (AC 2) -------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_carries_full_and_cc_rows_exact_shape(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # FakeGateway → send_log.message_id == 1
    session_id = await _bound_session_id(batch_id)

    text = "✅ Aprobada CC: 4111 Status aprobada"
    await _capture_ok(1001, 1, text)

    res = await http.get(f"/api/sessions/{session_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == session_id
    assert body["name"] is None
    assert body["gate_value"] == gate["value"]
    assert body["gate_name"] == gate["name"]
    assert body["is_active"] is True
    assert (body["responses_total"], body["cc_total"]) == (1, 1)

    rows = await _response_rows(session_id)
    full_db = next(r for r in rows if r.kind == "full")
    cc_db = next(r for r in rows if r.kind == "cc")

    (full_row,) = body["responses"]
    created_at = full_row.pop("created_at")
    assert datetime.fromisoformat(created_at) == full_db.created_at
    assert full_row == {
        "id": full_db.id,
        "message_id": 1001,
        "status": "ok",
        "text": text,
    }
    (cc_row,) = body["cc"]
    # Truncated at the literal "Status" (intentional parsing, 🔒).
    assert cc_row == {"id": cc_db.id, "text": "4111"}


@pytest.mark.asyncio(loop_scope="session")
async def test_detail_is_uncapped_while_snapshot_stays_capped(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The contrast proves ``limit=None``: with the snapshot cap at 1 and two
    captures, the snapshot list ships 1 row while the Historial detail
    delivers BOTH (the complete data belongs to Historial — 3.2 promise)."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = await _bound_session_id(batch_id)
    monkeypatch.setattr(batches_service, "_SNAPSHOT_ROWS", 1)

    await _capture_ok(9401, 1, "✅ Primera")
    await _capture_ok(9402, 1, "✅ Segunda")

    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert len(snap["responses"]) == 1  # capped

    res = await http.get(f"/api/sessions/{session_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert [row["message_id"] for row in body["responses"]] == [9401, 9402]
    assert body["responses_total"] == 2


# --- Rename (AC 4) --------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_rename_persists_caps_at_200_and_ignores_live_batches(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    session_id = await _bound_session_id(batch_id)

    # Rename WITH the batch still live ⇒ 200 (legacy parity: unguarded).
    res = await http.patch(f"/api/sessions/{session_id}", json={"name": "Visa junio"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == session_id
    assert body["name"] == "Visa junio"
    assert body["is_active"] is True

    # Persisted: the list reflects it.
    listed = await http.get("/api/sessions")
    (item,) = [s for s in listed.json()["items"] if s["id"] == session_id]
    assert item["name"] == "Visa junio"

    # Exactly 200 chars is the cap (String(200), legacy escribir_nombre).
    res = await http.patch(f"/api/sessions/{session_id}", json={"name": "x" * 200})
    assert res.status_code == 200, res.text
    assert res.json()["name"] == "x" * 200

    # 201 chars / empty / whitespace-only ⇒ 422 (pydantic field_validator).
    assert (
        await http.patch(f"/api/sessions/{session_id}", json={"name": "x" * 201})
    ).status_code == 422
    assert (
        await http.patch(f"/api/sessions/{session_id}", json={"name": ""})
    ).status_code == 422
    assert (
        await http.patch(f"/api/sessions/{session_id}", json={"name": "   "})
    ).status_code == 422


# --- Delete (AC 5 + 6) -----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_guarded_by_live_batch_then_cascades_clean(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    first_batch = await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1 — and the batch completes
    session_id = await _bound_session_id(first_batch)
    await _capture_ok(1101, 1, "✅ Aprobada CC: 4111 Status a")
    assert len(await _response_rows(session_id)) == 2  # full + cc

    # Same gate ⇒ the new LIVE batch binds the SAME session (legacy reuse).
    second_batch = await _post_batch(http, "dos", gate["id"])
    assert await _bound_session_id(second_batch) == session_id

    res = await http.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 409, res.text
    assert res.json() == IN_USE_BODY  # the message IS the AC 6 copy

    # Stop the lote — the session is deletable even while STILL active:
    # the guard is "bound to a live batch", not is_active.
    stop = await http.post(f"/api/batches/{second_batch}/stop")
    assert stop.status_code == 204, stop.text
    assert (await _get_session_row(session_id)).is_active is True

    res = await http.delete(f"/api/sessions/{session_id}")
    assert res.status_code == 204, res.text

    # Gone from the list …
    listed = await http.get("/api/sessions")
    assert [s["id"] for s in listed.json()["items"]] == []
    # … its responses rows died with it (FK CASCADE) …
    assert await _response_rows(session_id) == []
    # … and BOTH batches survive, unbound (FK SET NULL — lote history is
    # the lote's own).
    for batch_id in (first_batch, second_batch):
        batch = await _get_batch(batch_id)
        assert batch.capture_session_id is None
    assert (await _get_batch(first_batch)).state == "completed"
    assert (await _get_batch(second_batch)).state == "stopped"


# --- 404 never leaks existence (AC 8) ---------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_not_found_is_identical_for_unknown_foreign_and_overflow_ids(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http_a, _ = client_user
    batch_id = await _post_batch(http_a, "uno", gate["id"])
    session_a = await _bound_session_id(batch_id)

    user_b = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http_b, user_b.email)

        # B's list NEVER contains A's sessions.
        listed = await http_b.get("/api/sessions")
        assert listed.json() == {"items": [], "total": 0}

        # Three bad ids — A's id seen from B (the three verbs), an unknown id
        # and an out-of-int4 id — all 404 with the IDENTICAL body.
        res = await http_b.get(f"/api/sessions/{session_a}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.patch(
            f"/api/sessions/{session_a}", json={"name": "ajena"}
        )
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.delete(f"/api/sessions/{session_a}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        unknown = 2**31 - 1  # int4-max: valid bind, never a real id here
        res = await http_b.get(f"/api/sessions/{unknown}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        overflow = 2**31  # out of int4 — guarded before it can hit asyncpg
        res = await http_b.get(f"/api/sessions/{overflow}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.patch(
            f"/api/sessions/{overflow}", json={"name": "nada"}
        )
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.delete(f"/api/sessions/{overflow}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        # A, of course, still reaches their own session.
        res = await http_a.get(f"/api/sessions/{session_a}")
        assert res.status_code == 200
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})
