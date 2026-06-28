"""Tests for the owner-managed pricing-plan catalog (plan-catalog feature).

Covers the spec's I/O & Edge-Case Matrix and Acceptance Criteria across the
layers that ship the feature:
- the owner-only ``/api/admin/plans`` CRUD (create/list/patch/delete) + the
  field-bound validation and the duplicate-name / in-use / not-found contracts;
- assigning a plan on client create (``expires_at ≈ now + duration_days``) and
  renewing via ``plan_id`` (extends from ``max(now, current)``);
- the plan ``max_lines_per_batch`` cap on batch CREATE and APPEND (and the
  ``plan_id=NULL`` no-cap path);
- the widened global-interval floor (sub-1s rejected);
- the per-tenant scheduler cooldown (``pick_next``/``note_sent`` over an
  injectable monotonic clock — a tenant is skipped until its antispam elapses
  while a second eligible tenant is still picked).

Conftest idiom: real ASGI app + dev Postgres, self-seeding/self-cleaning. The
plan catalog is GLOBAL state shared across tenants, so the local autouse
``track_plans`` fixture deletes every plan a test creates on teardown — nulling
``users.plan_id`` first (the FK is RESTRICT). The autouse ``reset_scheduler``
(conftest) returns the singleton to the env-default floor per test.

Run (from backend/, venv active):  pytest tests/test_plans_catalog.py
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from app.core.scheduler import Scheduler
from app.db.base import async_session_factory
from app.db.models import Plan, User
from app.db.repos.batches import ActiveSender
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from tests.conftest import (
    login,
    seed_user,
    unique_email,
)

# The shared `ctx` (owner + admin, logged in), `client_user`, `gate` and
# `fake_gateway` fixtures live in tests/conftest.py.


# --- Local fixtures / helpers ------------------------------------------------


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def track_plans() -> AsyncIterator[set[int]]:
    """Delete every plan created during a test (global catalog, shared DB).

    Plans are NOT tenant-scoped, so they would leak across modules. The
    ``users.plan_id`` FK is RESTRICT, so a still-referenced plan can't be
    deleted — null out any reference first, then drop the tracked rows.
    """
    created: set[int] = set()
    yield created
    if not created:
        return
    async with async_session_factory() as session:
        await session.execute(
            update(User).where(User.plan_id.in_(created)).values(plan_id=None)
        )
        await session.execute(delete(Plan).where(Plan.id.in_(created)))
        await session.commit()


def _plan_payload(**overrides: object) -> dict:
    """A valid create-plan body; overrides win (unique name by default)."""
    body: dict = {
        "name": f"Plan {uuid.uuid4().hex[:8]}",
        "price_usd": "19.99",
        "duration_days": 30,
        "max_lines_per_batch": 100,
        "is_active": True,
    }
    body.update(overrides)
    return body


async def _create_plan(
    owner_client: AsyncClient, tracked: set[int], **overrides: object
) -> dict:
    """POST a plan via the owner API, track it for cleanup, return the row."""
    res = await owner_client.post("/api/admin/plans", json=_plan_payload(**overrides))
    assert res.status_code == 201, res.text
    body = res.json()
    tracked.add(body["id"])
    return body


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> object:
    return await http.post("/api/batches", json={"text": text, "gate_id": gate_id})


async def _set_plan_id(user_id: int, plan_id: int | None) -> None:
    """Link a seeded user to a plan directly (bypass the create-user route)."""
    async with async_session_factory() as session:
        row = await session.get(User, user_id)
        assert row is not None
        row.plan_id = plan_id
        await session.commit()


# --- Plan CRUD happy paths (matrix: Create plan) -----------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_plan_persists_and_appears_in_list(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Matrix 'Create plan': owner POST valid fields → 201, row, in the list."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    plan = await _create_plan(
        owner_client,
        track_plans,
        name=f"Pro {uuid.uuid4().hex[:8]}",
        price_usd="49.50",
        duration_days=15,
        max_lines_per_batch=10,
    )
    assert plan["duration_days"] == 15
    assert plan["max_lines_per_batch"] == 10
    assert plan["is_active"] is True
    # Decimals serialize as strings/numbers — compare by value, not literal.
    assert Decimal(str(plan["price_usd"])) == Decimal("49.50")

    listing = await owner_client.get("/api/admin/plans")
    assert listing.status_code == 200
    body = listing.json()
    assert plan["id"] in {p["id"] for p in body["items"]}
    assert body["total"] == len(body["items"])


@pytest.mark.asyncio(loop_scope="session")
async def test_update_plan_partial_edit_and_deactivate(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """PATCH writes only provided fields; ``is_active:false`` retires the plan."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, duration_days=30)

    edit = await owner_client.patch(
        f"/api/admin/plans/{plan['id']}",
        json={"duration_days": 60, "is_active": False},
    )
    assert edit.status_code == 200, edit.text
    body = edit.json()
    assert body["duration_days"] == 60
    assert body["is_active"] is False
    # Untouched fields survive the partial edit.
    assert body["max_lines_per_batch"] == plan["max_lines_per_batch"]

    # A retired plan still appears in the full catalog list (active + retired).
    listing = await owner_client.get("/api/admin/plans")
    retired = next(p for p in listing.json()["items"] if p["id"] == plan["id"])
    assert retired["is_active"] is False


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_unused_plan_is_204(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """An unreferenced plan deletes cleanly (the in-use guard does not fire)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans)

    res = await owner_client.delete(f"/api/admin/plans/{plan['id']}")
    assert res.status_code == 204, res.text
    track_plans.discard(plan["id"])  # already gone — nothing to clean

    listing = await owner_client.get("/api/admin/plans")
    assert plan["id"] not in {p["id"] for p in listing.json()["items"]}


# --- Owner-only enforcement (Boundaries: CRUD owner-only via require_role) ----


@pytest.mark.asyncio(loop_scope="session")
async def test_plans_crud_is_owner_only(
    ctx: dict[str, object],
    track_plans: set[int],
    client_user: tuple[AsyncClient, User],
) -> None:
    """Non-owner (admin AND client) callers are 403 on every plan verb."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    client_http, _ = client_user
    # A real plan id so the 403 is the authorization gate, not a 404.
    plan = await _create_plan(owner_client, track_plans)

    for http in (admin_client, client_http):
        assert (await http.get("/api/admin/plans")).status_code == 403
        assert (
            await http.post("/api/admin/plans", json=_plan_payload())
        ).status_code == 403
        assert (
            await http.patch(
                f"/api/admin/plans/{plan['id']}", json={"duration_days": 5}
            )
        ).status_code == 403
        assert (
            await http.delete(f"/api/admin/plans/{plan['id']}")
        ).status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_plans_list_is_401_anonymous(track_plans: set[int]) -> None:
    """An unauthenticated caller is rejected before the owner gate."""
    async with _client() as anon:
        res = await anon.get("/api/admin/plans")
        assert res.status_code == 401


# --- Invalid field rejection (matrix: Invalid plan field) --------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_plan_invalid_fields_rejected(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Matrix 'Invalid plan field': days 0 / lines 0 / negative price → 400
    invalid_plan, each with a field-specific message."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    cases = [
        {"duration_days": 0},
        {"max_lines_per_batch": 0},
        {"price_usd": "-1"},
    ]
    for override in cases:
        res = await owner_client.post(
            "/api/admin/plans", json=_plan_payload(**override)
        )
        assert res.status_code == 400, f"{override}: {res.text}"
        assert res.json()["code"] == "invalid_plan", override
        # Field-specific Spanish copy, not the generic fallback.
        assert res.json()["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_update_plan_invalid_field_rejected(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """An edit that would drop a field below its floor → 400 invalid_plan."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans)
    res = await owner_client.patch(
        f"/api/admin/plans/{plan['id']}", json={"duration_days": 0}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_plan"


# --- Duplicate name (matrix: Create plan → duplicate name) -------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_plan_duplicate_name_is_409(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Matrix 'Create plan' error: duplicate name → 409 plan_name_taken."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    name = f"Dup {uuid.uuid4().hex[:8]}"
    await _create_plan(owner_client, track_plans, name=name)

    dup = await owner_client.post("/api/admin/plans", json=_plan_payload(name=name))
    assert dup.status_code == 409, dup.text
    assert dup.json()["code"] == "plan_name_taken"


@pytest.mark.asyncio(loop_scope="session")
async def test_update_plan_to_existing_name_is_409(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Renaming a plan onto another plan's name → 409 plan_name_taken."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    taken = f"Taken {uuid.uuid4().hex[:8]}"
    await _create_plan(owner_client, track_plans, name=taken)
    mover = await _create_plan(owner_client, track_plans)

    res = await owner_client.patch(
        f"/api/admin/plans/{mover['id']}", json={"name": taken}
    )
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "plan_name_taken"


# --- Not found (patch/delete unknown id) -------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_patch_and_delete_unknown_plan_is_404(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Unknown (and out-of-int4) plan id on patch/delete → 404 plan_not_found."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for plan_id in (999999999, 999999999999):
        patch = await owner_client.patch(
            f"/api/admin/plans/{plan_id}", json={"duration_days": 5}
        )
        assert patch.status_code == 404, patch.text
        assert patch.json()["code"] == "plan_not_found"
        dele = await owner_client.delete(f"/api/admin/plans/{plan_id}")
        assert dele.status_code == 404, dele.text
        assert dele.json()["code"] == "plan_not_found"


# --- Delete plan in use (matrix: Delete plan in use) -------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_plan_in_use_is_409(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Matrix 'Delete plan in use': a plan referenced by ≥1 user → 409
    plan_in_use (retire via is_active=false instead)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans)

    client = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    created.add(client.email)
    await _set_plan_id(client.id, plan["id"])

    res = await owner_client.delete(f"/api/admin/plans/{plan['id']}")
    assert res.status_code == 409, res.text
    assert res.json()["code"] == "plan_in_use"


# --- Assign plan on client create (matrix: Assign plan on client create) -----


@pytest.mark.asyncio(loop_scope="session")
async def test_create_client_with_plan_sets_expiry_and_link(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Matrix 'Assign plan on client create': active ``plan_id`` → client
    created, ``expires_at ≈ now + duration_days``, ``plan_id`` set."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, duration_days=15)

    email = unique_email("client", prefix="test-plancat")
    created.add(email)
    res = await owner_client.post(
        "/api/admin/users",
        json={
            "email": email,
            "password": "pw123456",
            "role": "client",
            "plan_id": plan["id"],
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    expires = datetime.fromisoformat(body["expires_at"])
    expected = datetime.now(UTC) + timedelta(days=15)
    # Tolerance window, not exact equality (clock moves between create and now).
    assert abs((expires - expected).total_seconds()) < 60

    # The link landed on the row (DB is the source of truth for plan_id).
    async with async_session_factory() as session:
        row = await session.get(User, body["id"])
        assert row is not None
        assert row.plan_id == plan["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_create_client_with_inactive_plan_is_invalid(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Matrix edge: an inactive (or unknown) ``plan_id`` → 400 invalid_plan."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, is_active=False)

    inactive = await owner_client.post(
        "/api/admin/users",
        json={
            "email": unique_email("client", prefix="test-plancat"),
            "password": "pw123456",
            "role": "client",
            "plan_id": plan["id"],
        },
    )
    assert inactive.status_code == 400, inactive.text
    assert inactive.json()["code"] == "invalid_plan"

    unknown = await owner_client.post(
        "/api/admin/users",
        json={
            "email": unique_email("client", prefix="test-plancat"),
            "password": "pw123456",
            "role": "client",
            "plan_id": 999999999,
        },
    )
    assert unknown.status_code == 400, unknown.text
    assert unknown.json()["code"] == "invalid_plan"


# --- Renew via plan (matrix: Renew via plan) ---------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_active_client_via_plan_id_stacks_days(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Matrix 'Renew via plan': an ACTIVE plan renewed via ``plan_id`` extends
    from current expiry (``max(now, current) + duration_days``) and links it."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, duration_days=30)

    old_expiry = datetime.now(UTC) + timedelta(days=10)
    client = await seed_user("client", expires_at=old_expiry)
    created.add(client.email)

    res = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"plan_id": plan["id"]}
    )
    assert res.status_code == 200, res.text
    new_expiry = datetime.fromisoformat(res.json()["expires_at"])
    # Active plan → anchor on current expiry, add the plan's duration.
    expected = old_expiry + timedelta(days=30)
    assert abs((new_expiry - expected).total_seconds()) < 60

    async with async_session_factory() as session:
        row = await session.get(User, client.id)
        assert row is not None
        assert row.plan_id == plan["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_expired_client_via_plan_id_grants_from_today(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Renewing an EXPIRED client via ``plan_id`` anchors on now (restores
    access) — the ``max(now, current)`` branch."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, duration_days=30)

    client = await seed_user(
        "client", expires_at=datetime.now(UTC) - timedelta(days=60)
    )
    created.add(client.email)

    res = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"plan_id": plan["id"]}
    )
    assert res.status_code == 200, res.text
    new_expiry = datetime.fromisoformat(res.json()["expires_at"])
    # Anchored on today, not the 60-day-stale expiry → roughly now + 30d.
    expected = datetime.now(UTC) + timedelta(days=30)
    assert abs((new_expiry - expected).total_seconds()) < 120
    assert new_expiry > datetime.now(UTC)


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_via_inactive_plan_is_invalid(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """Renewing onto an inactive plan → 400 invalid_plan (active plans only)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, is_active=False)

    client = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=10)
    )
    created.add(client.email)

    res = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"plan_id": plan["id"]}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_plan"


# --- /me plan summary (Code Map: auth.py /me returns the plan summary) --------


@pytest.mark.asyncio(loop_scope="session")
async def test_me_carries_plan_summary_when_assigned(
    ctx: dict[str, object], track_plans: set[int]
) -> None:
    """A client on a plan sees ``me.plan = {name, max_lines_per_batch}``;
    a no-plan client sees ``plan = null``. Antispam is no longer in the summary
    (antispam-per-user feature)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(
        owner_client, track_plans, max_lines_per_batch=7
    )

    client = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    created.add(client.email)

    # No plan yet → me.plan is null.
    async with _client() as http:
        await login(http, client.email)
        before = await http.get("/api/auth/me")
        assert before.status_code == 200, before.text
        assert before.json()["plan"] is None

    # Link the plan → me.plan carries the summary.
    await _set_plan_id(client.id, plan["id"])
    async with _client() as http:
        await login(http, client.email)
        after = await http.get("/api/auth/me")
        assert after.status_code == 200, after.text
        summary = after.json()["plan"]
        assert summary is not None
        assert summary["name"] == plan["name"]
        assert summary["max_lines_per_batch"] == 7
        assert "antispam_seconds" not in summary


# --- Batch line cap on CREATE (matrix: Send within / over cap) ---------------


@pytest.mark.asyncio(loop_scope="session")
async def test_batch_create_within_cap_is_accepted(
    ctx: dict[str, object], track_plans: set[int], gate: dict
) -> None:
    """Matrix 'Send within cap': cap 10, exactly 10 lines → batch accepted."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, max_lines_per_batch=10)

    client = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    created.add(client.email)
    await _set_plan_id(client.id, plan["id"])

    async with _client() as http:
        await login(http, client.email)
        text = "\n".join(f"linea {i}" for i in range(10))
        res = await _post_batch(http, text, gate["id"])
        assert res.status_code == 201, res.text
        assert res.json()["added"] == 10


@pytest.mark.asyncio(loop_scope="session")
async def test_batch_create_over_cap_rejected_nothing_queued(
    ctx: dict[str, object], track_plans: set[int], gate: dict
) -> None:
    """Matrix 'Send over cap': cap 10, 12 lines → 400 batch_line_limit, nothing
    queued (the message states the cap and the attempted count)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, max_lines_per_batch=10)

    client = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    created.add(client.email)
    await _set_plan_id(client.id, plan["id"])

    async with _client() as http:
        await login(http, client.email)
        text = "\n".join(f"linea {i}" for i in range(12))
        res = await _post_batch(http, text, gate["id"])
        assert res.status_code == 400, res.text
        body = res.json()
        assert body["code"] == "batch_line_limit"
        assert "10" in body["message"] and "12" in body["message"]

    # Nothing queued: the tenant has no live batch.
    async with async_session_factory() as session:
        from app.db.repos import batches as batches_repo

        live = await batches_repo.get_live_batch(session, client.tenant_id)
        assert live is None


# --- Batch line cap on APPEND (matrix: Send over cap, append path) -----------


@pytest.mark.asyncio(loop_scope="session")
async def test_batch_append_over_cap_rejected_nothing_added(
    ctx: dict[str, object], track_plans: set[int], gate: dict
) -> None:
    """The cap is enforced against the RESULTING batch size on append, so a
    client can't bypass it by chunking → 400 batch_line_limit, nothing added."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    plan = await _create_plan(owner_client, track_plans, max_lines_per_batch=3)

    client = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    created.add(client.email)
    await _set_plan_id(client.id, plan["id"])

    async with _client() as http:
        await login(http, client.email)
        # First batch fills the cap (3 lines).
        first = await _post_batch(http, "a\nb\nc", gate["id"])
        assert first.status_code == 201, first.text
        assert first.json()["added"] == 3

        # Appending even one MORE distinct line would make 4 > cap 3 → rejected.
        over = await _post_batch(http, "d", gate["id"])
        assert over.status_code == 400, over.text
        assert over.json()["code"] == "batch_line_limit"

        # The batch still holds exactly the original 3 lines (nothing added).
        async with async_session_factory() as session:
            from app.db.repos import batches as batches_repo

            sent, queued, _failed = await batches_repo.counts(
                session, first.json()["id"]
            )
            assert sent + queued == 3


# --- No-plan client: no cap (matrix: No-plan client) -------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_no_plan_client_has_no_line_cap(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    """Matrix 'No-plan client': ``plan_id=NULL`` sends many lines → accepted
    (no cap), unchanged behavior. ``client_user`` is seeded with no plan."""
    http, user = client_user
    assert user.plan_id is None  # the fixture's client carries no plan
    text = "\n".join(f"linea {i}" for i in range(500))
    res = await _post_batch(http, text, gate["id"])
    assert res.status_code == 201, res.text
    assert res.json()["added"] == 500


# --- Global interval floor (matrix: Floor below min) -------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_global_interval_below_one_second_rejected(
    ctx: dict[str, object],
) -> None:
    """Matrix 'Floor below min': owner PUT interval 0.5 → 400 (min 1s); a
    valid >=1s value is accepted. The floor is never auto-lowered below 1s."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for bad in (0.5, 0):
        res = await owner_client.put(
            "/api/admin/interval", json={"interval_seconds": bad}
        )
        assert res.status_code == 400, f"{bad}: {res.text}"
        assert res.json()["code"] == "invalid_send_interval"

    # The new lower bound (1s) is itself valid.
    ok = await owner_client.put(
        "/api/admin/interval", json={"interval_seconds": 1.0}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["interval_seconds"] == 1.0
    # Restore the default so later tests start from a clean floor.
    await owner_client.put("/api/admin/interval", json={"interval_seconds": 4.0})


# --- Scheduler per-tenant cooldown (matrix: Antispam pacing; AC: 20s spacing) -


class _FakeClock:
    """Deterministic monotonic stand-in (mirrors test_scheduler.FakeClock)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _sender(tenant_id: int, *, antispam: float) -> ActiveSender:
    """A client-tier sender carrying a per-tenant antispam cooldown."""
    return ActiveSender(
        tenant_id=tenant_id,
        batch_id=tenant_id * 10,
        priority=0,
        antispam_seconds=antispam,
    )


def test_cooldown_skips_tenant_until_antispam_elapses() -> None:
    """AC: a tenant on antispam=20s is NOT re-picked until 20s pass, while a
    second eligible tenant IS picked during the gap."""
    clock = _FakeClock()
    sched = Scheduler(now=clock)
    a = _sender(1, antispam=20.0)
    b = _sender(2, antispam=20.0)
    active = [a, b]

    # First slot goes to tenant 1; record its send → cooldown starts.
    pick = sched.pick_next(active)
    assert pick is not None and pick.tenant_id == 1
    sched.note_sent(1)

    # Immediately after: tenant 1 is cooling down, so the only eligible sender
    # is tenant 2 — it is picked (interleaved within tenant 1's gap).
    pick = sched.pick_next(active)
    assert pick is not None and pick.tenant_id == 2
    sched.note_sent(2)

    # Both now cooling down → nobody eligible → None (worker idles, re-polls).
    assert sched.pick_next(active) is None

    # 19s later: still inside both 20s cooldowns → still nobody.
    clock.advance(19.0)
    assert sched.pick_next(active) is None

    # Past 20s since tenant 1's send → tenant 1 becomes eligible again.
    clock.advance(1.5)  # total 20.5s since tenant 1's note_sent
    pick = sched.pick_next(active)
    assert pick is not None and pick.tenant_id == 1


def test_cooldown_lets_uncooled_tenant_through_while_other_waits() -> None:
    """A long-antispam tenant alone yields None right after its send, but a
    no-cooldown peer keeps flowing — the cooldown slows ONLY the cooling tenant."""
    clock = _FakeClock()
    sched = Scheduler(now=clock)
    slow = _sender(1, antispam=20.0)
    fast = _sender(2, antispam=0.0)  # no override → 0 (no per-tenant cooldown)

    # Slow tenant sends, then is skipped; the fast peer is always servable.
    assert sched.pick_next([slow]).tenant_id == 1
    sched.note_sent(1)
    assert sched.pick_next([slow]) is None  # alone + cooling → idle

    picks = []
    for _ in range(3):
        pick = sched.pick_next([fast])
        assert pick is not None
        picks.append(pick.tenant_id)
        sched.note_sent(2)
    assert picks == [2, 2, 2]  # never blocked by its own (zero) cooldown


def test_cooldown_is_cleared_by_reset() -> None:
    """``reset()`` (a restart) wipes the cooldown map — every tenant is again
    immediately eligible (process-memory contract)."""
    clock = _FakeClock()
    sched = Scheduler(now=clock)
    a = _sender(1, antispam=20.0)

    assert sched.pick_next([a]).tenant_id == 1
    sched.note_sent(1)
    assert sched.pick_next([a]) is None  # cooling down

    sched.reset()
    # The injected clock is preserved across reset(); the cooldown map is not.
    assert sched.pick_next([a]).tenant_id == 1
