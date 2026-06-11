"""Integration tests for the admin user-management API (Story 1.3).

Drives the real ASGI app (httpx ``ASGITransport``) against the dev Postgres,
authenticating with a real session cookie — the same shape Story 1.2's manual
verification used. Each run seeds throwaway owner/admin accounts with unique
emails and deletes everything it created on teardown, so the dev DB is left
clean and reruns don't collide.

Run (from backend/, venv active):  pytest tests/test_admin_users.py
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import Tenant, User
from app.db.repos import users as users_repo
from app.main import app
from app.services.auth import hash_password
from httpx import ASGITransport, AsyncClient

PASSWORD = "seed-pass-123"  # noqa: S105 — throwaway test credential


def _email(role: str) -> str:
    return f"test-{role}-{uuid.uuid4().hex[:8]}@cc.test"


async def _seed(role: str) -> User:
    """Create a fresh user (own tenant) directly, bypassing the API."""
    async with async_session_factory() as session:
        tenant = await users_repo.create_tenant(session, name=f"t-{uuid.uuid4().hex}")
        user = await users_repo.create_user(
            session,
            tenant_id=tenant.id,
            email=_email(role),
            password_hash=hash_password(PASSWORD),
            role=role,
            expires_at=None,
        )
        await session.commit()
        return user


async def _login(client: AsyncClient, email: str) -> None:
    res = await client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text


async def _cleanup(emails: set[str]) -> None:
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


@pytest_asyncio.fixture(loop_scope="session")
async def ctx() -> AsyncIterator[dict[str, object]]:
    """Seed an owner + an admin, log each in, and clean up afterwards."""
    created: set[str] = set()
    owner = await _seed("owner")
    admin = await _seed("admin")
    created.update({owner.email, admin.email})

    transport = ASGITransport(app=app)
    owner_client = AsyncClient(transport=transport, base_url="http://test")
    admin_client = AsyncClient(transport=transport, base_url="http://test")
    await _login(owner_client, owner.email)
    await _login(admin_client, admin.email)

    yield {
        "owner_client": owner_client,
        "admin_client": admin_client,
        "owner": owner,
        "admin": admin,
        "created": created,
    }

    await owner_client.aclose()
    await admin_client.aclose()
    await _cleanup(created)


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_creates_client_with_expiry_and_fresh_tenant(
    ctx: dict[str, object],
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    admin: User = ctx["admin"]  # type: ignore[assignment]

    email = _email("client")
    created.add(email)
    res = await admin_client.post(
        "/api/admin/users",
        json={"email": email, "password": "pw123456", "role": "client", "plan_days": 30},
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["role"] == "client"
    assert body["expires_at"] is not None  # plan expiry populated
    assert body["tenant_id"] != admin.tenant_id  # its own fresh tenant


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_email_is_email_taken(ctx: dict[str, object]) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    email = _email("client")
    created.add(email)
    payload = {"email": email, "password": "pw123456", "role": "client", "plan_days": 10}
    first = await admin_client.post("/api/admin/users", json=payload)
    assert first.status_code == 201, first.text
    dup = await admin_client.post("/api/admin/users", json=payload)
    assert dup.status_code == 409
    assert dup.json()["code"] == "email_taken"


@pytest.mark.asyncio(loop_scope="session")
async def test_client_requires_positive_plan_days(ctx: dict[str, object]) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    res = await admin_client.post(
        "/api/admin/users",
        json={"email": _email("client"), "password": "pw123456", "role": "client"},
    )
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_plan_days"


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_creating_admin_is_forbidden(ctx: dict[str, object]) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    res = await admin_client.post(
        "/api/admin/users",
        json={"email": _email("admin"), "password": "pw123456", "role": "admin"},
    )
    assert res.status_code == 403
    assert res.json()["code"] == "forbidden"


@pytest.mark.asyncio(loop_scope="session")
async def test_listing_is_role_filtered(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    admin: User = ctx["admin"]  # type: ignore[assignment]

    admin_roles = {u["role"] for u in (await admin_client.get("/api/admin/users")).json()["items"]}
    assert "admin" not in admin_roles  # admin sees clients only
    assert "owner" not in admin_roles

    owner_items = (await owner_client.get("/api/admin/users")).json()["items"]
    owner_roles = {u["role"] for u in owner_items}
    assert "admin" in owner_roles  # owner sees admins too (the seeded one)
    assert "owner" not in owner_roles  # never other owners
    assert any(u["id"] == admin.id for u in owner_items)


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_creates_and_deletes_admin(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    email = _email("admin")
    created.add(email)
    res = await owner_client.post(
        "/api/admin/users",
        json={"email": email, "password": "pw123456", "role": "admin"},
    )
    assert res.status_code == 201, res.text
    new_admin = res.json()
    assert new_admin["role"] == "admin"
    assert new_admin["expires_at"] is None  # admins carry no plan

    deleted = await owner_client.delete(f"/api/admin/users/{new_admin['id']}")
    assert deleted.status_code == 204


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_deleting_a_client_is_forbidden(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    email = _email("client")
    created.add(email)
    res = await owner_client.post(
        "/api/admin/users",
        json={"email": email, "password": "pw123456", "role": "client", "plan_days": 5},
    )
    client_id = res.json()["id"]
    # 1.3 only removes admins; a client target is forbidden (removal is 1.5).
    forbidden = await owner_client.delete(f"/api/admin/users/{client_id}")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "forbidden"


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_delete_is_forbidden_for_admin_actor(ctx: dict[str, object]) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    owner: User = ctx["owner"]  # type: ignore[assignment]

    # An admin actor hits the owner-only DELETE → 403 before any lookup.
    res = await admin_client.delete(f"/api/admin/users/{owner.id}")
    assert res.status_code == 403
