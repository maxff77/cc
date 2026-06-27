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


async def _user_expiry(user_id: int) -> datetime | None:
    async with async_session_factory() as s:
        u = await s.get(User, user_id)
        return u.expires_at if u else None


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


# days==0 is now VALID (a credits-only key); only negative / over-max days are
# invalid. The empty-key case (0 days + 0 credits) is its own error below.
@pytest.mark.parametrize("days", [-5, 36501])
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


async def test_revoke_unclaimed_key(ctx, plan_factory):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]

    # Unclaimed → 204, status revoked.
    k1 = await _gen_key(admin, days=2)
    assert (await admin.post(f"/api/admin/keys/{k1['id']}/revoke")).status_code == 204
    listing = (await admin.get("/api/admin/keys")).json()["items"]
    assert next(k for k in listing if k["id"] == k1["id"])["status"] == "revoked"


async def test_revoke_claimed_key_cancels_plan(ctx, plan_factory):
    # Kill-switch: revoking a claimed DAYS key revokes the key AND cancels the
    # claimer's plan — expires_at pulled to <= now and their live session killed.
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    key = await _gen_key(admin, days=30)
    http, user = await _new_client(expires_in_days=5)
    try:
        assert (
            await http.post("/api/keys/claim", json={"code": key["code"]})
        ).status_code == 200
        assert (await http.get("/api/auth/me")).status_code == 200  # active
        res = await admin.post(f"/api/admin/keys/{key['id']}/revoke")
        assert res.status_code == 204, res.text
        listing = (await admin.get("/api/admin/keys")).json()["items"]
        assert next(k for k in listing if k["id"] == key["id"])["status"] == "revoked"
        exp = await _user_expiry(user.id)
        assert exp is not None and exp <= datetime.now(UTC) + timedelta(seconds=5)
        # session revoked → next request is unauthenticated.
        assert (await http.get("/api/auth/me")).status_code == 401
    finally:
        await http.aclose()
        await cleanup_users({user.email})


async def test_revoke_claimed_credits_only_keeps_plan(ctx, plan_factory):
    # A credits-ONLY key never granted days, so revoking it revokes the key but
    # leaves the claimer's plan + session untouched.
    await plan_factory(default=True)
    premium = await plan_factory()
    admin: AsyncClient = ctx["admin_client"]
    key = (
        await admin.post("/api/admin/keys", json={"days": 0, "credits": 50})
    ).json()
    http, user = await _new_client(expires_in_days=5)
    try:
        async with async_session_factory() as s:
            await s.execute(
                update(User).where(User.id == user.id).values(plan_id=premium)
            )
            await s.commit()
        assert (
            await http.post("/api/keys/claim", json={"code": key["code"]})
        ).status_code == 200
        before = await _user_expiry(user.id)
        res = await admin.post(f"/api/admin/keys/{key['id']}/revoke")
        assert res.status_code == 204, res.text
        assert await _user_expiry(user.id) == before  # plan untouched
        assert (await http.get("/api/auth/me")).status_code == 200  # session alive
    finally:
        await http.aclose()
        await cleanup_users({user.email})


async def test_revoke_claimed_idempotent_no_reexpire(ctx, plan_factory):
    # A second revoke after the claimer was renewed is a no-op — the renewed
    # plan is NOT re-expired (early-return on already-revoked).
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    key = await _gen_key(admin, days=30)
    http, user = await _new_client(expires_in_days=5)
    try:
        assert (
            await http.post("/api/keys/claim", json={"code": key["code"]})
        ).status_code == 200
        assert (
            await admin.post(f"/api/admin/keys/{key['id']}/revoke")
        ).status_code == 204
        # operator later renews the client into the future.
        future = datetime.now(UTC) + timedelta(days=10)
        async with async_session_factory() as s:
            await s.execute(
                update(User).where(User.id == user.id).values(expires_at=future)
            )
            await s.commit()
        # revoke again → no-op, renewed expiry preserved.
        assert (
            await admin.post(f"/api/admin/keys/{key['id']}/revoke")
        ).status_code == 204
        assert await _user_expiry(user.id) == future
    finally:
        await http.aclose()
        await cleanup_users({user.email})


# --- Default-plan integrity (review patches) ----------------------------------


async def test_set_default_rejects_inactive_plan(ctx, plan_factory):
    owner: AsyncClient = ctx["owner_client"]
    pid = await plan_factory()  # active
    assert (
        await owner.patch(f"/api/admin/plans/{pid}", json={"is_active": False})
    ).status_code == 200
    # Flagging an inactive plan as default would set a "Keys" badge on a plan
    # that breaks generation — rejected.
    res = await owner.post(f"/api/admin/plans/{pid}/default")
    assert res.status_code == 400 and res.json()["code"] == "invalid_plan"


