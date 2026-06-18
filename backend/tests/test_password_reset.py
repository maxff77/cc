"""Integration tests for password reset + forced change (Story 1.6).

Drives the real ASGI app (httpx ``ASGITransport``) against the dev Postgres,
mirroring ``test_admin_lifecycle.py``: self-seeding with unique emails,
self-cleaning on teardown, all pinned to ``loop_scope="session"`` so they share
the async engine pool. Seed/login/cleanup helpers come from ``tests.conftest``.

The critical round-trips: reset → old sessions 401 + temp login lands flagged
(AC1/AC2 entry), flagged session 403s ``/me`` repeatably (AC2), change → same
session works + new password logs in (AC3).

Run (from backend/, venv active):  pytest tests/test_password_reset.py
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import User
from app.main import app
from httpx import ASGITransport, AsyncClient

from tests.conftest import PASSWORD, login, seed_user

# The shared `ctx` fixture (owner + admin, logged in, self-cleaning) lives in
# tests/conftest.py.


async def _seed_client(*, days: int = 30) -> User:
    return await seed_user(
        "client",
        expires_at=datetime.now(UTC) + timedelta(days=days),
        email_prefix="test-pwreset",
    )


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _reset(admin_client: AsyncClient, user_id: int) -> str:
    """Reset ``user_id``'s password; assert 200 and return the temp password."""
    res = await admin_client.post(f"/api/admin/users/{user_id}/reset-password")
    assert res.status_code == 200, res.text
    temp = res.json()["temp_password"]
    assert temp
    return temp


# --- Reset (AC1) ------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_happy_path(ctx: dict[str, object]) -> None:
    """AC1 + AC2 entry: temp password replaces the old one and steers login."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed_client()
    created.add(client.email)

    temp = await _reset(admin_client, client.id)
    assert temp != PASSWORD

    # Old password no longer works.
    async with _client() as fresh:
        old = await fresh.post(
            "/api/auth/login", json={"email": client.email, "password": PASSWORD}
        )
        assert old.status_code == 401, old.text
        assert old.json()["code"] == "invalid_credentials"

    # Temp password logs in and home_path steers to the forced screen.
    async with _client() as fresh:
        res = await fresh.post(
            "/api/auth/login", json={"email": client.email, "password": temp}
        )
        assert res.status_code == 200, res.text
        assert res.json()["home_path"] == "/change-password"


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_revokes_live_sessions(ctx: dict[str, object]) -> None:
    """A live session (cookie A) dies the instant the reset runs."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed_client()
    created.add(client.email)

    async with _client() as client_session:
        await login(client_session, client.email)
        alive = await client_session.get("/api/auth/me")
        assert alive.status_code == 200, alive.text

        await _reset(admin_client, client.id)

        revoked = await client_session.get("/api/auth/me")
        assert revoked.status_code == 401, revoked.text
        assert revoked.json()["code"] == "not_authenticated"


