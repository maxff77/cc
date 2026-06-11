"""Integration tests for plan expiry + lockout (Story 1.4).

Drives the real ASGI app (httpx ``ASGITransport``) against the dev Postgres,
mirroring ``test_admin_users.py``: self-seeding with unique emails, direct DB
mutation for state setup, self-cleaning on teardown, all pinned to
``loop_scope="session"`` so they share the async engine pool.

Run (from backend/, venv active):  pytest tests/test_plan_expiry.py
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.config import settings
from app.db.base import async_session_factory
from app.db.models import Tenant, User
from app.db.repos import users as users_repo
from app.main import app
from app.services.auth import hash_password
from httpx import ASGITransport, AsyncClient

PASSWORD = "seed-pass-123"  # noqa: S105 — throwaway test credential


def _email(role: str) -> str:
    return f"test-expiry-{role}-{uuid.uuid4().hex[:8]}@cc.test"


async def _seed(role: str, *, expires_at: datetime | None) -> User:
    """Create a fresh user (own tenant) directly, bypassing the API."""
    async with async_session_factory() as session:
        tenant = await users_repo.create_tenant(session, name=f"t-{uuid.uuid4().hex}")
        user = await users_repo.create_user(
            session,
            tenant_id=tenant.id,
            email=_email(role),
            password_hash=hash_password(PASSWORD),
            role=role,
            expires_at=expires_at,
        )
        await session.commit()
        return user


async def _set_expires_at(user_id: int, when: datetime) -> None:
    """Move a user's plan expiry directly in the DB (simulates time passing)."""
    async with async_session_factory() as session:
        row = await session.get(User, user_id)
        assert row is not None
        row.expires_at = when
        await session.commit()


async def _login(client: AsyncClient, email: str) -> None:
    res = await client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text


async def _cleanup(emails: set[str]) -> None:
    async with async_session_factory() as session:
        for email in emails:
            user = await users_repo.get_by_email(session, email)
            if user is None:
                continue
            tenant = await session.get(Tenant, user.tenant_id)
            await session.delete(user)
            if tenant is not None:
                await session.delete(tenant)
        await session.commit()


def _client() -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )


@pytest_asyncio.fixture(loop_scope="session")
async def created() -> AsyncIterator[set[str]]:
    """Track seeded emails and delete them (plus tenants) on teardown."""
    emails: set[str] = set()
    yield emails
    await _cleanup(emails)


@pytest.mark.asyncio(loop_scope="session")
async def test_login_as_expired_client_is_rejected(created: set[str]) -> None:
    """A client whose plan already lapsed cannot log in; no cookie is set."""
    past = datetime.now(UTC) - timedelta(days=1)
    user = await _seed("client", expires_at=past)
    created.add(user.email)

    async with _client() as client:
        res = await client.post(
            "/api/auth/login",
            json={"email": user.email, "password": PASSWORD},
        )
        assert res.status_code == 403, res.text
        assert res.json()["code"] == "plan_expired"
        assert settings.session_cookie_name not in client.cookies


@pytest.mark.asyncio(loop_scope="session")
async def test_mid_session_expiry_cuts_access_and_revokes(
    created: set[str],
) -> None:
    """AC3: expiry mid-session cuts the next request and revokes the session.

    The first request after expiry returns 403 plan_expired; the SAME cookie on
    a second request returns 401 not_authenticated (the row was revoked).
    """
    future = datetime.now(UTC) + timedelta(days=30)
    user = await _seed("client", expires_at=future)
    created.add(user.email)

    async with _client() as client:
        await _login(client, user.email)
        # Plan lapses while the session is live.
        await _set_expires_at(user.id, datetime.now(UTC) - timedelta(seconds=1))

        first = await client.get("/api/auth/me")
        assert first.status_code == 403, first.text
        assert first.json()["code"] == "plan_expired"

        second = await client.get("/api/auth/me")
        assert second.status_code == 401, second.text
        assert second.json()["code"] == "not_authenticated"


@pytest.mark.asyncio(loop_scope="session")
async def test_active_client_works_normally(created: set[str]) -> None:
    """A client with a future expiry logs in and reads /me normally."""
    future = datetime.now(UTC) + timedelta(days=30)
    user = await _seed("client", expires_at=future)
    created.add(user.email)

    async with _client() as client:
        await _login(client, user.email)
        res = await client.get("/api/auth/me")
        assert res.status_code == 200, res.text
        assert res.json()["role"] == "client"


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_staff_never_expires(created: set[str], role: str) -> None:
    """owner/admin carry no plan (expires_at = None) → never expired."""
    user = await _seed(role, expires_at=None)
    created.add(user.email)

    async with _client() as client:
        await _login(client, user.email)
        res = await client.get("/api/auth/me")
        assert res.status_code == 200, res.text
        assert res.json()["role"] == role
