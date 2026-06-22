"""Limpiar (sessionless cockpit, PR-1) tests: the cockpit "Limpiar" stamps a
NON-destructive view-cutoff on the tenant's ONE perpetual capture session.

Asserts the load-bearing invariants of the spec:
- ``clear_view`` stamps ``cleared_response_id = MAX(responses.id)`` (an ``id``
  high-water-mark, NOT a timestamp) and deletes ZERO ``responses`` rows.
- the DISPLAY reads (``list_full``/``list_cc``/``full_count``/``cc_count``) hide
  every row with ``id <= cutoff`` — all three panels (Completa, Aprobadas ✅,
  Datos CC) empty — while a no-cutoff read still returns every row (PR-2-ready:
  approved ✅ rows survive).
- the INTEGRITY queries IGNORE the cutoff: ``responded_line_count`` is
  unchanged (so "esperando respuesta" does NOT spike), ``has_ok_revision`` still
  finds the ✅, and the ``add_new_cc`` dedup SELECT (now PER-MESSAGE) is
  cutoff-agnostic — a CC value cleared then re-seen on a NEW message IS
  re-inserted (Datos CC mirrors Aprobadas), only the pre-cutoff row stays hidden.
- a same-instant tie (two rows sharing ``created_at``) is split cleanly by the
  ``id`` high-water-mark — a ``created_at`` cutoff would leak/hide a boundary row.
- the endpoint is tenant-scoped (a foreign/unknown tenant ⇒ 404, no leak),
  returns ``{"cleared_response_id": n}`` and re-emits ``session.active`` with the
  now-empty post-cutoff slice.

Same idiom as test_sessions.py: real ASGI app against the dev Postgres, captures
go DIRECT to ``capture.process_incoming``, batches drain via ``send_worker.step``.

Run (from backend/, venv active):  pytest tests/test_clear_declined.py
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, send_worker
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Batch, Response, User
from app.db.repos import responses as responses_repo
from app.main import app
from app.services import batches as batches_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

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


async def _full_row_count(capture_session_id: int) -> int:
    """Physical 'full' rows (no cutoff, no hidden filter) — the row CENSUS."""
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Response)
                .where(
                    Response.capture_session_id == capture_session_id,
                    Response.kind == responses_repo.KIND_FULL,
                )
            )
        ).scalar_one()


async def _cc_texts(capture_session_id: int) -> list[str]:
    """Every physical 'cc' value (no cutoff) ordered by id — the dedup CENSUS."""
    async with async_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Response.text)
                    .where(
                        Response.capture_session_id == capture_session_id,
                        Response.kind == responses_repo.KIND_CC,
                    )
                    .order_by(Response.id)
                )
            )
            .scalars()
            .all()
        )


async def _create_other_gate(http_owner: AsyncClient, gate: dict) -> dict:
    """A second active gate in the SAME category (covered by the gate fixture's
    category-wide cleanup)."""
    res = await http_owner.post(
        "/api/admin/gates",
        json={
            "value": f".h{uuid.uuid4().hex[:6]}",
            "name": "Otro Limpiar",
            "display_value": "Otro Visible",
            "category_id": gate["category_id"],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


# --- Limpiar hides all 3 panels; deletes 0 rows; integrity unchanged ---------


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_hides_all_panels_deletes_nothing_keeps_integrity(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "a\nb\nc", gate["id"])
    await _drain()  # message_id 1..3, send_log filled
    session_id = await _bound_session_id(batch_id)

    # Two ✅ (each yields a CC) and one ❌ — all three panels have content.
    await _capture(7001, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(7002, 2, "✅ Aprobada CC: 4222 Status b")
    await _capture(7003, 3, "❌ Declinada")

    # Baseline: Completa 3, Aprobadas 2, Datos CC 2; 3 answered ⇒ awaiting 0.
    async with async_session_factory() as session:
        snap = await batches_service.active_session_data(session, user.tenant_id)
    assert (snap["responses_total"], snap["responses_ok_total"], snap["cc_new"]) == (
        3,
        2,
        2,
    )
    assert snap["awaiting_reply"] == 0

    rows_before = await _full_row_count(session_id)
    cc_before = await _cc_texts(session_id)
    assert rows_before == 3
    assert cc_before == ["4111", "4222"]

    res = await http.post("/api/sessions/clear")
    assert res.status_code == 200, res.text
    cutoff = res.json()["cleared_response_id"]
    assert isinstance(cutoff, int) and cutoff > 0

    # All three DISPLAY panels are now empty (id <= cutoff hidden) …
    async with async_session_factory() as session:
        snap = await batches_service.active_session_data(session, user.tenant_id)
        assert (
            snap["responses_total"],
            snap["responses_ok_total"],
            snap["cc_new"],
        ) == (0, 0, 0)
        assert snap["responses"] == [] and snap["cc"] == []
        # … awaiting stays cutoff-AGNOSTIC — it does NOT spike (still 0).
        assert snap["awaiting_reply"] == 0
        # DISPLAY reads honor the cutoff; a no-cutoff read still sees everything.
        assert await responses_repo.full_count(
            session, session_id, cleared_response_id=cutoff
        ) == 0
        assert await responses_repo.full_count(session, session_id) == 3
        assert (
            await responses_repo.full_count(
                session, session_id, status=responses_repo.STATUS_OK,
                cleared_response_id=cutoff,
            )
            == 0
        )
        # ✅ survives un-cut (PR-2-ready) …
        assert (
            await responses_repo.full_count(
                session, session_id, status=responses_repo.STATUS_OK
            )
            == 2
        )
        assert (
            len(await responses_repo.list_full(session, session_id, None)) == 3
        )
        assert (
            len(
                await responses_repo.list_full(
                    session, session_id, None, cleared_response_id=cutoff
                )
            )
            == 0
        )
        # INTEGRITY queries IGNORE the cutoff.
        assert await responses_repo.responded_line_count(session, session_id) == 3
        assert await responses_repo.has_ok_revision(
            session, chat_id=0, message_id=7001
        )

    # ZERO responses rows deleted — the census is identical.
    assert await _full_row_count(session_id) == rows_before
    assert await _cc_texts(session_id) == cc_before

    # The endpoint re-emitted session.active to the tenant with the empty slice.
    active = [e for e in events if e[1] == "session.active"]
    assert active, "expected a session.active re-emit"
    tenant_id, _, data = active[-1]
    assert tenant_id == user.tenant_id
    assert (data["responses_total"], data["responses_ok_total"], data["cc_new"]) == (
        0,
        0,
        0,
    )
    assert data["responses"] == [] and data["cc"] == []


# --- Reconnect after Limpiar: the snapshot merges the same cutoff ------------


@pytest.mark.asyncio(loop_scope="session")
async def test_snapshot_after_clear_stays_empty_then_new_capture_reappears(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A reconnect post-Limpiar rebuilds empty panels (``snapshot`` merges
    ``active_session_data`` → same cutoff); a NEW capture (id > cutoff)
    reappears (the cutoff is a high-water-mark, not a freeze)."""
    http, user = client_user
    await _post_batch(http, "a\nb", gate["id"])
    await _drain()  # message_id 1..2
    await _capture(8001, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(8002, 2, "❌ Declinada")

    res = await http.post("/api/sessions/clear")
    assert res.status_code == 200, res.text

    # Reconnect snapshot: panels empty (the merge applies the cutoff).
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert (snap["responses_total"], snap["cc_new"]) == (0, 0)
    assert snap["responses"] == [] and snap["cc"] == []

    # A NEW reply (its row id > cutoff) reappears — Limpiar froze nothing.
    await _capture(8003, 1, "✅ Aprobada CC: 4333 Status c")
    async with async_session_factory() as session:
        snap = await batches_service.snapshot(session, user.tenant_id)
    assert snap["responses_total"] == 1
    assert [r["status"] for r in snap["responses"]] == ["ok"]
    assert [c["text"] for c in snap["cc"]] == ["4333"]


@pytest.mark.asyncio(loop_scope="session")
async def test_live_cc_total_event_respects_cutoff_after_clear(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """The live ``response.captured`` event's ``cc_total`` (the cockpit "Datos CC
    nuevas" badge, assigned verbatim by ws.ts) must honor the Limpiar cutoff —
    otherwise the first reply after a Limpiar snaps the badge back to the full
    historical count while the snapshot/session.active path shows the post-clear
    slice. Regression guard for the capture.py ``cc_count`` cutoff."""
    http, _ = client_user
    await _post_batch(http, "a\nb", gate["id"])
    await _drain()  # message_id 1..2
    await _capture(8101, 1, "✅ Aprobada CC: 4111 Status a")

    res = await http.post("/api/sessions/clear")
    assert res.status_code == 200, res.text

    # A NEW CC after the clear: its live event must count ONLY post-cutoff CC
    # (1), not the full history (2).
    await _capture(8102, 2, "✅ Aprobada CC: 4222 Status b")
    captured = [e for e in events if e[1] == "response.captured"]
    assert captured[-1][2]["new_cc"] == ["4222"]
    assert captured[-1][2]["cc_total"] == 1  # post-cutoff only (NOT 2)


# --- Cross-gate CC re-capture after Limpiar: per-message dedup ---------------


@pytest.mark.asyncio(loop_scope="session")
async def test_cleared_cc_re_seen_on_new_message_is_reinserted(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A CC value captured on gate A, then CLEARED, then re-seen on a DIFFERENT
    message (gate B) IS re-inserted: dedup is PER-MESSAGE
    (``uq_responses_session_msg_cc``), so each approved card contributes its CC.
    Limpiar (cutoff-only) still hides the pre-cutoff row from the live panel."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    http, user = client_user

    # Gate A: capture "4111".
    batch_a = await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1
    session_id = await _bound_session_id(batch_a)
    await _capture(9001, 1, "✅ Aprobada CC: 4111 Status a")
    assert await _cc_texts(session_id) == ["4111"]

    # Limpiar wipes the live view.
    res = await http.post("/api/sessions/clear")
    assert res.status_code == 200, res.text
    async with async_session_factory() as session:
        snap = await batches_service.active_session_data(session, user.tenant_id)
    assert snap["cc_new"] == 0 and snap["cc"] == []

    # Gate B reuses the SAME perpetual session (snapshot refreshed in place).
    other = await _create_other_gate(owner_client, gate)
    batch_b = await _post_batch(http, "dos", other["id"])
    assert await _bound_session_id(batch_b) == session_id
    await _drain()  # message_id 2

    # Re-seeing "4111" on a NEW message DOES re-insert it (per-message dedup) …
    await _capture(9002, 2, "✅ Aprobada CC: 4111 Status b")
    assert await _cc_texts(session_id) == ["4111", "4111"]  # census keeps both
    # … and a genuinely new value lands too.
    await _capture(9003, 2, "✅ Aprobada CC: 4222 Status c")
    assert await _cc_texts(session_id) == ["4111", "4111", "4222"]
    async with async_session_factory() as session:
        snap = await batches_service.active_session_data(session, user.tenant_id)
    # The live panel shows only the POST-cutoff rows (the pre-Limpiar 4111 is
    # below the cutoff): the re-seen 4111 + the new 4222.
    assert [c["text"] for c in snap["cc"]] == ["4111", "4222"]


# --- Same-instant tie: id high-water splits two rows sharing created_at ------


@pytest.mark.asyncio(loop_scope="session")
async def test_same_instant_tie_split_cleanly_by_id_high_water(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Two rows sharing the SAME ``created_at`` must be split cleanly by the
    ``id`` high-water-mark: clearing at the FIRST row's id hides exactly that
    row and keeps the second, even though a ``created_at`` cutoff (txn-start
    ``now()``) could not tell them apart."""
    http, user = client_user
    batch_id = await _post_batch(http, "a\nb", gate["id"])
    await _drain()  # message_id 1..2
    session_id = await _bound_session_id(batch_id)
    await _capture(6001, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(6002, 2, "✅ Aprobada CC: 4222 Status b")

    # Force the two 'full' rows to share an identical created_at (the txn-start
    # tie the design note warns about), then cut at the FIRST row's id.
    async with async_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(Response)
                    .where(
                        Response.capture_session_id == session_id,
                        Response.kind == responses_repo.KIND_FULL,
                    )
                    .order_by(Response.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        shared = rows[0].created_at
        rows[1].created_at = shared  # identical timestamp → the tie
        first_id = rows[0].id
        await session.commit()

    # Stamp the cutoff at the FIRST row's id (simulating a Limpiar that landed
    # exactly between two same-instant captures).
    async with async_session_factory() as session:
        from app.db.models import CaptureSession

        cs = await session.get(CaptureSession, session_id)
        assert cs is not None
        cs.cleared_response_id = first_id
        await session.commit()

    # Exactly the second row survives the cut — the tie did NOT leak/hide it.
    async with async_session_factory() as session:
        visible = await responses_repo.list_full(
            session, session_id, None, cleared_response_id=first_id
        )
        assert [r.message_id for r in visible] == [6002]
        assert (
            await responses_repo.full_count(
                session, session_id, cleared_response_id=first_id
            )
            == 1
        )

    # The live snapshot reflects the same clean split.
    async with async_session_factory() as session:
        snap = await batches_service.active_session_data(session, user.tenant_id)
    assert [r["message_id"] for r in snap["responses"]] == [6002]


# --- Tenant isolation: POST /api/sessions/clear 404s identically -------------


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_404s_for_tenant_without_a_session(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A tenant that never sent a batch has no perpetual session ⇒ POST
    /api/sessions/clear 404s identically (``tenant_id`` only from the session,
    no existence leak). After its first batch the SAME call succeeds."""
    http, _ = client_user  # fresh tenant — never sent a batch
    res = await http.post("/api/sessions/clear")
    assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

    # Once a batch creates the perpetual session, clear succeeds.
    await _post_batch(http, "uno", gate["id"])
    res = await http.post("/api/sessions/clear")
    assert res.status_code == 200, res.text
    assert "cleared_response_id" in res.json()


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_does_not_touch_another_tenants_session(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """One tenant's Limpiar stamps ONLY its own perpetual session — a second
    tenant's session keeps ``cleared_response_id`` NULL and its panels full
    (the cutoff is per-session, resolved from the cookie, never cross-tenant)."""
    http, _ = client_user
    a_batch = await _post_batch(http, "a", gate["id"])
    await _drain()  # message_id 1
    a_session = await _bound_session_id(a_batch)
    await _capture(5001, 1, "✅ Aprobada CC: 4111 Status a")

    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    other_http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(other_http, other.email)
        b_batch = await _post_batch(other_http, "b", gate["id"])
        await _drain()  # message_id 2
        b_session = await _bound_session_id(b_batch)
        assert b_session != a_session
        await _capture(5002, 2, "✅ Aprobada CC: 4222 Status b")

        # Tenant A clears — only A's session gets a cutoff.
        res = await http.post("/api/sessions/clear")
        assert res.status_code == 200, res.text

        # B's session keeps cleared_response_id NULL and its panels FULL.
        async with async_session_factory() as session:
            from app.db.models import CaptureSession

            b_cs = await session.get(CaptureSession, b_session)
            assert b_cs is not None
            assert b_cs.cleared_response_id is None
            b_snap = await batches_service.active_session_data(
                session, b_cs.tenant_id
            )
        assert b_snap["responses_total"] == 1
        assert [c["text"] for c in b_snap["cc"]] == ["4222"]
    finally:
        await other_http.aclose()
        from tests.conftest import cleanup_users

        await cleanup_users({other.email})