# --- Flag gate (AC2) --------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_flag_gates_everything_repeatably(ctx: dict[str, object]) -> None:
    """A flagged session 403s password_change_required — repeatable, NOT one-shot."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed_client()
    created.add(client.email)
    temp = await _reset(admin_client, client.id)

    async with _client() as session:
        res = await session.post(
            "/api/auth/login", json={"email": client.email, "password": temp}
        )
        assert res.status_code == 200, res.text

        for _ in range(2):  # twice: the 403 must not consume the session
            gated = await session.get("/api/auth/me")
            assert gated.status_code == 403, gated.text
            assert gated.json()["code"] == "password_change_required"


# --- Forced change (AC3) ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_forced_change_happy_path(ctx: dict[str, object]) -> None:
    """AC3 end-to-end: change clears the flag, keeps the session, swaps the hash."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed_client()
    created.add(client.email)
    temp = await _reset(admin_client, client.id)
    new_password = "brand-new-pass-1"

    async with _client() as session:
        res = await session.post(
            "/api/auth/login", json={"email": client.email, "password": temp}
        )
        assert res.status_code == 200, res.text

        changed = await session.post(
            "/api/auth/change-password",
            json={"current_password": temp, "new_password": new_password},
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["home_path"] == "/app"  # client role home

        # Same cookie now passes — flag cleared, session kept (no re-login).
        me = await session.get("/api/auth/me")
        assert me.status_code == 200, me.text

    # Temp password is dead; the new one logs in with the normal home_path.
    async with _client() as fresh:
        dead = await fresh.post(
            "/api/auth/login", json={"email": client.email, "password": temp}
        )
        assert dead.status_code == 401, dead.text

        ok = await fresh.post(
            "/api/auth/login",
            json={"email": client.email, "password": new_password},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["home_path"] == "/app"


@pytest.mark.asyncio(loop_scope="session")
async def test_change_password_guards(ctx: dict[str, object]) -> None:
    """Wrong current → 401; reuse → 400; short/long → 422; no cookie → 401;
    non-flagged session → 403."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed_client()
    created.add(client.email)
    temp = await _reset(admin_client, client.id)

    async with _client() as session:
        res = await session.post(
            "/api/auth/login", json={"email": client.email, "password": temp}
        )
        assert res.status_code == 200, res.text

        # Proof of the temp password is required (1.6 review): a session that
        # survived the reset's revoke can't set the new password blind.
        wrong = await session.post(
            "/api/auth/change-password",
            json={"current_password": "not-the-temp", "new_password": "valid-pass-99"},
        )
        assert wrong.status_code == 401, wrong.text
        assert wrong.json()["code"] == "invalid_credentials"

        reuse = await session.post(
            "/api/auth/change-password",
            json={"current_password": temp, "new_password": temp},
        )
        assert reuse.status_code == 400, reuse.text
        assert reuse.json()["code"] == "password_reuse"

        short = await session.post(
            "/api/auth/change-password",
            json={"current_password": temp, "new_password": "short-7"},
        )
        assert short.status_code == 422, short.text

        # Upper bound: an unbounded password would feed argon2 unthrottled.
        long = await session.post(
            "/api/auth/change-password",
            json={"current_password": temp, "new_password": "x" * 129},
        )
        assert long.status_code == 422, long.text

    async with _client() as anon:
        no_cookie = await anon.post(
            "/api/auth/change-password",
            json={"current_password": temp, "new_password": "valid-pass-99"},
        )
        assert no_cookie.status_code == 401, no_cookie.text
        assert no_cookie.json()["code"] == "not_authenticated"

    # A NON-flagged session may not use the endpoint (forced flow only).
    not_flagged = await admin_client.post(
        "/api/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "valid-pass-99"},
    )
    assert not_flagged.status_code == 403, not_flagged.text
    assert not_flagged.json()["code"] == "forbidden"


@pytest.mark.asyncio(loop_scope="session")
async def test_change_revokes_other_sessions(ctx: dict[str, object]) -> None:
    """Completing the change kills every OTHER session (1.6 review): a second
    device that logged in with the leaked temp password dies instantly."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed_client()
    created.add(client.email)
    temp = await _reset(admin_client, client.id)

    async with _client() as device_a, _client() as device_b:
        for device in (device_a, device_b):
            res = await device.post(
                "/api/auth/login", json={"email": client.email, "password": temp}
            )
            assert res.status_code == 200, res.text

        changed = await device_a.post(
            "/api/auth/change-password",
            json={"current_password": temp, "new_password": "brand-new-pass-2"},
        )
        assert changed.status_code == 200, changed.text

        # Device A (the one that proved the temp password) continues…
        me_a = await device_a.get("/api/auth/me")
        assert me_a.status_code == 200, me_a.text

        # …device B is revoked.
        me_b = await device_b.get("/api/auth/me")
        assert me_b.status_code == 401, me_b.text
        assert me_b.json()["code"] == "not_authenticated"


# --- Authorization on reset -------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_authorization(ctx: dict[str, object]) -> None:
    """Admin target → 403; unknown id → 404; client caller → 403."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin: User = ctx["admin"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    non_client = await owner_client.post(
        f"/api/admin/users/{admin.id}/reset-password"
    )
    assert non_client.status_code == 403, non_client.text
    assert non_client.json()["code"] == "forbidden"

    unknown = await admin_client.post("/api/admin/users/999999999/reset-password")
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["code"] == "user_not_found"

    caller = await _seed_client()
    target = await _seed_client()
    created.update({caller.email, target.email})
    async with _client() as client_session:
        await login(client_session, caller.email)
        res = await client_session.post(
            f"/api/admin/users/{target.id}/reset-password"
        )
        assert res.status_code == 403, res.text
        assert res.json()["code"] == "forbidden"


@pytest.mark.asyncio(loop_scope="session")
async def test_both_admin_and_owner_can_reset(ctx: dict[str, object]) -> None:
    """FR6 'admin or owner': both actors succeed."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    c1 = await _seed_client()
    c2 = await _seed_client()
    created.update({c1.email, c2.email})

    await _reset(admin_client, c1.id)
    await _reset(owner_client, c2.id)


# --- Independence of gates --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_blocked_client_keeps_blocked_gate(
    ctx: dict[str, object],
) -> None:
    """Reset works on a BLOCKED client; their temp login still 403s blocked."""
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    client = await _seed_client()
    created.add(client.email)

    blocked = await admin_client.post(f"/api/admin/users/{client.id}/block")
    assert blocked.status_code == 200, blocked.text

    temp = await _reset(admin_client, client.id)

    # The blocked gate fires first — gate order untouched by the flag.
    async with _client() as fresh:
        res = await fresh.post(
            "/api/auth/login", json={"email": client.email, "password": temp}
        )
        assert res.status_code == 403, res.text
        assert res.json()["code"] == "account_blocked"
