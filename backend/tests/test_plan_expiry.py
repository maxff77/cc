"""Integration tests for plan expiry + lockout (Story 1.4).

Drives the real ASGI app (httpx ``ASGITransport``) against the dev Postgres,
mirroring ``test_admin_users.py``: self-seeding with unique emails, direct DB
mutation for state setup, self-cleaning on teardown, all pinned to
``loop_scope="session"`` so they share the async engine pool. Seed/login/
cleanup helpers are shared via ``tests.conftest``.

Run (from backend/, venv active):  pytest tests/test_plan_expiry.py
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.config import settings
from app.db.base import async_session_factory
from app.db.models import User
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.conftest import PASSWORD, cleanup_users, login, seed_user


async def _seed(role: str, *, expires_at: datetime | None) -> User:
    return await seed_user(role, expires_at=expires_at, email_prefix="test-expiry")


async def _set_expires_at(user_id: int, when: datetime) -> None:
    """Move a user's plan expiry directly in the DB (simulates time passing)."""
    async with async_session_factory() as session:
        row = await session.get(User, user_id)
        assert row is not None
        row.expires_at = when
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
    await cleanup_users(emails)


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
async def test_mid_session_expiry_is_repeatable_and_keeps_session(
    created: set[str],
) -> None:
    """AC3: expiry mid-session cuts access on EVERY request, NOT revoking.

    plan_expired is a repeatable 403 (mirrors must_change_password): the session
    survives so an admin's renewal can auto-recover the open /expired tab. The
    SAME cookie on a second request must STILL get 403 plan_expired — never the
    401 a revoked session would give.
    """
    future = datetime.now(UTC) + timedelta(days=30)
    user = await _seed("client", expires_at=future)
    created.add(user.email)

    async with _client() as client:
        await login(client, user.email)
        # Plan lapses while the session is live.
        await _set_expires_at(user.id, datetime.now(UTC) - timedelta(seconds=1))

        first = await client.get("/api/auth/me")
        assert first.status_code == 403, first.text
        assert first.json()["code"] == "plan_expired"

        second = await client.get("/api/auth/me")
        assert second.status_code == 403, second.text
        assert second.json()["code"] == "plan_expired"


@pytest.mark.asyncio(loop_scope="session")
async def test_renewal_restores_access_on_same_session(
    created: set[str],
) -> None:
    """Auto-recovery: renewing the plan revives the SAME (un-revoked) session.

    The expired client's tab polls /me; the instant an admin pushes expires_at
    back into the future, that same cookie answers 200 again — no re-login. This
    is the whole point of not revoking on expiry.
    """
    future = datetime.now(UTC) + timedelta(days=30)
    user = await _seed("client", expires_at=future)
    created.add(user.email)

    async with _client() as client:
        await login(client, user.email)
        # Plan lapses, then is renewed (admin sets a future expiry).
        await _set_expires_at(user.id, datetime.now(UTC) - timedelta(seconds=1))
        expired = await client.get("/api/auth/me")
        assert expired.status_code == 403, expired.text
        assert expired.json()["code"] == "plan_expired"

        await _set_expires_at(user.id, datetime.now(UTC) + timedelta(days=30))

        recovered = await client.get("/api/auth/me")
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["role"] == "client"


@pytest.mark.asyncio(loop_scope="session")
async def test_active_client_works_normally(created: set[str]) -> None:
    """A client with a future expiry logs in and reads /me normally."""
    future = datetime.now(UTC) + timedelta(days=30)
    user = await _seed("client", expires_at=future)
    created.add(user.email)

    async with _client() as client:
        await login(client, user.email)
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
        await login(client, user.email)
        res = await client.get("/api/auth/me")
        assert res.status_code == 200, res.text
        assert res.json()["role"] == role
