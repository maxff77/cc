"""Integration tests for the credential vault (``/api/credentials``).

Same harness as the other API modules: drives the real ASGI app (httpx
``ASGITransport``) against the dev Postgres, self-seeding with unique emails,
self-cleaning on teardown (credentials cascade with the tenant via FK CASCADE on
``tenant_id``, so no manual row cleanup is needed).

Locks the invariants the spec freezes:
- ``password`` NEVER leaves the DB (absent from POST and GET responses).
- email format is validated in-handler → bad address is 400 ``invalid_credential``.
- DELETE is tenant-scoped: a foreign / unknown / oversized id all 404 IDENTICALLY
  (no existence leak).

Run (from backend/, venv active):  pytest tests/test_credentials.py
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.conftest import cleanup_users, login, seed_user

_PG_INT_MAX = 2**31 - 1


@pytest_asyncio.fixture(loop_scope="session")
async def client_a() -> AsyncIterator[AsyncClient]:
    """A logged-in client tenant (valid plan), self-cleaning."""
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    yield http
    await http.aclose()
    await cleanup_users({user.email})


@pytest_asyncio.fixture(loop_scope="session")
async def client_b() -> AsyncIterator[AsyncClient]:
    """A SECOND, distinct client tenant — for cross-tenant isolation checks."""
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    yield http
    await http.aclose()
    await cleanup_users({user.email})


# --- Store → list (password never returned) ---------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_store_then_list_omits_password(client_a: AsyncClient) -> None:
    res = await client_a.post(
        "/api/credentials",
        json={"email": "saved@example.com", "password": "secreto123"},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["email"] == "saved@example.com"
    assert body["used"] is False
    assert "password" not in body

    listed = await client_a.get("/api/credentials")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert any(r["id"] == body["id"] for r in rows)
    assert all("password" not in r for r in rows)


# --- Email validation -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("bad", ["sin-arroba", "a@b", "a@b.", "@b.com", "a b@c.com", ""])
async def test_bad_email_is_400_without_leaking(
    client_a: AsyncClient, bad: str
) -> None:
    res = await client_a.post(
        "/api/credentials", json={"email": bad, "password": "secreto123"}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_credential"
    # value-free: the rejected secret never surfaces in the error body.
    assert "secreto123" not in res.text


# --- DELETE: tenant-scoped --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_removes_own_entry(client_a: AsyncClient) -> None:
    created = await client_a.post(
        "/api/credentials",
        json={"email": "todelete@example.com", "password": "secreto123"},
    )
    assert created.status_code == 201, created.text
    cred_id = created.json()["id"]

    gone = await client_a.delete(f"/api/credentials/{cred_id}")
    assert gone.status_code == 204, gone.text

    listed = await client_a.get("/api/credentials")
    assert all(r["id"] != cred_id for r in listed.json())


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_foreign_or_unknown_is_404(
    client_a: AsyncClient, client_b: AsyncClient
) -> None:
    # A creates a row; B must NOT be able to delete it (and gets the SAME 404 as
    # a truly unknown id — no existence leak).
    created = await client_a.post(
        "/api/credentials",
        json={"email": "owned-by-a@example.com", "password": "secreto123"},
    )
    a_id = created.json()["id"]

    foreign = await client_b.delete(f"/api/credentials/{a_id}")
    assert foreign.status_code == 404, foreign.text
    assert foreign.json()["code"] == "credential_not_found"

    unknown = await client_b.delete("/api/credentials/2147000000")
    assert unknown.status_code == 404, unknown.text

    oversized = await client_b.delete(f"/api/credentials/{_PG_INT_MAX + 1}")
    assert oversized.status_code == 404, oversized.text

    # A's row survived B's attempt.
    listed = await client_a.get("/api/credentials")
    assert any(r["id"] == a_id for r in listed.json())
