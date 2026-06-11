"""Pytest fixtures + shared helpers for the backend suite.

The suite drives the real ASGI app (httpx ``ASGITransport``) against the dev
Postgres: self-seeding with unique emails, direct DB mutation for state setup,
self-cleaning on teardown. The seed/login/cleanup helpers live HERE so every
test module shares one copy of the tenant+user schema wiring — a model or repo
signature change is fixed in one place.
"""

import uuid
from datetime import datetime

from app.db.base import async_session_factory
from app.db.models import Tenant, User
from app.db.repos import users as users_repo
from app.services.auth import hash_password
from httpx import AsyncClient

PASSWORD = "seed-pass-123"  # noqa: S105 — throwaway test credential


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
