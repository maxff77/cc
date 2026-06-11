"""Integration tests for client lifecycle: renew + block/unblock (Story 1.5).

Drives the real ASGI app (httpx ``ASGITransport``) against the dev Postgres,
mirroring ``test_admin_users.py`` / ``test_plan_expiry.py``: self-seeding with
unique emails, direct DB mutation for state setup (pushing ``expires_at`` into
the past), self-cleaning on teardown, all pinned to ``loop_scope="session"`` so
they share the async engine pool. Seed/login/cleanup helpers are shared via
``tests.conftest``.

The two critical round-trips are AC2 (expired → renew → login 200) and AC3
(block → live cookie 401 + relogin 403 account_blocked).

Run (from backend/, venv active):  pytest tests/test_admin_lifecycle.py
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.db.base import async_session_factory
from app.db.models import User
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.conftest import PASSWORD, login, seed_user

# The shared `ctx` fixture (owner + admin, logged in, self-cleaning) lives in
# tests/conftest.py.


async def _seed(role: str, *, expires_at: datetime | None = None) -> User:
    return await seed_user(role, expires_at=expires_at, email_prefix="test-lifecycle")


async def _set_expires_at(user_id: int, when: datetime) -> None:
    """Move a user's plan expiry directly in the DB (simulates time passing)."""
    async with async_session_factory() as session:
        row = await session.get(User, user_id)
        assert row is not None
        row.expires_at = when
        await session.commit()


async def _set_blocked_flag(user_id: int, blocked: bool) -> None:
    """Flip ``is_blocked`` directly in the DB — WITHOUT revoking sessions.

    Simulates the login/block race where a session commits after the block's
    bulk revoke already ran (the hole the per-request check in
    ``get_current_user`` closes).
    """
    async with async_session_factory() as session:
        row = await session.get(User, user_id)
        assert row is not None
        row.is_blocked = blocked
        await session.commit()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- Renew (AC1, AC2) -----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_active_client_with_plan_days_extends(ctx: dict[str, object]) -> None:
    """Add-days on an ACTIVE plan stacks: new expiry ≈ old expiry + days."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    old_expiry = datetime.now(UTC) + timedelta(days=30)
    client = await _seed("client", expires_at=old_expiry)
    created.add(client.email)

    res = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"plan_days": 30}
    )
    assert res.status_code == 200, res.text
    new_expiry = datetime.fromisoformat(res.json()["expires_at"])
    expected = old_expiry + timedelta(days=30)
    # Tolerance window, not exact equality (clock moves between seed and call).
    assert abs((new_expiry - expected).total_seconds()) < 60


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_expired_client_restores_access(ctx: dict[str, object]) -> None:
    """AC2 end-to-end: an expired client renewed with days can log in again."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=1))
    created.add(client.email)
    # Plan lapsed 60 days ago — anchoring on current expiry would still leave it
    # expired; the add-days anchor counts from today.
    await _set_expires_at(client.id, datetime.now(UTC) - timedelta(days=60))

    res = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"plan_days": 30}
    )
    assert res.status_code == 200, res.text
    new_expiry = datetime.fromisoformat(res.json()["expires_at"])
    assert new_expiry > datetime.now(UTC)

    # The renewed client now logs in normally.
    async with _client() as fresh:
        relog = await fresh.post(
            "/api/auth/login", json={"email": client.email, "password": PASSWORD}
        )
        assert relog.status_code == 200, relog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_with_explicit_future_date_is_verbatim(
    ctx: dict[str, object],
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=5))
    created.add(client.email)

    target = datetime.now(UTC) + timedelta(days=90)
    iso = target.replace(microsecond=0).isoformat()
    res = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"expires_at": iso}
    )
    assert res.status_code == 200, res.text
    persisted = datetime.fromisoformat(res.json()["expires_at"])
    assert abs((persisted - target).total_seconds()) < 2


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_invalid_payloads(ctx: dict[str, object]) -> None:
    """Neither / both / past date → invalid_renewal; bad days → invalid_plan_days."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=5))
    created.add(client.email)
    url = f"/api/admin/users/{client.id}/renew"
    future = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    neither = await admin_client.post(url, json={})
    assert neither.status_code == 400
    assert neither.json()["code"] == "invalid_renewal"

    both = await admin_client.post(url, json={"plan_days": 30, "expires_at": future})
    assert both.status_code == 400
    assert both.json()["code"] == "invalid_renewal"

    past_date = await admin_client.post(url, json={"expires_at": past})
    assert past_date.status_code == 400
    assert past_date.json()["code"] == "invalid_renewal"

    zero = await admin_client.post(url, json={"plan_days": 0})
    assert zero.status_code == 400
    assert zero.json()["code"] == "invalid_plan_days"

    too_big = await admin_client.post(url, json={"plan_days": 36501})
    assert too_big.status_code == 400
    assert too_big.json()["code"] == "invalid_plan_days"

    # Far-future date (beyond now + PLAN_DAYS_MAX) — the overflow guard: a
    # stored huge expiry would make a later add-days renewal exceed datetime.max.
    far = await admin_client.post(url, json={"expires_at": "9999-12-31T00:00:00Z"})
    assert far.status_code == 400
    assert far.json()["code"] == "invalid_renewal"


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_date_cannot_shorten_active_plan(ctx: dict[str, object]) -> None:
    """A date earlier than the current expiry is rejected (renew never shortens)."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    old_expiry = datetime.now(UTC) + timedelta(days=90)
    client = await _seed("client", expires_at=old_expiry)
    created.add(client.email)

    sooner = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    res = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"expires_at": sooner}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "renewal_would_shorten"


