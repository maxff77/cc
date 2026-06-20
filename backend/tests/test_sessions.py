"""Sessions router tests (sessionless cockpit, PR-1).

The client-facing session lifecycle (list / detail / rename / continue / new /
delete + the old per-id clear-declined) is GONE — those tests are pruned. What
remains under ``/api/sessions``:

- the per-session ``GET /{id}/export`` (admin / PR-2, CUTOFF-AGNOSTIC full
  history) — legacy-parity bodies asserted EXACT (filtrada: one datum per line +
  final newline; completa: ``[ts] {text}`` blocks per revision), the
  Content-Disposition filename from the gate slug, on-the-fly generation (no
  cache), works during a live batch, empty body for a session with no rows, and
  422 on an invalid view;
- ``session_to_out`` schema coverage (still imported by the admin support view);
- tenant isolation: a FOREIGN/unknown/out-of-int4 id on ``GET /{id}/export``
  and a foreign-tenant ``POST /api/sessions/clear`` both 404 with the IDENTICAL
  body (``tenant_id`` only from the session — never the path).

Same idiom as test_attribution.py: real ASGI app against the dev Postgres,
self-seeding, self-cleaning, ``FakeGateway``; captures go DIRECT to
``capture.process_incoming`` and batches drain via ``send_worker.step()``.

Run (from backend/, venv active):  pytest tests/test_sessions.py
"""

import re
from datetime import UTC, datetime, timedelta

import pytest
from app.api.sessions import session_to_out
from app.core import capture, send_worker
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Batch, CaptureSession, Response, User
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

NOT_FOUND_BODY = {"code": "session_not_found", "message": "Esa sesión no existe."}

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


# --- session_to_out schema coverage (still used by the admin support view) ---


@pytest.mark.asyncio(loop_scope="session")
async def test_session_to_out_maps_snapshot_fields_and_active_flag(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """``session_to_out`` (imported by ``api/admin.py``) maps the perpetual
    session to the client-visible shape: the gate SNAPSHOTS (display value +
    name, never the real ``value``), the ``is_active`` flag and ``created_at``."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    session_id = await _bound_session_id(batch_id)
    row = await _get_session_row(session_id)

    out = session_to_out(row)
    assert out.id == session_id
    assert out.name is None
    assert out.gate_display_value == gate["display_value"]
    assert out.gate_name == gate["name"]
    assert out.is_active is True
    assert out.created_at == row.created_at


# --- Export (Story 3.5; admin / PR-2 per-id export, CUTOFF-AGNOSTIC) ---------


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
    _assert_txt_headers(res, gate["display_value"], session_id, "filtrada")
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
    _assert_txt_headers(res, gate["display_value"], session_id, "completa")
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
async def test_filtrada_con_response_exports_only_ok(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """``view=filtrada_completa`` exports only the ✅ revisions' full text —
    the ❌ stays out."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno\ndos", gate["id"])
    await _drain()  # message_id 1 (uno) and 2 (dos)
    session_id = await _bound_session_id(batch_id)

    ok_text = "✅ Aprobada CC: 4111 Status a"
    rejected_text = "❌ Declinada"
    await _capture_ok(5001, 1, ok_text)
    await _capture_ok(5002, 2, rejected_text)

    res = await http.get(
        f"/api/sessions/{session_id}/export?view=filtrada_completa"
    )
    assert res.status_code == 200, res.text
    _assert_txt_headers(res, gate["display_value"], session_id, "filtrada_completa")
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
async def test_export_works_during_live_batch(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """AC 2: NO live-batch guard — export answers 200 while the lote is
    sending; after the drain a capture exports too."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    session_id = await _bound_session_id(batch_id)

    # Live (sending, not drained) ⇒ 200, no guard.
    res = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert res.status_code == 200, res.text
    _assert_txt_headers(res, gate["display_value"], session_id, "filtrada")

    await _drain()  # message_id 1 — the batch completes
    await _capture_ok(3801, 1, "✅ Aprobada CC: 4111 Status a")
    res = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert (res.status_code, res.text) == (200, "4111\n")


@pytest.mark.asyncio(loop_scope="session")
async def test_export_is_cutoff_agnostic_full_history(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """The per-id export is the admin / PR-2 path: it ignores the cockpit
    ``cleared_response_id`` and dumps the FULL history even after a Limpiar
    (whereas the cockpit ``GET /export`` would be empty)."""
    http, _ = client_user
    batch_id = await _post_batch(http, "uno\ndos", gate["id"])
    await _drain()  # message_id 1..2
    session_id = await _bound_session_id(batch_id)
    await _capture_ok(4101, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture_ok(4102, 2, "✅ Aprobada CC: 4222 Status b")

    # Stamp a cutoff (a Limpiar) past both rows.
    res = await http.post("/api/sessions/clear")
    assert res.status_code == 200, res.text
    assert (await _get_session_row(session_id)).cleared_response_id is not None

    # The cockpit export respects the cutoff ⇒ empty …
    cockpit = await http.get("/api/sessions/export?view=filtrada")
    assert (cockpit.status_code, cockpit.text) == (200, "")
    # … but the per-id admin/PR-2 export is cutoff-agnostic ⇒ full history.
    perid = await http.get(f"/api/sessions/{session_id}/export?view=filtrada")
    assert (perid.status_code, perid.text) == (200, "4111\n4222\n")


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
        _assert_txt_headers(res, gate["display_value"], session_id, view)
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


# --- 404 never leaks existence (AC 8) ----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_not_found_is_identical_for_unknown_foreign_and_overflow_ids(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """The SURVIVING session surfaces (per-id ``GET /{id}/export`` and the
    cockpit ``POST /clear``) never leak existence: a foreign/unknown/out-of-int4
    id on the export and a foreign-tenant clear both 404 with the IDENTICAL
    body (``tenant_id`` only from the session)."""
    http_a, _ = client_user
    batch_id = await _post_batch(http_a, "uno", gate["id"])
    session_a = await _bound_session_id(batch_id)

    user_b = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http_b, user_b.email)

        # B has never sent a batch ⇒ no perpetual session ⇒ clear 404s.
        res = await http_b.post("/api/sessions/clear")
        assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        # A's session id seen from B (foreign), an unknown id and an
        # out-of-int4 id on the per-id export — all 404, IDENTICAL body.
        for bad in (session_a, 2**31 - 1, 2**31):
            res = await http_b.get(f"/api/sessions/{bad}/export?view=completa")
            assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        # A, of course, still exports their own session.
        res = await http_a.get(f"/api/sessions/{session_a}/export?view=completa")
        assert res.status_code == 200
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})
