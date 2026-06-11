"""Integration tests for the admin user-management API (Story 1.3).

Drives the real ASGI app (httpx ``ASGITransport``) against the dev Postgres,
authenticating with a real session cookie — the same shape Story 1.2's manual
verification used. Each run seeds throwaway owner/admin accounts with unique
emails and deletes everything it created on teardown, so the dev DB is left
clean and reruns don't collide.

Seed/login/cleanup helpers are shared via ``tests.conftest``.

Run (from backend/, venv active):  pytest tests/test_admin_users.py
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.db.models import User
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.conftest import cleanup_users, login, seed_user, unique_email


@pytest_asyncio.fixture(loop_scope="session")
async def ctx() -> AsyncIterator[dict[str, object]]:
    """Seed an owner + an admin, log each in, and clean up afterwards."""
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


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_creates_client_with_expiry_and_fresh_tenant(
    ctx: dict[str, object],
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    admin: User = ctx["admin"]  # type: ignore[assignment]

    email = unique_email("client")
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

    email = unique_email("client")
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
        json={"email": unique_email("client"), "password": "pw123456", "role": "client"},
    )
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_plan_days"


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_creating_admin_is_forbidden(ctx: dict[str, object]) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    res = await admin_client.post(
        "/api/admin/users",
        json={"email": unique_email("admin"), "password": "pw123456", "role": "admin"},
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

    email = unique_email("admin")
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

    email = unique_email("client")
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