# --- Block / unblock (AC3, AC4) -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_block_revokes_sessions_and_blocks_login(ctx: dict[str, object]) -> None:
    """AC3: block revokes live sessions (cookie 401) and the next login 403s."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=30))
    created.add(client.email)

    # The client has a live session (cookie A).
    async with _client() as client_session:
        await login(client_session, client.email)
        alive = await client_session.get("/api/auth/me")
        assert alive.status_code == 200, alive.text

        # Admin blocks them.
        blocked = await admin_client.post(f"/api/admin/users/{client.id}/block")
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["is_blocked"] is True

        # Cookie A is now revoked → 401 not_authenticated (immediate lockout).
        revoked = await client_session.get("/api/auth/me")
        assert revoked.status_code == 401, revoked.text
        assert revoked.json()["code"] == "not_authenticated"

    # A fresh login attempt shows the blocked notice (Story 1.2).
    async with _client() as fresh:
        relog = await fresh.post(
            "/api/auth/login", json={"email": client.email, "password": PASSWORD}
        )
        assert relog.status_code == 403, relog.text
        assert relog.json()["code"] == "account_blocked"


@pytest.mark.asyncio(loop_scope="session")
async def test_unblock_restores_login(ctx: dict[str, object]) -> None:
    """AC4: unblock → the client can log in again normally."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=30))
    created.add(client.email)

    await admin_client.post(f"/api/admin/users/{client.id}/block")
    unblocked = await admin_client.post(f"/api/admin/users/{client.id}/unblock")
    assert unblocked.status_code == 200, unblocked.text
    assert unblocked.json()["is_blocked"] is False

    async with _client() as fresh:
        relog = await fresh.post(
            "/api/auth/login", json={"email": client.email, "password": PASSWORD}
        )
        assert relog.status_code == 200, relog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_blocked_flag_alone_locks_out_live_session(
    ctx: dict[str, object],
) -> None:
    """Defense-in-depth: a session that survived block-time revocation is still
    cut off by ``get_current_user``'s per-request ``is_blocked`` check."""
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=30))
    created.add(client.email)

    async with _client() as client_session:
        await login(client_session, client.email)
        # Flip the flag WITHOUT revoking — the surviving-session race.
        await _set_blocked_flag(client.id, True)

        res = await client_session.get("/api/auth/me")
        assert res.status_code == 401, res.text
        assert res.json()["code"] == "not_authenticated"


@pytest.mark.asyncio(loop_scope="session")
async def test_block_is_idempotent(ctx: dict[str, object]) -> None:
    """Re-blocking an already-blocked client is a no-op 200."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=30))
    created.add(client.email)

    first = await admin_client.post(f"/api/admin/users/{client.id}/block")
    assert first.status_code == 200, first.text
    second = await admin_client.post(f"/api/admin/users/{client.id}/block")
    assert second.status_code == 200, second.text
    assert second.json()["is_blocked"] is True


# --- Independence of the two gates ----------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_renew_does_not_touch_blocked_flag(ctx: dict[str, object]) -> None:
    """A blocked client whose plan is renewed stays blocked (gates independent)."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=5))
    created.add(client.email)

    await admin_client.post(f"/api/admin/users/{client.id}/block")
    renewed = await admin_client.post(
        f"/api/admin/users/{client.id}/renew", json={"plan_days": 30}
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["is_blocked"] is True  # block untouched by renew


# --- Authorization --------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_actions_on_admin_target_are_forbidden(ctx: dict[str, object]) -> None:
    """All three actions reject a non-client (admin) target with 403 forbidden."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin: User = ctx["admin"]  # type: ignore[assignment]

    for action, payload in (
        ("renew", {"plan_days": 30}),
        ("block", None),
        ("unblock", None),
    ):
        res = await owner_client.post(
            f"/api/admin/users/{admin.id}/{action}", json=payload
        )
        assert res.status_code == 403, f"{action}: {res.text}"
        assert res.json()["code"] == "forbidden"


@pytest.mark.asyncio(loop_scope="session")
async def test_actions_on_unknown_user_are_404(ctx: dict[str, object]) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    for action, payload in (
        ("renew", {"plan_days": 30}),
        ("block", None),
        ("unblock", None),
    ):
        res = await admin_client.post(
            f"/api/admin/users/999999999/{action}", json=payload
        )
        assert res.status_code == 404, f"{action}: {res.text}"
        assert res.json()["code"] == "user_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_client_caller_is_forbidden(ctx: dict[str, object]) -> None:
    """A client-role caller hits require_admin_or_owner → 403 forbidden."""
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    caller = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=30))
    target = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=30))
    created.update({caller.email, target.email})

    async with _client() as client_session:
        await login(client_session, caller.email)
        res = await client_session.post(
            f"/api/admin/users/{target.id}/block"
        )
        assert res.status_code == 403, res.text
        assert res.json()["code"] == "forbidden"


@pytest.mark.asyncio(loop_scope="session")
async def test_both_admin_and_owner_can_act(ctx: dict[str, object]) -> None:
    """FR4 'admin or owner': both actors can renew/block a client."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    c1 = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=10))
    c2 = await _seed("client", expires_at=datetime.now(UTC) + timedelta(days=10))
    created.update({c1.email, c2.email})

    by_admin = await admin_client.post(
        f"/api/admin/users/{c1.id}/renew", json={"plan_days": 15}
    )
    assert by_admin.status_code == 200, by_admin.text

    by_owner = await owner_client.post(f"/api/admin/users/{c2.id}/block")
    assert by_owner.status_code == 200, by_owner.text
    assert by_owner.json()["is_blocked"] is True
