"""clear-declined tests: the cockpit "Limpiar" soft-hides a session's declined
(❌) 'full' revisions.

Asserts the load-bearing invariants of the spec:
- ``hide_rejected`` marks ONLY rejected 'full' rows of the target session; ✅
  and 'cc' are untouched; it is idempotent.
- the DISPLAY reads (``list_full``/``full_count``) drop the hidden rows, while
  ``include_hidden=True`` still returns them.
- the INTEGRITY queries keep counting the hidden rows: ``responded_line_count``
  is unchanged (so "esperando respuesta" does NOT spike) and the reply
  reconciler's work-list (``awaiting_sent_keys``) stays empty (a hidden ❌ is
  never re-fetched from Telegram and re-inserted — the resurrection a physical
  DELETE would cause).
- the endpoint is tenant-scoped (unknown/foreign/oversize id ⇒ 404, no leak),
  returns ``{"hidden": n}`` and re-emits ``session.active`` with the trimmed
  Completa.

Same idiom as test_sessions.py: real ASGI app against the dev Postgres, captures
go DIRECT to ``capture.process_incoming``, batches drain via ``send_worker.step``.

Run (from backend/, venv active):  pytest tests/test_clear_declined.py
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, send_worker
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Batch, Response, User
from app.db.repos import responses as responses_repo
from app.db.repos import send_log as send_log_repo
from app.main import app
from app.services import batches as batches_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import FakeGateway, login, seed_user

NOT_FOUND_BODY = {"code": "session_not_found", "message": "Esa sesión no existe."}
_PG_INT_MAX = 2**31 - 1


# --- Local helpers (mirrors test_sessions.py) --------------------------------


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
    while await send_worker.step():
        pass


async def _bound_session_id(batch_id: int) -> int:
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None and batch.capture_session_id is not None
        return batch.capture_session_id


async def _capture(message_id: int, reply_to: int, text: str) -> None:
    await capture.process_incoming(
        IncomingReply(
            message_id=message_id, reply_to_msg_id=reply_to, text=text, edited=False
        )
    )


async def _hidden_at_by_status(capture_session_id: int) -> list[tuple[str | None, bool]]:
    """Every 'full' row as ``(status, is_hidden)`` ordered by id."""
    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(Response)
                .where(
                    Response.capture_session_id == capture_session_id,
                    Response.kind == responses_repo.KIND_FULL,
                )
                .order_by(Response.id)
            )
        ).scalars().all()
        return [(r.status, r.hidden_at is not None) for r in rows]


# --- Repo-level: soft-hide marks only declined; display vs integrity ---------


@pytest.mark.asyncio(loop_scope="session")
async def test_hide_rejected_marks_only_declined_and_display_excludes_them(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    batch_id = await _post_batch(http, "a\nb\nc\nd", gate["id"])
    await _drain()  # message_id 1..4
    session_id = await _bound_session_id(batch_id)

    # Two ✅, two ❌ — each on its own line.
    await _capture(6001, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(6002, 2, "❌ Declinada")
    await _capture(6003, 3, "❌ Declinada")
    await _capture(6004, 4, "✅ Aprobada CC: 4222 Status b")

    async with async_session_factory() as session:
        hidden = await responses_repo.hide_rejected(session, session_id)
        await session.commit()
    assert hidden == 2

    # Only the two ❌ rows carry hidden_at; the ✅ rows stay visible.
    assert await _hidden_at_by_status(session_id) == [
        ("ok", False),
        ("rejected", True),
        ("rejected", True),
        ("ok", False),
    ]

    async with async_session_factory() as session:
        # DISPLAY reads drop the hidden rows; include_hidden brings them back.
        assert await responses_repo.full_count(session, session_id) == 2
        assert (
            await responses_repo.full_count(session, session_id, include_hidden=True)
            == 4
        )
        # ✅ total is unaffected (we never hide 'ok').
        assert (
            await responses_repo.full_count(
                session, session_id, status=responses_repo.STATUS_OK
            )
            == 2
        )
        visible = await responses_repo.list_full(session, session_id, None)
        assert [r.status for r in visible] == ["ok", "ok"]
        assert (
            len(await responses_repo.list_full(session, session_id, None, include_hidden=True))
            == 4
        )

        # Idempotent: a second clear hides nothing.
        assert await responses_repo.hide_rejected(session, session_id) == 0


# --- Endpoint: awaiting unchanged, reconciler safe, emit, tenant scope -------


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_declined_endpoint_keeps_counters_and_reconciler_and_emits(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "a\nb\nc", gate["id"])
    await _drain()  # message_id 1..3, send_log filled
    session_id = await _bound_session_id(batch_id)

    await _capture(7001, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(7002, 2, "❌ Declinada")
    await _capture(7003, 3, "❌ Declinada")

    # Baseline integrity: 3 lines delivered, 3 answered ⇒ nothing awaiting.
    within = datetime.now(UTC) - timedelta(hours=72)
    async with async_session_factory() as session:
        assert await responses_repo.responded_line_count(session, session_id) == 3
        assert await batches_service.awaiting_reply_count(session, session_id) == 0
        assert await send_log_repo.awaiting_sent_keys(session, within=within) == set()

    res = await http.post(f"/api/sessions/{session_id}/clear-declined")
    assert res.status_code == 200, res.text
    assert res.json() == {"hidden": 2}

    # The rejected rows are HIDDEN, not deleted — integrity queries unchanged.
    async with async_session_factory() as session:
        # responded_line_count still counts the hidden rows ⇒ awaiting stays 0
        # (a physical DELETE would drop it to 1 and spike awaiting to 2).
        assert await responses_repo.responded_line_count(session, session_id) == 3
        assert await batches_service.awaiting_reply_count(session, session_id) == 0
        # The reconciler work-list stays empty ⇒ a hidden ❌ is never re-fetched
        # and re-inserted (the resurrection a DELETE would invite).
        assert await send_log_repo.awaiting_sent_keys(session, within=within) == set()
        # Both ❌ rows still physically exist (retained for attribution).
        assert (
            await responses_repo.full_count(session, session_id, include_hidden=True)
            == 3
        )

    # Reload parity: a fresh snapshot slice shows the trimmed Completa.
    async with async_session_factory() as session:
        snap = await batches_service.active_session_data(session, user.tenant_id)
    assert snap["responses_total"] == 1
    assert snap["responses_ok_total"] == 1
    assert snap["awaiting_reply"] == 0
    assert [r["status"] for r in snap["responses"]] == ["ok"]

    # The endpoint re-emitted session.active to the tenant with the trimmed view.
    active = [e for e in events if e[1] == "session.active"]
    assert active, "expected a session.active re-emit"
    tenant_id, _, data = active[-1]
    assert tenant_id == user.tenant_id
    assert (data["responses_total"], data["responses_ok_total"]) == (1, 1)
    assert data["awaiting_reply"] == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_declined_unknown_and_foreign_session_404(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    # Unknown id and an out-of-int4 id both 404 alike (no existence leak).
    for bad in (99_999_999, _PG_INT_MAX + 1, 0):
        res = await http.post(f"/api/sessions/{bad}/clear-declined")
        assert res.status_code == 404, res.text
        assert res.json() == NOT_FOUND_BODY

    # A FOREIGN tenant's real session id ⇒ the same 404 (tenant_id from the
    # session, never the path).
    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    other_http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(other_http, other.email)
        other_batch = await _post_batch(other_http, "x", gate["id"])
        await _drain()
        foreign_session = await _bound_session_id(other_batch)
        res = await http.post(f"/api/sessions/{foreign_session}/clear-declined")
        assert res.status_code == 404, res.text
        assert res.json() == NOT_FOUND_BODY
    finally:
        await other_http.aclose()
        from tests.conftest import cleanup_users

        await cleanup_users({other.email})
