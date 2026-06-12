"""Story 3.6 cross-tenant support view tests: list + detail under
``/api/admin/tenants/{tenant_id}/sessions[/{session_id}]`` (the explicit
audited ``for_tenant(id)`` support path — AC 1/2), the ``audit_log`` rows
written fail-closed BEFORE data is served (AC 2), the client blocked at the
API with 403 ``forbidden`` (AC 3, server side — the middleware redirect is
UX, covered by the manual smoke), the tenant 404 trio (unknown id / non-client
tenant / out-of-int4 — existence never leaked, for owner AND admin actors),
the session 404 trio on the detail (unknown / another tenant's / overflow),
the honest empty list for a client without sessions (AC 4 — the copy "Este
cliente no tiene sesiones." is the frontend Table's) and the STRUCTURAL
read-only guarantee (PATCH/DELETE/continue under the admin prefix ⇒ 405:
the verbs do not exist).

Same idiom as test_sessions.py: real ASGI app against the dev Postgres,
self-seeding, self-cleaning, ``FakeGateway``; captures go DIRECT to
``capture.process_incoming`` and batches drain via ``send_worker.step()``.
The audit rows CASCADE with the target client's tenant in its teardown —
zero manual cleanup.

Run (from backend/, venv active):  pytest tests/test_support_view.py
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, send_worker
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import AuditLog, Batch, Response, User
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

TENANT_NOT_FOUND_BODY = {
    "code": "tenant_not_found",
    "message": "Ese cliente no existe.",
}
SESSION_NOT_FOUND_BODY = {
    "code": "session_not_found",
    "message": "Esa sesión no existe.",
}
FORBIDDEN_BODY = {
    "code": "forbidden",
    "message": "No tienes permiso para acceder a esto.",
}

# --- Local helpers (test modules don't import each other) --------------------


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    batch_id: int = res.json()["id"]
    return batch_id


async def _drain() -> None:
    """Run worker steps until the queue is empty (FakeGateway ids 1..n)."""
    while await send_worker.step():
        pass


async def _bound_session_id(batch_id: int) -> int:
    async with async_session_factory() as session:
        batch = await session.get(Batch, batch_id)
        assert batch is not None
        assert batch.capture_session_id is not None
        return batch.capture_session_id


async def _capture_ok(message_id: int, reply_to: int, text: str) -> None:
    await capture.process_incoming(
        IncomingReply(
            message_id=message_id, reply_to_msg_id=reply_to, text=text, edited=False
        )
    )


async def _response_rows(capture_session_id: int) -> list[Response]:
    async with async_session_factory() as session:
        stmt = (
            select(Response)
            .where(Response.capture_session_id == capture_session_id)
            .order_by(Response.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _audit_rows(tenant_id: int) -> list[AuditLog]:
    async with async_session_factory() as session:
        stmt = (
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant_id)
            .order_by(AuditLog.id)
        )
        return list((await session.execute(stmt)).scalars().all())


# --- List cross-tenant (AC 1) -------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_lists_client_sessions_with_email_and_exact_shape(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # FakeGateway → message_id 1
    session_id = await _bound_session_id(batch_id)
    await _capture_ok(6001, 1, "✅ Aprobada CC: 4111 Status a")
    await _capture_ok(6002, 1, "✅ Aprobada CC: 4222 Status b")

    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.get(f"/api/admin/tenants/{user.tenant_id}/sessions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tenant_id"] == user.tenant_id
    assert body["email"] == user.email
    assert body["total"] == 1

    (item,) = body["items"]
    created_at = item.pop("created_at")
    assert created_at  # SessionOut shape, exact
    assert item == {
        "id": session_id,
        "name": None,
        "gate_value": gate["value"],
        "gate_name": gate["name"],
        "is_active": True,
    }


# --- Detail cross-tenant (AC 1) -------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_reads_session_detail_with_exact_rows(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()  # message_id 1
    session_id = await _bound_session_id(batch_id)
    text = "✅ Aprobada CC: 4111 Status aprobada"
    await _capture_ok(6101, 1, text)

    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    res = await admin_client.get(
        f"/api/admin/tenants/{user.tenant_id}/sessions/{session_id}"
    )
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
        "message_id": 6101,
        "status": "ok",
        "text": text,
    }
    (cc_row,) = body["cc"]
    # Truncated at the literal "Status" (intentional parsing, 🔒).
    assert cc_row == {"id": cc_db.id, "text": "4111"}


# --- Audit trail (AC 2) ----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_every_support_read_writes_one_audit_row(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """One ``audit_log`` row per support read: the owner's list and the
    admin's detail each leave their row, with the TARGET tenant, the actor and
    (detail only) the capture session id. Rows CASCADE with the client's
    tenant on teardown — no manual cleanup."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = await _bound_session_id(batch_id)

    owner: User = ctx["owner"]  # type: ignore[assignment]
    admin: User = ctx["admin"]  # type: ignore[assignment]
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    res = await owner_client.get(f"/api/admin/tenants/{user.tenant_id}/sessions")
    assert res.status_code == 200, res.text
    res = await admin_client.get(
        f"/api/admin/tenants/{user.tenant_id}/sessions/{session_id}"
    )
    assert res.status_code == 200, res.text

    rows = await _audit_rows(user.tenant_id)
    assert [
        (r.actor_user_id, r.action, r.capture_session_id) for r in rows
    ] == [
        (owner.id, "support_sessions_list", None),
        (admin.id, "support_session_detail", session_id),
    ]
    assert all(r.tenant_id == user.tenant_id for r in rows)
    assert all(r.created_at is not None for r in rows)


