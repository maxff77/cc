"""Story 3.3 Historial tests: list (newest first, active flag), detail with
the COMPLETE data (uncapped, vs the capped snapshot), rename (200-char cap,
unguarded by live batches), delete (live-batch guard, FK CASCADE on
responses, SET NULL on batches) and tenant isolation (404 never leaks
existence — five verbs, three bad ids). Plus Story 3.4 Continuar:
reactivation by replacement + `session.active` emission, dedup preserved
across the continue (DB-backed, `uq_responses_session_cc`), the any-live-batch
guard (409 `batch_live`, paused included) and idempotency on the
already-active session. Plus Story 3.5 Export: `GET /{id}/export?view=` as
downloadable `.txt` — legacy-parity bodies asserted EXACT (filtrada: one
datum per line + final newline; completa: `[ts] {text}` blocks per revision),
Content-Disposition filename from the gate slug, on-the-fly generation (no
cache), unguarded during a live batch AND on closed sessions, empty body for
a session with no rows, and 422 on an invalid view.

Same idiom as test_attribution.py: real ASGI app against the dev Postgres,
self-seeding, self-cleaning, ``FakeGateway``; captures go DIRECT to
``capture.process_incoming`` (ASGITransport never runs the lifespan) and
batches drain via ``send_worker.step()`` — no sockets, no telethon.

Run (from backend/, venv active):  pytest tests/test_sessions.py
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, send_worker
from app.core.broadcaster import broadcaster
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
LIVE_BODY = {
    "code": "batch_live",
    "message": "Termina o detén el lote actual antes de continuar otra sesión.",
}

# --- Local helpers -----------------------------------------------------------


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Record every broadcaster emission as ``(tenant_id|None, event, data)``
    (idiom test_batch_controls.py — no socket plumbing)."""
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


# --- Continuar (Story 3.4, AC 1) ----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_continue_reactivates_by_replacement_and_emits_session_active(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, user = client_user
    # Session SA (gate A) with one captured CC …
    first_batch = await _post_batch(http, "uno", gate["id"])
    await _drain()  # FakeGateway → message_id 1
    session_a = await _bound_session_id(first_batch)
    text = "✅ Aprobada CC: 4111 Status aprobada"
    await _capture_ok(2001, 1, text)

    # … then gate B takes over the active session and its batch completes.
    other = await _create_other_gate(ctx, gate)
    second_batch = await _post_batch(http, "dos", other["id"])
    session_b = await _bound_session_id(second_batch)
    assert session_b != session_a
    await _drain()  # message_id 2

    res = await http.post(f"/api/sessions/{session_a}/continue")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == session_a
    assert body["is_active"] is True
    assert (body["gate_value"], body["gate_name"]) == (gate["value"], gate["name"])

    # Activation by replacement — exactly ONE active session.
    listed = await http.get("/api/sessions")
    flags = {s["id"]: s["is_active"] for s in listed.json()["items"]}
    assert flags == {session_a: True, session_b: False}

    # The event is the snapshot's session slice VERBATIM, emitted post-commit,
    # with the PREVIOUS rows present in exact shape.
    rows = await _response_rows(session_a)
    full_db = next(r for r in rows if r.kind == "full")
    cc_db = next(r for r in rows if r.kind == "cc")
    assert events[-1] == (
        user.tenant_id,
        "session.active",
        {
            "session_id": session_a,
            "cc_new": 1,
            "responses_total": 1,
            "responses_ok_total": 1,
            "responses": [
                {
                    "id": full_db.id,
                    "message_id": 2001,
                    "status": "ok",
                    "text": text,
                    "created_at": full_db.created_at.isoformat(),
                }
            ],
            # Truncated at the literal "Status" (intentional parsing, 🔒).
            "cc": [{"id": cc_db.id, "text": "4111"}],
        },
    )


# --- Dedup preserved + append to the same session (Story 3.4, AC 2) -----------


