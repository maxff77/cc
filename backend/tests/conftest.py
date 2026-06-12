"""Pytest fixtures + shared helpers for the backend suite.

The suite drives the real ASGI app (httpx ``ASGITransport``) against the dev
Postgres: self-seeding with unique emails, direct DB mutation for state setup,
self-cleaning on teardown. The seed/login/cleanup helpers live HERE so every
test module shares one copy of the tenant+user schema wiring — a model or repo
signature change is fixed in one place.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import Tenant, User
from app.db.repos import users as users_repo
from app.main import app
from app.services.auth import hash_password
from httpx import ASGITransport, AsyncClient

PASSWORD = "seed-pass-123"  # noqa: S105 — throwaway test credential


class FakeGateway:
    """In-memory stand-in for ``app.core.telegram.gateway`` (Story 2.2).

    The architecture's "fake Telegram client fixture" starts here — Stories
    2.3/2.4/2.5 reuse it. Records sent texts, returns incrementing message
    ids, and is programmable to raise: push exceptions onto ``errors`` and
    each ``send`` pops+raises one before succeeding (e.g. a
    ``FloodWaitError(request=None, capture=0)`` once, then success).
    """

    def __init__(self) -> None:
        self.authorized = True
        self.target_ok = True
        self.sent: list[str] = []
        self.errors: list[Exception] = []
        self._next_id = 0

    @property
    def ready(self) -> bool:
        return self.authorized and self.target_ok

    async def send(self, text: str) -> int:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(text)
        self._next_id += 1
        return self._next_id


def unique_email(role: str, *, prefix: str = "test") -> str:
    """Collision-free throwaway address, prefixed per test module."""
    return f"{prefix}-{role}-{uuid.uuid4().hex[:8]}@cc.test"


async def seed_user(
    role: str,
    *,
    expires_at: datetime | None = None,
    email_prefix: str = "test",
) -> User:
    """Create a fresh user (own tenant) directly, bypassing the API."""
    async with async_session_factory() as session:
        tenant = await users_repo.create_tenant(session, name=f"t-{uuid.uuid4().hex}")
        user = await users_repo.create_user(
            session,
            tenant_id=tenant.id,
            email=unique_email(role, prefix=email_prefix),
            password_hash=hash_password(PASSWORD),
            role=role,
            expires_at=expires_at,
        )
        await session.commit()
        return user


async def login(client: AsyncClient, email: str) -> None:
    """Log ``email`` in with the seeded password; asserts success."""
    res = await client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text


@pytest_asyncio.fixture(loop_scope="session")
async def ctx() -> AsyncIterator[dict[str, object]]:
    """Seed an owner + an admin, log each in, and clean up afterwards.

    Shared by the admin API modules (test_admin_users, test_admin_lifecycle).
    Tests add the per-test emails they seed to ``ctx["created"]`` so the
    teardown removes them too.
    """
    created: set[str] = set()
    owner = await seed_user("owner")
    admin = await seed_user("admin")
    created.update({owner.email, admin.email})

    transport = ASGITransport(app=app)
    owner_client = AsyncClient(transport=transport, base_url="http://test")
    admin_client = AsyncClient(transport=transport, base_url="http://test")
    await login(owner_client, owner.email)
    await login(admin_client, admin.email)

    yield {
        "owner_client": owner_client,
        "admin_client": admin_client,
        "owner": owner,
        "admin": admin,
        "created": created,
    }

    await owner_client.aclose()
    await admin_client.aclose()
    await cleanup_users(created)


async def cleanup_users(emails: set[str]) -> None:
    """Delete every user created during the test, plus its tenant."""
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
