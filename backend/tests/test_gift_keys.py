"""Gift-keys feature: generate / set-default / claim / revoke (I/O matrix).

Covers the spec's edge-case matrix: generate bounds + no-default, the
single-default invariant, new-vs-existing claim (plan + days + the NO-credit
assertion), the expired-client bypass, claimed/revoked/unknown rejections, the
single-use guarantee, revoke states, and the non-client 403.

Plans are created directly in the DB (via ``plan_factory``) and the default is
flagged through the owner API so the clear-prior-default flip is exercised; the
factory cleans its plans + their keys on teardown.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import GiftKey, Plan, Tenant, User
from app.db.repos import tenants as tenants_repo
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from tests.conftest import PASSWORD, cleanup_users, login, seed_user

pytestmark = pytest.mark.asyncio(loop_scope="session")


# --- Helpers --------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def plan_factory(ctx: dict):
    """Make plans on demand; clean them (and any keys) on teardown.

    ``default=True`` flags it through the owner API (POST /plans/{id}/default)
    so the clear-prior-default flip is the path under test.
    """
    owner: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: list[int] = []

    async def make(*, default: bool = False, credits: int = 50) -> int:
        async with async_session_factory() as s:
            plan = Plan(
                name=f"plan-{uuid.uuid4().hex[:8]}",
                price_usd=Decimal("5.00"),
                duration_days=30,
                antispam_seconds=Decimal("4"),
                max_lines_per_batch=100,
                credits=credits,
                is_active=True,
            )
            s.add(plan)
            await s.commit()
            pid = plan.id
        created.append(pid)
        if default:
            res = await owner.post(f"/api/admin/plans/{pid}/default")
            assert res.status_code == 200, res.text
        return pid

    yield make

    async with async_session_factory() as s:
        for pid in created:
            await s.execute(delete(GiftKey).where(GiftKey.plan_id == pid))
        for pid in created:
            await s.execute(delete(Plan).where(Plan.id == pid))
        await s.commit()


async def _new_client(*, expires_in_days: int | None = 30) -> tuple[AsyncClient, User]:
    """A fresh logged-in client (own tenant); caller closes + cleans up."""
    expires = (
        None
        if expires_in_days is None
        else datetime.now(UTC) + timedelta(days=expires_in_days)
    )
    user = await seed_user("client", expires_at=expires)
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    return http, user


async def _balance(tenant_id: int) -> int:
    async with async_session_factory() as s:
        return await tenants_repo.get_credit_balance(s, tenant_id)


async def _gen_key(client: AsyncClient, days: int = 3) -> dict:
    res = await client.post("/api/admin/keys", json={"days": days})
    assert res.status_code == 201, res.text
    return res.json()


# --- Generate -------------------------------------------------------------


async def test_generate_key_snapshots_default_plan(ctx, plan_factory):
    pid = await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    res = await admin.post("/api/admin/keys", json={"days": 30})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["status"] == "active"
    assert body["plan_id"] == pid
    assert body["days"] == 30
    assert body["created_by_email"] == ctx["admin"].email
    assert body["claimed_by_email"] is None
    # RangerX-XXXX-XXXX-XXXX
    parts = body["code"].split("-")
    assert parts[0] == "RangerX" and len(parts) == 4
    assert all(len(p) == 4 for p in parts[1:])


@pytest.mark.parametrize("days", [0, -5, 36501])
async def test_generate_invalid_days(ctx, plan_factory, days):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    res = await admin.post("/api/admin/keys", json={"days": days})
    assert res.status_code == 400
    assert res.json()["code"] == "invalid_key_days"


async def test_generate_without_default_plan(ctx):
    # No default plan configured anywhere → generation must refuse.
    async with async_session_factory() as s:
        await s.execute(update(Plan).values(is_default=False))
        await s.commit()
    admin: AsyncClient = ctx["admin_client"]
    res = await admin.post("/api/admin/keys", json={"days": 10})
    assert res.status_code == 409
    assert res.json()["code"] == "no_default_plan"


async def test_generate_requires_admin(plan_factory):
    # No auth → not_authenticated (401); the role gate guards the mint.
    http = AsyncClient(transport=ASGITransport(app=__import__("app.main", fromlist=["app"]).app), base_url="http://test")
    res = await http.post("/api/admin/keys", json={"days": 5})
    assert res.status_code == 401
    await http.aclose()


# --- Set default (single-default invariant) -------------------------------


async def test_set_default_clears_prior(ctx, plan_factory):
    owner: AsyncClient = ctx["owner_client"]
    a = await plan_factory(default=True)
    b = await plan_factory()
    res = await owner.post(f"/api/admin/plans/{b}/default")
    assert res.status_code == 200, res.text
    listing = (await owner.get("/api/admin/plans")).json()["items"]
    flags = {p["id"]: p["is_default"] for p in listing}
    assert flags[b] is True
    assert flags[a] is False


async def test_set_default_owner_only(ctx, plan_factory):
    pid = await plan_factory()
    admin: AsyncClient = ctx["admin_client"]
    res = await admin.post(f"/api/admin/plans/{pid}/default")
    assert res.status_code == 403


# --- Claim ----------------------------------------------------------------


async def test_claim_new_client_assigns_plan_days_no_credits(ctx, plan_factory):
    pid = await plan_factory(default=True, credits=50)
    admin: AsyncClient = ctx["admin_client"]
    key = await _gen_key(admin, days=7)

    http, user = await _new_client(expires_in_days=1)  # plan_id NULL
    try:
        before = user.expires_at
        res = await http.post("/api/keys/claim", json={"code": key["code"]})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["plan_id"] == pid  # basic plan assigned
        assert body["days_added"] == 7
        # expires extended ~7d from the (still-active) anchor
        new_exp = datetime.fromisoformat(body["expires_at"])
        assert new_exp > before + timedelta(days=6)
        # NEVER grant the plan's credits.
        assert await _balance(user.tenant_id) == 0
        # key consumed
        listing = (await admin.get("/api/admin/keys")).json()["items"]
        claimed = next(k for k in listing if k["code"] == key["code"])
        assert claimed["status"] == "claimed"
        assert claimed["claimed_by_email"] == user.email
    finally:
        await http.aclose()
        await cleanup_users({user.email})


async def test_claim_existing_client_keeps_plan(ctx, plan_factory):
    await plan_factory(default=True)          # bronze (key tier)
    premium = await plan_factory()            # the client's existing tier
    admin: AsyncClient = ctx["admin_client"]
    key = await _gen_key(admin, days=3)

    http, user = await _new_client(expires_in_days=5)
    try:
        # Give the client an existing plan + some credits directly.
        async with async_session_factory() as s:
            await s.execute(
                update(User).where(User.id == user.id).values(plan_id=premium)
            )
            await s.execute(
                update(Tenant).where(Tenant.id == user.tenant_id).values(credit_balance=20)
            )
            await s.commit()
        res = await http.post("/api/keys/claim", json={"code": key["code"]})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["plan_id"] == premium      # kept, NOT downgraded to bronze
        assert body["days_added"] == 3
        assert await _balance(user.tenant_id) == 20  # untouched
    finally:
        await http.aclose()
        await cleanup_users({user.email})


async def test_claim_by_expired_client_bypasses_gate(ctx, plan_factory):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    key = await _gen_key(admin, days=10)

    http, user = await _new_client(expires_in_days=5)
    try:
        # Expire the plan AFTER login (session stays valid; only the plan lapsed).
        async with async_session_factory() as s:
            await s.execute(
                update(User)
                .where(User.id == user.id)
                .values(expires_at=datetime.now(UTC) - timedelta(days=1))
            )
            await s.commit()
        # Every gated route 403s now…
        assert (await http.get("/api/auth/me")).status_code == 403
        # …but claim bypasses the expiry gate.
        res = await http.post("/api/keys/claim", json={"code": key["code"]})
        assert res.status_code == 200, res.text
        # recovered: /me answers again.
        assert (await http.get("/api/auth/me")).status_code == 200
    finally:
        await http.aclose()
        await cleanup_users({user.email})


async def test_claim_revoked_and_unknown_and_double(ctx, plan_factory):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]

    # Unknown code → 404.
    http, user = await _new_client()
    try:
        miss = await http.post("/api/keys/claim", json={"code": "RangerX-AAAA-BBBB-CCCC"})
        assert miss.status_code == 404 and miss.json()["code"] == "key_not_found"

        # Revoked key → 409 key_revoked.
        rk = await _gen_key(admin, days=2)
        rid = rk["id"]
        assert (await admin.post(f"/api/admin/keys/{rid}/revoke")).status_code == 204
        rev = await http.post("/api/keys/claim", json={"code": rk["code"]})
        assert rev.status_code == 409 and rev.json()["code"] == "key_revoked"
    finally:
        await http.aclose()
        await cleanup_users({user.email})

    # Single-use: two clients, one key — first wins, second is already-claimed
    # (the FOR-UPDATE lock serializes a real concurrent race to this outcome).
    key = await _gen_key(admin, days=2)
    h1, u1 = await _new_client()
    h2, u2 = await _new_client()
    try:
        first = await h1.post("/api/keys/claim", json={"code": key["code"]})
        assert first.status_code == 200, first.text
        second = await h2.post("/api/keys/claim", json={"code": key["code"]})
        assert second.status_code == 409
        assert second.json()["code"] == "key_already_claimed"
    finally:
        await h1.aclose()
        await h2.aclose()
        await cleanup_users({u1.email, u2.email})


async def test_non_client_cannot_claim(ctx, plan_factory):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    owner: AsyncClient = ctx["owner_client"]
    key = await _gen_key(admin, days=2)
    # Owner has a valid session but role != client → 403, key NOT consumed.
    res = await owner.post("/api/keys/claim", json={"code": key["code"]})
    assert res.status_code == 403 and res.json()["code"] == "forbidden"
    listing = (await admin.get("/api/admin/keys")).json()["items"]
    assert next(k for k in listing if k["code"] == key["code"])["status"] == "active"


# --- Revoke ---------------------------------------------------------------


async def test_revoke_unclaimed_then_claimed(ctx, plan_factory):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]

    # Unclaimed → 204, status revoked.
    k1 = await _gen_key(admin, days=2)
    assert (await admin.post(f"/api/admin/keys/{k1['id']}/revoke")).status_code == 204
    listing = (await admin.get("/api/admin/keys")).json()["items"]
    assert next(k for k in listing if k["id"] == k1["id"])["status"] == "revoked"

    # Claimed key cannot be revoked → 409 key_already_claimed.
    k2 = await _gen_key(admin, days=2)
    http, user = await _new_client()
    try:
        assert (await http.post("/api/keys/claim", json={"code": k2["code"]})).status_code == 200
        res = await admin.post(f"/api/admin/keys/{k2['id']}/revoke")
        assert res.status_code == 409 and res.json()["code"] == "key_already_claimed"
    finally:
        await http.aclose()
        await cleanup_users({user.email})