# --- Client blocked at the API (AC 3, server side) -------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_client_gets_403_forbidden_on_both_support_routes(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """``require_admin_or_owner`` is the real security boundary behind the
    middleware redirect: a client probing the support routes (even their OWN
    tenant id) gets the exact 403 body and no audit row is written."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    session_id = await _bound_session_id(batch_id)

    res = await http.get(f"/api/admin/tenants/{user.tenant_id}/sessions")
    assert (res.status_code, res.json()) == (403, FORBIDDEN_BODY)
    res = await http.get(
        f"/api/admin/tenants/{user.tenant_id}/sessions/{session_id}"
    )
    assert (res.status_code, res.json()) == (403, FORBIDDEN_BODY)

    assert await _audit_rows(user.tenant_id) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_cross_site_navigation_cannot_mint_audit_rows(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """The audit trail is not CSRF-forgeable: the session cookie is
    SameSite=Lax, so a cross-site TOP-LEVEL navigation would still carry it —
    both support GETs 403 on ``Sec-Fetch-Site`` values other than
    ``same-origin`` BEFORE writing the audit row, while the SPA's own
    same-origin fetch (and header-less non-browser clients) pass."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    session_id = await _bound_session_id(batch_id)

    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    list_url = f"/api/admin/tenants/{user.tenant_id}/sessions"
    detail_url = f"{list_url}/{session_id}"

    for site in ("cross-site", "same-site", "none"):
        for url in (list_url, detail_url):
            res = await owner_client.get(url, headers={"sec-fetch-site": site})
            assert (res.status_code, res.json()) == (403, FORBIDDEN_BODY), site
    assert await _audit_rows(user.tenant_id) == []

    for url in (list_url, detail_url):
        res = await owner_client.get(url, headers={"sec-fetch-site": "same-origin"})
        assert res.status_code == 200, res.text
    assert len(await _audit_rows(user.tenant_id)) == 2


# --- Tenant 404 trio (AC 2 — existence never leaked) ------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_tenant_not_found_is_identical_for_unknown_nonclient_and_overflow(
    ctx: dict[str, object],
    fake_gateway: FakeGateway,
) -> None:
    """Unknown tenant id, the OWNER'S tenant (exists, but its user is not a
    client) and an out-of-int4 id answer the IDENTICAL 404 — for the owner
    AND the admin as actors, on both GET routes."""
    owner: User = ctx["owner"]  # type: ignore[assignment]
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    unknown = 2**31 - 1  # int4-max: valid bind, never a real id here
    non_client = owner.tenant_id  # exists, but holds no 'client' user
    overflow = 2**31  # out of int4 — guarded before it can hit asyncpg

    for actor_client in (owner_client, admin_client):
        for tenant_id in (unknown, non_client, overflow):
            res = await actor_client.get(f"/api/admin/tenants/{tenant_id}/sessions")
            assert (res.status_code, res.json()) == (404, TENANT_NOT_FOUND_BODY)
            res = await actor_client.get(
                f"/api/admin/tenants/{tenant_id}/sessions/1"
            )
            assert (res.status_code, res.json()) == (404, TENANT_NOT_FOUND_BODY)


# --- Session 404 trio on the detail ------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_session_not_found_is_identical_for_unknown_foreign_and_overflow(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Under the FIRST client's tenant: an unknown session id, ANOTHER
    client's session id and an out-of-int4 id all 404 with the identical
    session body — the support path never mixes tenants."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    await _drain()
    await _bound_session_id(batch_id)

    # A second client with their own batch+session.
    user_b = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http_b, user_b.email)
        batch_b = await _post_batch(http_b, "dos", gate["id"])
        session_b = await _bound_session_id(batch_b)

        owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
        unknown = 2**31 - 1
        overflow = 2**31
        for session_id in (unknown, session_b, overflow):
            res = await owner_client.get(
                f"/api/admin/tenants/{user.tenant_id}/sessions/{session_id}"
            )
            assert (res.status_code, res.json()) == (404, SESSION_NOT_FOUND_BODY)

        # Under ITS OWN tenant, B's session resolves fine.
        res = await owner_client.get(
            f"/api/admin/tenants/{user_b.tenant_id}/sessions/{session_b}"
        )
        assert res.status_code == 200, res.text
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})


# --- Empty list (AC 4) --------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_client_without_sessions_lists_empty(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
) -> None:
    """A freshly seeded client with no batches ⇒ honest empty 200 (the copy
    "Este cliente no tiene sesiones." is painted by the frontend Table)."""
    _, user = client_user
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    res = await owner_client.get(f"/api/admin/tenants/{user.tenant_id}/sessions")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["items"], body["total"]) == ([], 0)
    assert body["email"] == user.email


# --- Read-only is structural (AC 1) -------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_mutating_verbs_do_not_exist_under_the_support_prefix(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """No rename, no delete, no continue under ``/api/admin/tenants/...`` —
    FastAPI's structural answer, same treatment as the validation 422s:
    PATCH/DELETE hit the GET-only path ⇒ 405 (Method Not Allowed); the
    continue sub-path doesn't exist AT ALL ⇒ 404 (no partial route match —
    even more absent than a wrong verb)."""
    http, user = client_user
    batch_id = await _post_batch(http, "uno", gate["id"])
    session_id = await _bound_session_id(batch_id)

    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    base = f"/api/admin/tenants/{user.tenant_id}/sessions/{session_id}"

    res = await owner_client.patch(base, json={"name": "soporte"})
    assert res.status_code == 405
    res = await owner_client.delete(base)
    assert res.status_code == 405
    res = await owner_client.post(f"{base}/continue")
    assert res.status_code == 404
