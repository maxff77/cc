"""Integration tests for public self-registration (POST /api/auth/register).

Same harness as test_plan_expiry / test_admin_users: drives the real ASGI app
against the dev Postgres, self-seeds with unique emails, self-cleans on
teardown, pinned to ``loop_scope="session"``.

Run (from backend/, venv active):  pytest tests/test_register.py
"""

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.config import settings
from app.db.base import async_session_factory
from app.db.repos import tenants as tenants_repo
from app.db.repos import users as users_repo
from app.main import app
from app.services import auth as auth_service
from httpx import ASGITransport, AsyncClient

from tests.conftest import PASSWORD, cleanup_users, unique_email


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture(loop_scope="session")
async def created() -> AsyncIterator[set[str]]:
    """Track registered emails and delete them (plus tenants) on teardown."""
    emails: set[str] = set()
    yield emails
    await cleanup_users(emails)


@pytest.fixture(autouse=True)
def reset_register_throttle() -> Iterator[None]:
    """Isolate each test from the process-memory per-IP register rate cap.

    register_throttle is module state shared across the process; without this,
    attempts from earlier tests (all on the same ASGITransport client IP, keyed
    under the constant "register" bucket) would bleed into later tests and trip
    the cap unexpectedly.
    """
    auth_service.register_throttle._buckets.clear()
    yield
    auth_service.register_throttle._buckets.clear()


@pytest.mark.asyncio(loop_scope="session")
async def test_register_creates_no_plan_client_and_auto_logs_in(
    created: set[str],
) -> None:
    """Happy path: a fresh signup creates a no-plan, already-expired client,
    sets the session cookie (auto-login), and is immediately gated to /expired
    (home_path "/app" → middleware redirect; /me answers 403 plan_expired)."""
    email = unique_email("client", prefix="test-register")

    async with _client() as client:
        res = await client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD},
        )
        assert res.status_code == 201, res.text
        created.add(email)

        body = res.json()
        assert body["role"] == "client"
        assert body["home_path"] == "/app"
        assert settings.session_cookie_name in client.cookies

        # The session exists but is gated — no plan yet.
        me = await client.get("/api/auth/me")
        assert me.status_code == 403, me.text
        assert me.json()["code"] == "plan_expired"

    # The row is a no-plan client: expired-now, no plan link, zero credits.
    async with async_session_factory() as session:
        user = await users_repo.get_by_email(session, email)
        assert user is not None
        assert user.role == "client"
        assert user.plan_id is None
        assert user.contact is None
        assert user.expires_at is not None
        assert user.expires_at <= datetime.now(UTC)
        assert await tenants_repo.get_credit_balance(session, user.tenant_id) == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_register_duplicate_email_is_rejected(created: set[str]) -> None:
    """A second signup with the same email (case-insensitive) is 409, no row."""
    email = unique_email("client", prefix="test-register")

    async with _client() as client:
        first = await client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD},
        )
        assert first.status_code == 201, first.text
        created.add(email)

        dup = await client.post(
            "/api/auth/register",
            json={"email": email.upper(), "password": PASSWORD},
        )
        assert dup.status_code == 409, dup.text
        assert dup.json()["code"] == "email_taken"


@pytest.mark.asyncio(loop_scope="session")
async def test_register_short_password_is_422(created: set[str]) -> None:
    """A password under the 8-char minimum is rejected by validation (422)."""
    email = unique_email("client", prefix="test-register")

    async with _client() as client:
        res = await client.post(
            "/api/auth/register",
            json={"email": email, "password": "short"},
        )
        assert res.status_code == 422, res.text

    # No account was created.
    async with async_session_factory() as session:
        assert await users_repo.get_by_email(session, email) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_register_is_rate_limited(created: set[str]) -> None:
    """Per-IP cap: after throttle_max_attempts from one IP the next is 429.

    Only the first attempt creates a row; the rest reuse the same email (409)
    but still count toward the cap, so the test creates exactly one user.
    """
    email = unique_email("client", prefix="test-register")

    async with _client() as client:
        first = await client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD},
        )
        assert first.status_code == 201, first.text
        created.add(email)

        # Burn the rest of the window (duplicates 409 but still count).
        for _ in range(settings.throttle_max_attempts - 1):
            dup = await client.post(
                "/api/auth/register",
                json={"email": email, "password": PASSWORD},
            )
            assert dup.status_code == 409, dup.text

        # Cap reached → refused regardless of body.
        blocked = await client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD},
        )
        assert blocked.status_code == 429, blocked.text
        assert blocked.json()["code"] == "too_many_attempts"