@pytest.mark.asyncio(loop_scope="session")
async def test_continue_preserves_dedup_and_appends_to_same_session(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """THE story test: after Continuar, a new batch of the same gate binds to
    the continued session and the CC dedup comes from the session's EXISTING
    rows (``add_new_cc`` + ``uq_responses_session_cc``) — Completa grows,
    Filtrada never repeats."""
    http, _ = client_user
    # SA (gate A) with the value "4111" already captured.
    first_batch = await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1
    session_a = await _bound_session_id(first_batch)
    await _capture_ok(2001, 1, "✅ Aprobada CC: 4111 Status a")

    # Gate B rotates the active session, its batch completes.
    other = await _create_other_gate(ctx, gate)
    second_batch = await _post_batch(http, "dos", other["id"])
    assert await _bound_session_id(second_batch) != session_a
    await _drain()  # message_id 2

    res = await http.post(f"/api/sessions/{session_a}/continue")
    assert res.status_code == 200, res.text

    # New sends APPEND to the same session: resolve_for_batch reuses the
    # (re)active session on gate match — the AC's "new sends append to it".
    third_batch = await _post_batch(http, "tres", gate["id"])
    assert await _bound_session_id(third_batch) == session_a
    await _drain()  # message_id 3

    # A reply repeating the SAME value ⇒ Completa grows, Filtrada does NOT
    # (the dedup came from the rows captured BEFORE the continue).
    await _capture_ok(2002, 3, "✅ Aprobada CC: 4111 Status b")
    res = await http.get(f"/api/sessions/{session_a}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cc_total"] == 1
    assert [row["text"] for row in body["cc"]] == ["4111"]
    assert body["responses_total"] == 2

    # A genuinely NEW value lands; insertion order preserved.
    await _capture_ok(2003, 3, "✅ Aprobada CC: 4222 Status c")
    res = await http.get(f"/api/sessions/{session_a}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["cc_total"] == 2
    assert [row["text"] for row in body["cc"]] == ["4111", "4222"]
    # Every 'full' revision is present — Completa grew with each reply.
    assert [row["message_id"] for row in body["responses"]] == [2001, 2002, 2003]


# --- Live-batch guard (Story 3.4, AC 3) ----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_continue_rejected_while_any_batch_is_live_or_paused(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, _ = client_user
    first_batch = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_a = await _bound_session_id(first_batch)

    # A live (sending) batch of ANOTHER gate — not drained on purpose.
    other = await _create_other_gate(ctx, gate)
    second_batch = await _post_batch(http, "dos\ntres", other["id"])
    session_b = await _bound_session_id(second_batch)

    # sending ⇒ 409 with the AC 3 copy verbatim …
    res = await http.post(f"/api/sessions/{session_a}/continue")
    assert (res.status_code, res.json()) == (409, LIVE_BODY)

    # … and paused too (legacy `_lote_vivo` parity: live OR paused).
    pause = await http.post(f"/api/batches/{second_batch}/pause")
    assert pause.status_code == 204, pause.text
    res = await http.post(f"/api/sessions/{session_a}/continue")
    assert (res.status_code, res.json()) == (409, LIVE_BODY)

    # Nothing changed and NO session.active went out.
    listed = await http.get("/api/sessions")
    flags = {s["id"]: s["is_active"] for s in listed.json()["items"]}
    assert flags == {session_a: False, session_b: True}
    assert not any(event == "session.active" for _, event, _ in events)

    # Stop the lote ⇒ the SAME continue now succeeds.
    stop = await http.post(f"/api/batches/{second_batch}/stop")
    assert stop.status_code == 204, stop.text
    res = await http.post(f"/api/sessions/{session_a}/continue")
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is True


# --- Idempotency (Story 3.4) ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_continue_already_active_session_is_idempotent(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Continuing the ALREADY-active session (surface idle) is a clean no-op:
    200, the row STAYS active (the repo's UPDATE excludes the target — the
    documented pitfall) and the reconcile event goes out anyway."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # batch completes — surface idle, session stays active
    session_a = await _bound_session_id(batch_id)
    assert (await _get_session_row(session_a)).is_active is True

    res = await http.post(f"/api/sessions/{session_a}/continue")
    assert res.status_code == 200, res.text
    assert res.json()["is_active"] is True

    # Really still active in the DB, and the list keeps exactly ONE active.
    assert (await _get_session_row(session_a)).is_active is True
    listed = await http.get("/api/sessions")
    assert [s["is_active"] for s in listed.json()["items"]] == [True]

    # The session.active emission happened anyway (cheap multi-tab reconcile).
    tenant_id, event, data = events[-1]
    assert (tenant_id, event) == (user.tenant_id, "session.active")
    assert data["session_id"] == session_a


# --- Export (Story 3.5) ---------------------------------------------------------


def _expected_filename(gate_value: str, session_id: int, view: str) -> str:
    """The export filename derived with the SAME slug rule as the endpoint —
    the fixtures' gate values are random, never hardcode the slug."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", gate_value.lstrip(".")).strip("_")
    return f"{slug or 'gate'}-{session_id}-{view}.txt"


def _assert_txt_headers(res, gate_value: str, session_id: int, view: str) -> None:
    assert res.headers["content-type"].startswith("text/plain")
    expected = _expected_filename(gate_value, session_id, view)
    assert res.headers["content-disposition"] == f'attachment; filename="{expected}"'


@pytest.mark.asyncio(loop_scope="session")
async def test_export_filtrada_is_one_datum_per_line_with_final_newline(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """AC 1, filtrada: legacy ``filtrada.txt`` parity VERBATIM — one CC datum
    per line + final newline, insertion order, no timestamps."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1
    session_id = await _bound_session_id(batch_id)
    await _capture_ok(3501, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture_ok(3502, 1, "✅ Aprobada CC: 4222 Status b")

    res = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert res.status_code == 200, res.text
    _assert_txt_headers(res, gate["value"], session_id, "filtrada")
    assert res.text == "4111\n4222\n"


@pytest.mark.asyncio(loop_scope="session")
async def test_export_completa_carries_every_revision_as_timestamped_blocks(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """AC 1, completa: legacy ``completa.txt`` parity VERBATIM — one
    ``[YYYY-MM-DD HH:MM:SS] {text}\\n\\n`` block per 'full' revision,
    ascending; an EDIT of an already-✅ message is a second block."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1
    session_id = await _bound_session_id(batch_id)
    await _capture_ok(3601, 1, "✅ Aprobada CC: 4111 Status a")
    await capture.process_incoming(  # edited revision of the SAME message_id
        IncomingReply(
            message_id=3601,
            reply_to_msg_id=1,
            text="✅ Aprobada CC: 4111 Status b",
            edited=True,
        )
    )

    res = await http.get(f"/api/sessions/{session_id}/export?view=completa")
    assert res.status_code == 200, res.text
    _assert_txt_headers(res, gate["value"], session_id, "completa")
    full_rows = [r for r in await _response_rows(session_id) if r.kind == "full"]
    assert [r.text for r in full_rows] == [
        "✅ Aprobada CC: 4111 Status a",
        "✅ Aprobada CC: 4111 Status b",
    ]
    assert res.text == "".join(
        f"[{r.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {r.text}\n\n"
        for r in full_rows
    )


@pytest.mark.asyncio(loop_scope="session")
async def test_filtrada_con_response_counts_and_exports_only_ok(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """"Filtrada con response": ``responses_ok_total`` counts only the ✅
    revisions, and ``view=filtrada_completa`` exports only their full text —
    the ❌ stays in Completa alone."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno\ndos", gate["id"])
    await _drain()  # message_id 1 (uno) and 2 (dos)
    session_id = await _bound_session_id(batch_id)

    ok_text = "✅ Aprobada CC: 4111 Status a"
    rejected_text = "❌ Declinada"
    await _capture_ok(5001, 1, ok_text)
    await _capture_ok(5002, 2, rejected_text)

    # Detail: Completa has both 'full' revisions; the ok-total counts only ✅.
    body = (await http.get(f"/api/sessions/{session_id}")).json()
    assert (body["responses_total"], body["responses_ok_total"]) == (2, 1)

    # Export filtrada_completa: only the ✅ full text, as a timestamped block.
    res = await http.get(
        f"/api/sessions/{session_id}/export?view=filtrada_completa"
    )
    assert res.status_code == 200, res.text
    _assert_txt_headers(res, gate["value"], session_id, "filtrada_completa")
    ok_row = next(
        r
        for r in await _response_rows(session_id)
        if r.kind == "full" and r.status == "ok"
    )
    assert res.text == (
        f"[{ok_row.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {ok_text}\n\n"
    )
    assert rejected_text not in res.text


@pytest.mark.asyncio(loop_scope="session")
async def test_export_is_generated_on_the_fly_from_rows(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """AC 1 "no cache": a capture between two exports shows up in the second,
    and the response forbids caching in the HTTP contract (no-store)."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1
    session_id = await _bound_session_id(batch_id)
    await _capture_ok(3701, 1, "✅ Aprobada CC: 4111 Status a")

    first = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert (first.status_code, first.text) == (200, "4111\n")
    assert first.headers["Cache-Control"] == "no-store"

    await _capture_ok(3702, 1, "✅ Aprobada CC: 4222 Status b")
    second = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert (second.status_code, second.text) == (200, "4111\n4222\n")


@pytest.mark.asyncio(loop_scope="session")
async def test_export_works_during_live_batch_and_on_closed_session(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """AC 2: NO live-batch guard — export answers 200 while the lote is
    sending; and once another gate's batch deactivates the session, the now
    CLOSED session still exports the SAME content."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    session_id = await _bound_session_id(batch_id)

    # Live (sending, not drained) ⇒ 200, no guard.
    res = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert res.status_code == 200, res.text
    _assert_txt_headers(res, gate["value"], session_id, "filtrada")

    await _drain()  # message_id 1 — the batch completes
    await _capture_ok(3801, 1, "✅ Aprobada CC: 4111 Status a")
    res = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert (res.status_code, res.text) == (200, "4111\n")

    # Another gate's batch rotates the active session — ours is now Cerrada …
    other = await _create_other_gate(ctx, gate)
    second_batch = await _post_batch(http, "dos", other["id"])
    assert await _bound_session_id(second_batch) != session_id
    assert (await _get_session_row(session_id)).is_active is False

    # … and exports exactly the same.
    res = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert (res.status_code, res.text) == (200, "4111\n")


@pytest.mark.asyncio(loop_scope="session")
async def test_export_of_session_without_rows_is_an_empty_file(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Zero captures ⇒ 200 with an empty body on BOTH views (honest empty
    file — recorded decision: never a 404 that would conflate "no data" with
    "no session")."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = await _bound_session_id(batch_id)

    for view in ("completa", "filtrada"):
        res = await http.get(f"/api/sessions/{session_id}/export?view={view}")
        assert res.status_code == 200, res.text
        _assert_txt_headers(res, gate["value"], session_id, view)
        assert res.text == ""


@pytest.mark.asyncio(loop_scope="session")
async def test_export_rejects_invalid_view_with_422(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """``view`` outside the Literal ⇒ FastAPI validation 422 (the body is
    FastAPI's ``{detail}``, not the error contract — accepted project-wide)."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    session_id = await _bound_session_id(batch_id)

    res = await http.get(f"/api/sessions/{session_id}/export?view=otracosa")
    assert res.status_code == 422
    res = await http.get(f"/api/sessions/{session_id}/export")  # missing too
    assert res.status_code == 422


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

        # Three bad ids — A's id seen from B (the five verbs: continue since
        # 3.4, export since 3.5), an unknown id and an out-of-int4 id — all
        # 404 with the IDENTICAL body.
        res = await http_b.get(f"/api/sessions/{session_a}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.patch(
            f"/api/sessions/{session_a}", json={"name": "ajena"}
        )
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.delete(f"/api/sessions/{session_a}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.post(f"/api/sessions/{session_a}/continue")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.get(f"/api/sessions/{session_a}/export?view=completa")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        unknown = 2**31 - 1  # int4-max: valid bind, never a real id here
        res = await http_b.get(f"/api/sessions/{unknown}")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.post(f"/api/sessions/{unknown}/continue")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.get(f"/api/sessions/{unknown}/export?view=filtrada")
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
        res = await http_b.post(f"/api/sessions/{overflow}/continue")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)
        res = await http_b.get(f"/api/sessions/{overflow}/export?view=completa")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        # A, of course, still reaches their own session.
        res = await http_a.get(f"/api/sessions/{session_a}")
        assert res.status_code == 200
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})