async def test_deactivating_default_clears_flag(ctx, plan_factory):
    owner: AsyncClient = ctx["owner_client"]
    pid = await plan_factory(default=True)
    # Deactivating the current default must drop is_default (else generation
    # would 409 no_default_plan while a default visibly remains flagged).
    assert (
        await owner.patch(f"/api/admin/plans/{pid}", json={"is_active": False})
    ).status_code == 200
    listing = (await owner.get("/api/admin/plans")).json()["items"]
    row = next(p for p in listing if p["id"] == pid)
    assert row["is_default"] is False


async def test_claim_tolerates_case_and_whitespace(ctx, plan_factory):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    key = await _gen_key(admin, days=2)
    http, user = await _new_client()
    try:
        # Lowercased + stray interior/edge whitespace → still resolves.
        messy = "  " + key["code"].lower().replace("-", "- ") + "  "
        res = await http.post("/api/keys/claim", json={"code": messy})
        assert res.status_code == 200, res.text
    finally:
        await http.aclose()
        await cleanup_users({user.email})


# --- Credits on keys (gift-key-credits feature) ---------------------------


async def test_generate_credits_only_and_both(ctx, plan_factory):
    pid = await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]

    # Credits-only: days 0, credits 50 — still snapshots the default plan.
    res = await admin.post("/api/admin/keys", json={"days": 0, "credits": 50})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["days"] == 0 and body["credits"] == 50
    assert body["plan_id"] == pid

    # Days + credits together.
    res = await admin.post("/api/admin/keys", json={"days": 30, "credits": 50})
    assert res.status_code == 201, res.text
    both = res.json()
    assert both["days"] == 30 and both["credits"] == 50


async def test_generate_empty_key_rejected(ctx, plan_factory):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    res = await admin.post("/api/admin/keys", json={"days": 0, "credits": 0})
    assert res.status_code == 400 and res.json()["code"] == "empty_gift_key"


@pytest.mark.parametrize("credits", [-5, 2_147_483_648])
async def test_generate_invalid_credits(ctx, plan_factory, credits):
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    res = await admin.post(
        "/api/admin/keys", json={"days": 1, "credits": credits}
    )
    assert res.status_code == 400 and res.json()["code"] == "invalid_credits"


async def test_claim_credits_only_active_client(ctx, plan_factory):
    # Active client redeems a days-0 credits-50 key: plan + expiry untouched,
    # balance rises by 50.
    await plan_factory(default=True)
    premium = await plan_factory()
    admin: AsyncClient = ctx["admin_client"]
    key = (
        await admin.post("/api/admin/keys", json={"days": 0, "credits": 50})
    ).json()

    http, user = await _new_client(expires_in_days=5)
    try:
        async with async_session_factory() as s:
            await s.execute(
                update(User).where(User.id == user.id).values(plan_id=premium)
            )
            await s.execute(
                update(Tenant)
                .where(Tenant.id == user.tenant_id)
                .values(credit_balance=10)
            )
            await s.commit()
        before = user.expires_at
        res = await http.post("/api/keys/claim", json={"code": key["code"]})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["days_added"] == 0
        assert body["credits_added"] == 50
        assert body["plan_id"] == premium  # plan kept
        # expiry NOT extended (no days added).
        new_exp = datetime.fromisoformat(body["expires_at"])
        assert new_exp < before + timedelta(hours=1)
        assert await _balance(user.tenant_id) == 60  # 10 + 50
    finally:
        await http.aclose()
        await cleanup_users({user.email})


async def test_claim_days_and_credits_new_client(ctx, plan_factory):
    # Plan-less client redeems a days-30 credits-50 key: gets the basic plan,
    # +30d, AND +50 credits.
    pid = await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    key = (
        await admin.post("/api/admin/keys", json={"days": 30, "credits": 50})
    ).json()

    http, user = await _new_client(expires_in_days=1)  # plan_id NULL
    try:
        before = user.expires_at
        res = await http.post("/api/keys/claim", json={"code": key["code"]})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["plan_id"] == pid  # basic plan assigned
        assert body["days_added"] == 30
        assert body["credits_added"] == 50
        new_exp = datetime.fromisoformat(body["expires_at"])
        assert new_exp > before + timedelta(days=29)
        assert await _balance(user.tenant_id) == 50
    finally:
        await http.aclose()
        await cleanup_users({user.email})


async def test_claim_credits_only_plan_less_no_access(ctx, plan_factory):
    # Credits ≠ access: a credits-only key for a plan-less client adds credits
    # but assigns NO plan and does NOT extend expiry.
    await plan_factory(default=True)
    admin: AsyncClient = ctx["admin_client"]
    key = (
        await admin.post("/api/admin/keys", json={"days": 0, "credits": 25})
    ).json()

    http, user = await _new_client(expires_in_days=1)  # plan_id NULL
    try:
        res = await http.post("/api/keys/claim", json={"code": key["code"]})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["plan_id"] is None  # NOT assigned
        assert body["days_added"] == 0
        assert body["credits_added"] == 25
        assert await _balance(user.tenant_id) == 25
    finally:
        await http.aclose()
        await cleanup_users({user.email})
