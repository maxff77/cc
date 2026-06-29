"""Tests for editable support contacts: the owner-only handle list persisted in
``system_settings`` and read publicly by ``/api/public/support-contacts``.

Conftest idiom: real ASGI app + dev Postgres. The ``support_contacts`` row is
global state shared across tenants, so the local autouse fixture wipes it around
every test.

Run (from backend/, venv active):  pytest tests/test_support_contacts.py
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import SystemSetting, User
from app.main import app
from app.services import support_contacts as support_contacts_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

# --- Local fixtures -----------------------------------------------------------


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_support_contacts() -> AsyncIterator[None]:
    """Wipe the support-contacts row around every test (global knob, shared DB)."""

    async def _wipe() -> None:
        async with async_session_factory() as session:
            await session.execute(
                delete(SystemSetting).where(
                    SystemSetting.key
                    == support_contacts_service.SUPPORT_CONTACTS_KEY
                )
            )
            await session.commit()

    await _wipe()
    yield
    await _wipe()


def _handles(payload: dict) -> list[str]:
    return [c["handle"] for c in payload["contacts"]]


# --- Defaults: unset row → the pre-feature handles ----------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_unset_returns_default_handles(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.get("/api/admin/support-contacts")
    assert res.status_code == 200, res.text
    assert _handles(res.json()) == list(
        support_contacts_service.DEFAULT_HANDLES
    )


# --- PUT: normalize, dedupe, persist ------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_put_normalizes_dedupes_and_drops_blanks(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.put(
        "/api/admin/support-contacts",
        json={"handles": ["@AionRanger", "t.me/Soporte", "soporte", "   ", ""]},
    )
    assert res.status_code == 200, res.text
    # '@' + t.me prefixes stripped; 'soporte' deduped against 'Soporte'
    # (case-insensitive, first wins); blanks dropped.
    assert _handles(res.json()) == ["AionRanger", "Soporte"]

    # Persisted: a fresh read returns the stored list, not the defaults.
    again = await owner_client.get("/api/admin/support-contacts")
    assert _handles(again.json()) == ["AionRanger", "Soporte"]


@pytest.mark.asyncio(loop_scope="session")
async def test_put_rejects_all_blank(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.put(
        "/api/admin/support-contacts", json={"handles": ["  ", ""]}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "support_contacts_empty"


@pytest.mark.asyncio(loop_scope="session")
async def test_put_rejects_malformed_handle(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    # 'x' normalizes to a 1-char handle → below the 5-char floor.
    res = await owner_client.put(
        "/api/admin/support-contacts", json={"handles": ["@x"]}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_contact"


@pytest.mark.asyncio(loop_scope="session")
async def test_put_rejects_too_many(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    too_many = [f"handle{i:02d}" for i in range(9)]  # 9 > MAX (8)
    res = await owner_client.put(
        "/api/admin/support-contacts", json={"handles": too_many}
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "too_many_support_contacts"


# --- Authorization: owner-only ------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_endpoints_are_owner_only(
    ctx: dict[str, object], client_user: tuple[AsyncClient, User]
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    client_http, _ = client_user
    for http in (admin_client, client_http):
        assert (
            await http.get("/api/admin/support-contacts")
        ).status_code == 403
        res = await http.put(
            "/api/admin/support-contacts", json={"handles": ["AionRanger"]}
        )
        assert res.status_code == 403


# --- Public read reflects the owner's list ------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_public_endpoint_reflects_saved_list(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await owner_client.put(
        "/api/admin/support-contacts",
        json={"handles": ["PrimarioMX", "SecundarioMX"]},
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as anon:
        res = await anon.get("/api/public/support-contacts")
    assert res.status_code == 200, res.text
    assert _handles(res.json()) == ["PrimarioMX", "SecundarioMX"]
