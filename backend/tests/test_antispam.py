"""Tests for the antispam-per-user feature: antispam decoupled from plans into
an owner-set GLOBAL default + a per-user OVERRIDE, resolved by the scheduler as
``coalesce(User.antispam_seconds, default)`` (and ``0.0`` for owner/admin house
tenants).

Covers the spec's I/O & Edge-Case Matrix:
- the global default service (parse bounds, config fallback, round-trip);
- owner-only ``GET/PUT /api/admin/antispam`` (default, persist, bounds, finite);
- owner-only ``POST /api/admin/users/{id}/antispam`` (set / clear / zero / bounds
  / non-client / owner-only);
- the ``active_senders`` DB resolution: override wins, else the global default,
  and a house tenant resolves to 0.0 (never gated).

Conftest idiom: real ASGI app + dev Postgres, self-seeding/self-cleaning. The
``default_antispam_seconds`` row is global state, so the local autouse fixture
wipes it around every test.

Run (from backend/, venv active):  pytest tests/test_antispam.py
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.config import settings
from app.db.base import async_session_factory
from app.db.models import SystemSetting, User
from app.db.repos import batches as batches_repo
from app.main import app
from app.services import antispam as antispam_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, update

from tests.conftest import login, seed_user

DEFAULT_KEY = antispam_service.DEFAULT_ANTISPAM_KEY
ENV_DEFAULT = settings.scheduler_default_antispam_seconds


# --- Local fixtures / helpers ------------------------------------------------


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_default_antispam() -> AsyncIterator[None]:
    """Wipe the global default row around every test (shared knob)."""

    async def _wipe() -> None:
        async with async_session_factory() as session:
            await session.execute(
                delete(SystemSetting).where(SystemSetting.key == DEFAULT_KEY)
            )
            await session.commit()

    await _wipe()
    yield
    await _wipe()


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _post_batch(http: AsyncClient, gate_id: int, text: str = "x") -> object:
    return await http.post("/api/batches", json={"text": text, "gate_id": gate_id})


async def _set_override(user_id: int, value: float | None) -> None:
    """Write the per-user override directly (the scheduler reads it from the row)."""
    async with async_session_factory() as session:
        await session.execute(
            update(User).where(User.id == user_id).values(antispam_seconds=value)
        )
        await session.commit()


# --- Unit: defensive parse ----------------------------------------------------


def test_parse_default_accepts_valid_including_bounds() -> None:
    assert antispam_service._parse_default("1") == 1.0  # min inclusive
    assert antispam_service._parse_default("15.5") == 15.5
    assert antispam_service._parse_default("30") == 30.0  # max inclusive


def test_parse_default_rejects_garbage_and_out_of_range() -> None:
    for bad in (None, "", "abc", "0", "0.9", "30.1", "100", "-3"):
        assert antispam_service._parse_default(bad) is None


def test_config_default_antispam_clamped_to_band() -> None:
    """The env fallback is held to [1, 30] at LOAD (a typo'd .env can't push the
    default past the scheduler's prune cutoff). The suite neutralizes the cooldown
    by assigning this field directly (bypassing validation) — see conftest."""
    from app.config import Settings

    db = "postgresql+asyncpg://u:p@h/db"
    mk = lambda v: Settings(  # noqa: E731 — terse one-off factory
        database_url=db, scheduler_default_antispam_seconds=v
    ).scheduler_default_antispam_seconds

    assert mk(99) == 30.0  # above ceiling → clamped
    assert mk(0.1) == 1.0  # below floor → clamped
    assert mk(15) == 15.0  # in band → unchanged


# --- Service: fallback + round-trip -------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_get_default_falls_back_to_config_when_unset() -> None:
    async with async_session_factory() as session:
        assert await antispam_service.get_default(session) == ENV_DEFAULT


@pytest.mark.asyncio(loop_scope="session")
async def test_set_default_round_trip() -> None:
    async with async_session_factory() as session:
        await antispam_service.set_default(session, 22.0)
        await session.commit()
    async with async_session_factory() as session:
        assert await antispam_service.get_default(session) == 22.0


# --- Owner knob: GET/PUT /api/admin/antispam ----------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_default_antispam_defaults_to_config(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.get("/api/admin/antispam")
    assert res.status_code == 200
    assert res.json() == {"antispam_seconds": ENV_DEFAULT}


@pytest.mark.asyncio(loop_scope="session")
async def test_default_antispam_put_persists(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.put("/api/admin/antispam", json={"antispam_seconds": 18.0})
    assert res.status_code == 200, res.text
    assert res.json() == {"antispam_seconds": 18.0}
    again = await owner_client.get("/api/admin/antispam")
    assert again.json() == {"antispam_seconds": 18.0}


@pytest.mark.asyncio(loop_scope="session")
async def test_default_antispam_put_rejects_out_of_bounds(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for bad in (0, 0.9, 30.1, -1):
        res = await owner_client.put(
            "/api/admin/antispam", json={"antispam_seconds": bad}
        )
        assert res.status_code == 400, bad
        assert res.json()["code"] == "invalid_antispam"


@pytest.mark.asyncio(loop_scope="session")
async def test_default_antispam_put_rejects_non_finite(ctx: dict[str, object]) -> None:
    """NaN/±Inf can't arrive from a browser but a hand-crafted payload can; the
    isfinite guard must reject it before it persists (raw content because httpx's
    ``json=`` refuses to serialize them)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for literal in ("NaN", "Infinity", "-Infinity"):
        res = await owner_client.put(
            "/api/admin/antispam",
            content=f'{{"antispam_seconds": {literal}}}',
            headers={"content-type": "application/json"},
        )
        assert res.status_code == 400, literal
        assert res.json()["code"] == "invalid_antispam"


@pytest.mark.asyncio(loop_scope="session")
async def test_default_antispam_endpoints_are_owner_only(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    client_http, _ = client_user
    for http in (admin_client, client_http):
        assert (await http.get("/api/admin/antispam")).status_code == 403
        res = await http.put("/api/admin/antispam", json={"antispam_seconds": 10.0})
        assert res.status_code == 403


# --- Per-user override: POST /api/admin/users/{id}/antispam -------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_set_user_antispam_override(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    _, client = client_user
    res = await owner_client.post(
        f"/api/admin/users/{client.id}/antispam", json={"antispam_seconds": 4.0}
    )
    assert res.status_code == 200, res.text
    assert res.json()["antispam_seconds"] == 4.0
    async with async_session_factory() as session:
        row = await session.get(User, client.id)
        assert row is not None and float(row.antispam_seconds) == 4.0


@pytest.mark.asyncio(loop_scope="session")
async def test_clear_user_antispam_override(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    _, client = client_user
    await owner_client.post(
        f"/api/admin/users/{client.id}/antispam", json={"antispam_seconds": 4.0}
    )
    res = await owner_client.post(
        f"/api/admin/users/{client.id}/antispam", json={"antispam_seconds": None}
    )
    assert res.status_code == 200, res.text
    assert res.json()["antispam_seconds"] is None
    async with async_session_factory() as session:
        row = await session.get(User, client.id)
        assert row is not None and row.antispam_seconds is None


@pytest.mark.asyncio(loop_scope="session")
async def test_set_user_antispam_allows_zero(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    """0 = no per-tenant cooldown (the fastest a client can be sold)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    _, client = client_user
    res = await owner_client.post(
        f"/api/admin/users/{client.id}/antispam", json={"antispam_seconds": 0}
    )
    assert res.status_code == 200, res.text
    assert res.json()["antispam_seconds"] == 0.0


@pytest.mark.asyncio(loop_scope="session")
async def test_set_user_antispam_rejects_out_of_bounds(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    _, client = client_user
    for bad in (-0.1, 30.1):
        res = await owner_client.post(
            f"/api/admin/users/{client.id}/antispam", json={"antispam_seconds": bad}
        )
        assert res.status_code == 400, bad
        assert res.json()["code"] == "invalid_antispam"


@pytest.mark.asyncio(loop_scope="session")
async def test_set_user_antispam_non_client_forbidden(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]
    other_admin = await seed_user("admin")
    created.add(other_admin.email)
    res = await owner_client.post(
        f"/api/admin/users/{other_admin.id}/antispam", json={"antispam_seconds": 4.0}
    )
    assert res.status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_set_user_antispam_is_owner_only(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    _, client = client_user
    res = await admin_client.post(
        f"/api/admin/users/{client.id}/antispam", json={"antispam_seconds": 4.0}
    )
    assert res.status_code == 403


# --- active_senders DB resolution: coalesce(override, default), house→0 -------


@pytest.mark.asyncio(loop_scope="session")
async def test_active_senders_resolves_override_default_and_house(
    ctx: dict[str, object], gate: dict
) -> None:
    """The load-bearing join: a client with an override carries it; a client
    without one resolves to the passed-in global default; an owner/admin house
    tenant (no client row) resolves to 0.0 — never gated."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    overridden = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    created.add(overridden.email)
    await _set_override(overridden.id, 8.0)

    defaulted = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    created.add(defaulted.email)
    assert defaulted.antispam_seconds is None  # seeded with no override

    async with _client() as a, _client() as b:
        await login(a, overridden.email)
        await login(b, defaulted.email)
        assert (await _post_batch(a, gate["id"])).status_code == 201
        assert (await _post_batch(b, gate["id"])).status_code == 201
    # The owner posts under the shared "house" tenant (no client row).
    assert (await _post_batch(owner_client, gate["id"])).status_code == 201

    async with async_session_factory() as session:
        senders = await batches_repo.active_senders(session, default_antispam=15.0)
    by_tenant = {s.tenant_id: s.antispam_seconds for s in senders}

    assert by_tenant.get(overridden.tenant_id) == 8.0  # override wins
    assert by_tenant.get(defaulted.tenant_id) == 15.0  # falls back to default
    # Every remaining (house) tenant resolves to 0.0 — never the default.
    house = [
        v
        for t, v in by_tenant.items()
        if t not in (overridden.tenant_id, defaulted.tenant_id)
    ]
    assert house and all(v == 0.0 for v in house)
