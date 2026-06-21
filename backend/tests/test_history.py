"""Client history (PR-2) tests: the tenant's approved-✅ captured responses
grouped by gate, INDEPENDENT of the cockpit Limpiar cutoff (PR-1).

Asserts the load-bearing invariants of the spec:
- ``GET /api/history`` groups only the messages whose LATEST ``kind='full'``
  revision is ✅ by the batch's gate snapshot (``gate_name`` / display_value):
  a ❌-latest message and a ⏳-only message are excluded; each ✅ message carries
  its extracted ``cc``; gates ordered by most-recent activity, items newest-first.
- the history IGNORES the Limpiar cutoff: a tenant that pressed
  ``POST /api/sessions/clear`` still sees every ✅ message.
- ``DELETE /api/history/response/{id}`` deletes a message's full+cc rows and
  leaves the others; a foreign/unknown/oversized id 404s IDENTICALLY (no leak).
- ``DELETE /api/history/gate?name=`` scopes to that one gate; an unknown name is
  a 200 ``{deleted: 0}``.
- ``DELETE /api/history`` wipes ONLY the acting tenant's rows.
- 🔒 ``gate_value`` NEVER appears in any GET payload.
- the deletes touch ONLY ``responses`` rows — the batch survives.

Same idiom as test_clear_declined.py: real ASGI app against the dev Postgres,
captures go DIRECT to ``capture.process_incoming``, batches drain via
``send_worker.step``.

Run (from backend/, venv active):  pytest tests/test_history.py
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, send_worker
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Batch, Response, SendLog, User
from app.db.repos import send_log as send_log_repo
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

NOT_FOUND_BODY = {
    "code": "history_response_not_found",
    "message": "Esa respuesta no existe.",
}
_PG_INT_MAX = 2**31 - 1


# --- Local helpers (mirrors test_clear_declined.py) --------------------------


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


async def _capture(
    message_id: int, reply_to: int, text: str, *, edited: bool = False
) -> None:
    await capture.process_incoming(
        IncomingReply(
            message_id=message_id,
            reply_to_msg_id=reply_to,
            text=text,
            edited=edited,
        )
    )


async def _create_other_gate(http_owner: AsyncClient, gate: dict) -> dict:
    """A second active gate in the SAME category (covered by the gate fixture's
    category-wide cleanup)."""
    res = await http_owner.post(
        "/api/admin/gates",
        json={
            "value": f".h{uuid.uuid4().hex[:6]}",
            "name": "Otro Historial",
            "display_value": "Otro Visible Hist",
            "category_id": gate["category_id"],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _row_count_for_tenant(tenant_id: int) -> int:
    """Physical ``responses`` rows of a tenant (no cutoff) — the row CENSUS."""
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Response)
                .where(Response.tenant_id == tenant_id)
            )
        ).scalar_one()


async def _batch_count_for_tenant(tenant_id: int) -> int:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(Batch)
                .where(Batch.tenant_id == tenant_id)
            )
        ).scalar_one()


def _assert_no_gate_value(payload: object) -> None:
    """🔒 Recursively assert no ``gate_value`` key anywhere in a JSON payload."""
    if isinstance(payload, dict):
        assert "gate_value" not in payload, f"gate_value leaked: {payload!r}"
        for v in payload.values():
            _assert_no_gate_value(v)
    elif isinstance(payload, list):
        for v in payload:
            _assert_no_gate_value(v)


# --- GET: only ✅-latest, grouped by gate, ⏳/❌-latest excluded, cc present ---


@pytest.mark.asyncio(loop_scope="session")
async def test_history_groups_only_approved_latest_by_gate(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Captures of ✅, ❌-latest, and ⏳-only across gates A and B: only the
    messages whose LATEST full revision is ✅ appear, grouped by gate, each with
    its cc; the ❌-latest and the ⏳-only message are excluded. The newest gate
    (B) leads (most-recent activity); never any gate_value."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    http, _ = client_user

    # Gate A: 4 lines → message_id 1..4. One pure ✅, one ✅→❌ (latest ❌),
    # one ⏳-only (writes nothing), one ❌-only.
    await _post_batch(http, "a1\na2\na3\na4", gate["id"])
    await _drain()
    await _capture(1, 1, "✅ Aprobada CC: 4111 Status x")  # stays ✅
    await _capture(2, 2, "✅ Aprobada CC: 4222 Status y")  # then edited ❌:
    await _capture(2, 2, "❌ Declinada", edited=True)  # latest ❌ ⇒ excluded
    await _capture(3, 3, "⏳ procesando")  # ⏳-only ⇒ no row ⇒ excluded
    await _capture(4, 4, "❌ Declinada")  # never ✅ ⇒ excluded

    # Gate B (created AFTER A's captures so it is the most recent activity):
    # one ✅.
    gate_b = await _create_other_gate(owner_client, gate)
    await _post_batch(http, "b1", gate_b["id"])
    await _drain()  # message_id 5
    await _capture(5, 5, "✅ Aprobada CC: 5111 Status z")

    res = await http.get("/api/history")
    assert res.status_code == 200, res.text
    body = res.json()
    _assert_no_gate_value(body)

    gates = body["gates"]
    assert [g["name"] for g in gates] == [gate_b["name"], gate["name"]]
    assert [g["display_value"] for g in gates] == [
        gate_b["display_value"],
        gate["display_value"],
    ]

    # Gate B: just the one ✅, with its cc.
    b = gates[0]
    assert b["count"] == 1
    assert [i["text"] for i in b["items"]] == ["✅ Aprobada CC: 5111 Status z"]
    assert b["items"][0]["cc"] == ["5111"]
    assert "captured_at" in b["items"][0] and "id" in b["items"][0]

    # Gate A: ONLY message_id 1's ✅ survives (2 flipped to ❌, 3 was ⏳, 4 ❌).
    a = gates[1]
    assert a["count"] == 1
    assert [i["text"] for i in a["items"]] == ["✅ Aprobada CC: 4111 Status x"]
    assert a["items"][0]["cc"] == ["4111"]


@pytest.mark.asyncio(loop_scope="session")
async def test_history_empty_when_no_approved(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """No ✅ capture ⇒ ``{gates: []}`` (a ❌-only batch yields no history)."""
    http, _ = client_user
    await _post_batch(http, "a", gate["id"])
    await _drain()
    await _capture(1, 1, "❌ Declinada")

    res = await http.get("/api/history")
    assert res.status_code == 200, res.text
    assert res.json() == {"gates": []}


# --- GET ignores the Limpiar cutoff ------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_history_ignores_limpiar_cutoff(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A tenant that pressed Limpiar (cutoff stamped) STILL sees every ✅ in its
    history — the cutoff is a cockpit DISPLAY concern, never applied here."""
    http, _ = client_user
    await _post_batch(http, "a\nb", gate["id"])
    await _drain()  # message_id 1..2
    await _capture(1, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(2, 2, "✅ Aprobada CC: 4222 Status b")

    # Press Limpiar: the cockpit live view goes empty …
    res = await http.post("/api/sessions/clear")
    assert res.status_code == 200, res.text

    # … but the history still returns BOTH ✅ messages.
    res = await http.get("/api/history")
    assert res.status_code == 200, res.text
    gates = res.json()["gates"]
    assert len(gates) == 1
    assert gates[0]["count"] == 2
    assert sorted(i["cc"][0] for i in gates[0]["items"]) == ["4111", "4222"]


# --- DELETE one message (full + cc); foreign/unknown id 404s identically ------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_one_removes_full_and_cc_others_intact(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """DELETE /response/{id} removes that message's full revisions + cc and
    leaves the others; the batch row survives (only responses deleted)."""
    http, user = client_user
    await _post_batch(http, "a\nb", gate["id"])
    await _drain()  # message_id 1..2
    await _capture(1, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(2, 2, "✅ Aprobada CC: 4222 Status b")

    res = await http.get("/api/history")
    items = res.json()["gates"][0]["items"]
    assert len(items) == 2
    # message 1 ("4111") is the older ⇒ last in the newest-first list.
    target = next(i for i in items if i["cc"] == ["4111"])

    rows_before = await _row_count_for_tenant(user.tenant_id)
    batches_before = await _batch_count_for_tenant(user.tenant_id)

    res = await http.delete(f"/api/history/response/{target['id']}")
    assert res.status_code == 200, res.text
    # message 1 had 1 full + 1 cc row.
    assert res.json() == {"deleted": 2}

    # The other message survives; total dropped by exactly the deleted rows.
    res = await http.get("/api/history")
    remaining = res.json()["gates"][0]["items"]
    assert [i["cc"] for i in remaining] == [["4222"]]
    assert await _row_count_for_tenant(user.tenant_id) == rows_before - 2
    # Only responses deleted — the batch is untouched.
    assert await _batch_count_for_tenant(user.tenant_id) == batches_before


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_one_404s_for_foreign_unknown_and_oversized_id(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A foreign tenant's response id, an unknown id, and an out-of-int4 id ALL
    404 with the IDENTICAL body (no existence leak), and the foreign row is NOT
    deleted."""
    http, _ = client_user

    # A second tenant captures a ✅; tenant A learns its response id only via a
    # direct DB read (it can never see it through its OWN /api/history).
    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    other_http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(other_http, other.email)
        await _post_batch(other_http, "x", gate["id"])
        await _drain()  # message_id 1
        await _capture(1, 1, "✅ Aprobada CC: 9999 Status x")
        foreign_id = (
            await other_http.get("/api/history")
        ).json()["gates"][0]["items"][0]["id"]

        # Tenant A: every shape of "not yours" answers IDENTICALLY.
        for bad in (foreign_id, 999_999_999, _PG_INT_MAX + 1):
            res = await http.delete(f"/api/history/response/{bad}")
            assert (res.status_code, res.json()) == (404, NOT_FOUND_BODY)

        # The foreign tenant's ✅ is still there (the 404 deleted nothing).
        res = await other_http.get("/api/history")
        assert res.json()["gates"][0]["items"][0]["id"] == foreign_id
    finally:
        await other_http.aclose()
        await cleanup_users({other.email})


# --- DELETE by gate: scoped to one gate --------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_by_gate_scopes_to_one_gate(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """DELETE /gate?name=A deletes only gate-A responses; gate B and the
    "Sin gate" group remain. An unknown name is a 200 {deleted: 0}."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    http, user = client_user
    gate_b = await _create_other_gate(owner_client, gate)

    # Gate A: one ✅. Gate B: one ✅.
    await _post_batch(http, "a", gate["id"])
    await _drain()  # message_id 1
    await _capture(1, 1, "✅ Aprobada CC: 4111 Status a")
    await _post_batch(http, "b", gate_b["id"])
    await _drain()  # message_id 2
    await _capture(2, 2, "✅ Aprobada CC: 4222 Status b")

    # A "Sin gate" message: capture a ✅, then SET-NULL its batch_id (mirrors a
    # batch the FK SET-NULL'd after cleanup).
    await _post_batch(http, "c", gate["id"])
    await _drain()  # message_id 3
    await _capture(3, 3, "✅ Aprobada CC: 4333 Status c")
    async with async_session_factory() as session:
        await session.execute(
            Response.__table__.update()
            .where(
                Response.tenant_id == user.tenant_id,
                Response.message_id == 3,
            )
            .values(batch_id=None)
        )
        await session.commit()

    # Sanity: three groups (A, B, then trailing "Sin gate").
    body = (await http.get("/api/history")).json()
    names = [g["name"] for g in body["gates"]]
    assert gate["name"] in names and gate_b["name"] in names and None in names
    sin_gate = next(g for g in body["gates"] if g["name"] is None)
    assert sin_gate["display_value"] == "Sin gate"
    assert body["gates"][-1]["name"] is None  # trailing

    # Unknown name ⇒ 200 {deleted: 0}, nothing removed.
    res = await http.delete("/api/history/gate", params={"name": "no-such-gate"})
    assert (res.status_code, res.json()) == (200, {"deleted": 0})

    # Delete gate A: only A's responses go (1 full + 1 cc = 2 rows).
    res = await http.delete("/api/history/gate", params={"name": gate["name"]})
    assert res.status_code == 200, res.text
    assert res.json() == {"deleted": 2}

    # Gate B and "Sin gate" survive; gate A is gone.
    body = (await http.get("/api/history")).json()
    remaining = {g["name"] for g in body["gates"]}
    assert gate["name"] not in remaining
    assert gate_b["name"] in remaining and None in remaining


# --- DELETE all: wipes ONLY the acting tenant --------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_all_wipes_only_acting_tenant(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """DELETE /api/history removes every responses row of the acting tenant and
    leaves another tenant's rows untouched."""
    http, user = client_user
    await _post_batch(http, "a\nb", gate["id"])
    await _drain()  # message_id 1..2
    await _capture(1, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture(2, 2, "✅ Aprobada CC: 4222 Status b")

    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    other_http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(other_http, other.email)
        await _post_batch(other_http, "x", gate["id"])
        await _drain()  # message_id 3
        await _capture(3, 3, "✅ Aprobada CC: 9999 Status x")

        before_self = await _row_count_for_tenant(user.tenant_id)
        before_other = await _row_count_for_tenant(other.tenant_id)
        assert before_self > 0 and before_other > 0

        res = await http.delete("/api/history")
        assert res.status_code == 200, res.text
        assert res.json() == {"deleted": before_self}

        # Acting tenant emptied; the other tenant fully intact.
        assert await _row_count_for_tenant(user.tenant_id) == 0
        assert (await http.get("/api/history")).json() == {"gates": []}
        assert await _row_count_for_tenant(other.tenant_id) == before_other
        other_body = (await other_http.get("/api/history")).json()
        assert other_body["gates"][0]["items"][0]["cc"] == ["9999"]
    finally:
        await other_http.aclose()
        await cleanup_users({other.email})


# --- DELETE tombstones send_log so the reconciler won't resurrect ------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_tombstones_send_log_against_reconciler(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Deleting a message from Historial tombstones its send_log row, so the
    reply reconciler's work-list (``awaiting_sent_keys``) keeps EXCLUDING it —
    otherwise the deleted ✅ would be re-fetched from Telegram and re-inserted
    within ~45s (the "I delete it and it comes back on refresh" bug)."""
    http, user = client_user
    await _post_batch(http, "a", gate["id"])
    await _drain()  # message_id 1
    await _capture(1, 1, "✅ Aprobada CC: 4111 Status a")

    within = datetime.now(UTC) - timedelta(hours=72)

    async with async_session_factory() as session:
        pairs = {
            (c, m)
            for c, m in (
                await session.execute(
                    select(SendLog.chat_id, SendLog.message_id).where(
                        SendLog.tenant_id == user.tenant_id,
                        SendLog.message_id.is_not(None),
                    )
                )
            ).all()
        }
        # Delivered send, and NOT awaiting while its ✅ response still exists.
        assert pairs
        awaiting = await send_log_repo.awaiting_sent_keys(session, within=within)
    assert not (pairs & awaiting)

    # Delete the ✅ message from Historial.
    item = (await http.get("/api/history")).json()["gates"][0]["items"][0]
    res = await http.delete(f"/api/history/response/{item['id']}")
    assert res.status_code == 200, res.text

    # The response row is gone, but the send_log row is tombstoned — the
    # reconciler still excludes the pair, so the delete is NOT undone.
    async with async_session_factory() as session:
        awaiting = await send_log_repo.awaiting_sent_keys(session, within=within)
    assert not (pairs & awaiting), "purged line reappeared in reconciler work-list"


# --- Auth: every endpoint requires a session ---------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_history_requires_authentication(
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Anonymous access to every history route is rejected (no tenant from the
    cookie ⇒ 401)."""
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        assert (await anon.get("/api/history")).status_code == 401
        assert (await anon.delete("/api/history/response/1")).status_code == 401
        assert (
            await anon.delete("/api/history/gate", params={"name": "x"})
        ).status_code == 401
        assert (await anon.delete("/api/history")).status_code == 401
    finally:
        await anon.aclose()
