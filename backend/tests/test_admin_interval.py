"""Tests for the configurable send interval: the owner-only knob that sets
the scheduler's constant floor ``G`` live, persists it in ``system_settings``,
and restores it at boot.

Conftest idiom: real ASGI app + dev Postgres, self-seeding/self-cleaning. The
``send_interval_seconds`` row is global state shared across tenants, so the
local autouse fixture wipes it around every test; the conftest's autouse
``reset_scheduler`` returns the singleton floor to the env default per test.

Run (from backend/, venv active):  pytest tests/test_admin_interval.py
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core.scheduler import scheduler
from app.db.base import async_session_factory
from app.db.models import SystemSetting, User
from app.services import pacing as pacing_service
from httpx import AsyncClient
from sqlalchemy import delete

# --- Local fixtures -----------------------------------------------------------


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_interval() -> AsyncIterator[None]:
    """Wipe the interval row around every test (global knob, shared DB)."""
    async def _wipe() -> None:
        async with async_session_factory() as session:
            await session.execute(
                delete(SystemSetting).where(
                    SystemSetting.key == pacing_service.INTERVAL_KEY
                )
            )
            await session.commit()

    await _wipe()
    yield
    await _wipe()


async def _set_interval(owner_client: AsyncClient, seconds: float) -> None:
    res = await owner_client.put(
        "/api/admin/interval", json={"interval_seconds": seconds}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"interval_seconds": seconds}


# --- Unit: defensive parse ----------------------------------------------------


def test_parse_interval_accepts_valid_including_bounds() -> None:
    assert pacing_service._parse_interval("4.0") == 4.0
    assert pacing_service._parse_interval("0") == 0.0  # min inclusive (floor removed)
    assert pacing_service._parse_interval("1.9") == 1.9  # below old 2s floor, now valid
    assert pacing_service._parse_interval("30") == 30.0  # max inclusive


def test_parse_interval_rejects_garbage_and_out_of_range() -> None:
    for bad in (None, "", "abc", "30.1", "100", "-3"):
        assert pacing_service._parse_interval(bad) is None


# --- Owner knob: GET/PUT /api/admin/interval ----------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_interval_defaults_to_env(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.get("/api/admin/interval")
    assert res.status_code == 200
    assert res.json() == {"interval_seconds": 4.0}


@pytest.mark.asyncio(loop_scope="session")
async def test_interval_put_persists_and_applies_to_scheduler(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_interval(owner_client, 6.0)

    # Persisted (survives a fresh read)…
    res = await owner_client.get("/api/admin/interval")
    assert res.json() == {"interval_seconds": 6.0}
    # …and applied live to the scheduler floor — no restart needed.
    assert scheduler.floor == 6.0


@pytest.mark.asyncio(loop_scope="session")
async def test_interval_put_rejects_out_of_bounds(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for bad in (-0.1, 30.1, -3):
        res = await owner_client.put(
            "/api/admin/interval", json={"interval_seconds": bad}
        )
        assert res.status_code == 400, bad
        assert res.json()["code"] == "invalid_send_interval"


@pytest.mark.asyncio(loop_scope="session")
async def test_interval_put_accepts_zero_floor_removed(ctx: dict[str, object]) -> None:
    """Anti-ban floor removed on owner request: 0 (and sub-2s values) now pass."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for ok in (0, 0.5, 1.9):
        res = await owner_client.put(
            "/api/admin/interval", json={"interval_seconds": ok}
        )
        assert res.status_code == 200, ok
        assert res.json()["interval_seconds"] == ok
        assert scheduler.floor == ok


@pytest.mark.asyncio(loop_scope="session")
async def test_interval_put_rejects_non_finite(ctx: dict[str, object]) -> None:
    """NaN/±Inf can't arrive from a browser (JSON.stringify → null) but a
    hand-crafted non-standard-JSON payload can; the isfinite guard must reject
    it before it reaches set_floor (a NaN floor would break pacing forever).
    Sent as raw content because httpx's ``json=`` refuses to serialize them."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    for literal in ("NaN", "Infinity", "-Infinity"):
        res = await owner_client.put(
            "/api/admin/interval",
            content=f'{{"interval_seconds": {literal}}}',
            headers={"content-type": "application/json"},
        )
        assert res.status_code == 400, literal
        assert res.json()["code"] == "invalid_send_interval"


@pytest.mark.asyncio(loop_scope="session")
async def test_interval_endpoints_are_owner_only(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    client_http, _ = client_user
    for http in (admin_client, client_http):
        assert (await http.get("/api/admin/interval")).status_code == 403
        res = await http.put(
            "/api/admin/interval", json={"interval_seconds": 5.0}
        )
        assert res.status_code == 403


# --- Boot: persisted interval restores the scheduler floor --------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_apply_persisted_restores_floor_on_boot() -> None:
    async with async_session_factory() as session:
        await pacing_service.set_interval(session, 7.0)
        await session.commit()

    scheduler.reset()  # simulate a process restart
    assert scheduler.floor == 4.0  # env default before the boot loader runs

    async with async_session_factory() as session:
        await pacing_service.apply_persisted(session)
    assert scheduler.floor == 7.0
