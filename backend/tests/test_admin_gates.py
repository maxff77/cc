"""Integration tests for the gate catalog (Story 2.1).

Owner-only CRUD on ``/api/admin/gates`` + the read-only ``/api/gates`` feed.
Drives the real ASGI app against the dev Postgres (same shape as
``test_admin_users``): self-seeding, self-cleaning — created gate rows are
deleted directly on teardown (soft-deleted ones included).

Run (from backend/, venv active):  pytest tests/test_admin_gates.py
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import Gate
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from tests.conftest import cleanup_users, login, seed_user


def unique_gate_value() -> str:
    """Collision-free gate value, verbatim-with-dot shape (≤20 chars)."""
    return f".t{uuid.uuid4().hex[:6]}"


@pytest_asyncio.fixture(loop_scope="session")
async def gates_created() -> AsyncIterator[set[str]]:
    """Track gate values created by a test; delete their rows on teardown.

    Matches by value with no ``deleted_at`` filter so soft-deleted rows are
    removed too.
    """
    values: set[str] = set()
    yield values
    if values:
        async with async_session_factory() as session:
            await session.execute(delete(Gate).where(Gate.value.in_(values)))
            await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def client_client() -> AsyncIterator[AsyncClient]:
    """A logged-in CLIENT-role http client (ctx only seeds owner + admin)."""
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    transport_client = AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    )
    await login(transport_client, user.email)
    yield transport_client
    await transport_client.aclose()
    await cleanup_users({user.email})


async def _create_gate(
    owner_client: AsyncClient, value: str, created: set[str]
) -> dict[str, object]:
    created.add(value)
    res = await owner_client.post("/api/admin/gates", json={"value": value})
    assert res.status_code == 201, res.text
    return res.json()


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_creates_gate_verbatim(
    ctx: dict[str, object], gates_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    value = unique_gate_value()
    body = await _create_gate(owner_client, value, gates_created)
    assert body["value"] == value  # verbatim, dot included
    assert body["id"] > 0
    assert body["created_at"] is not None

    listed = await owner_client.get("/api/admin/gates")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert value in [g["value"] for g in items]
    assert listed.json()["total"] == len(items)


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_active_value_is_gate_exists(
    ctx: dict[str, object], gates_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    value = unique_gate_value()
    await _create_gate(owner_client, value, gates_created)
    dup = await owner_client.post("/api/admin/gates", json={"value": value})
    assert dup.status_code == 409
    assert dup.json()["code"] == "gate_exists"


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_edits_gate_value(
    ctx: dict[str, object], gates_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    body = await _create_gate(owner_client, unique_gate_value(), gates_created)
    new_value = unique_gate_value()
    gates_created.add(new_value)
    res = await owner_client.patch(
        f"/api/admin/gates/{body['id']}", json={"value": new_value}
    )
    assert res.status_code == 200, res.text
    assert res.json()["value"] == new_value

    listed = await owner_client.get("/api/admin/gates")
    assert new_value in [g["value"] for g in listed.json()["items"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_to_duplicate_value_is_gate_exists(
    ctx: dict[str, object], gates_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    other = await _create_gate(owner_client, unique_gate_value(), gates_created)
    target = await _create_gate(owner_client, unique_gate_value(), gates_created)
    res = await owner_client.patch(
        f"/api/admin/gates/{target['id']}", json={"value": other["value"]}
    )
    assert res.status_code == 409
    assert res.json()["code"] == "gate_exists"


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_is_soft_and_hides_from_both_lists(
    ctx: dict[str, object], gates_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    body = await _create_gate(owner_client, unique_gate_value(), gates_created)
    res = await owner_client.delete(f"/api/admin/gates/{body['id']}")
    assert res.status_code == 204

    admin_list = await owner_client.get("/api/admin/gates")
    assert body["value"] not in [g["value"] for g in admin_list.json()["items"]]
    open_list = await owner_client.get("/api/gates")
    assert body["value"] not in [g["value"] for g in open_list.json()["items"]]

    # AC5: the row still exists, retired (deleted_at set) — soft-delete.
    async with async_session_factory() as session:
        row = (
            await session.execute(select(Gate).where(Gate.id == body["id"]))
        ).scalar_one()
        assert row.deleted_at is not None

    # Deleting again (already retired) → 404 gate_not_found.
    again = await owner_client.delete(f"/api/admin/gates/{body['id']}")
    assert again.status_code == 404
    assert again.json()["code"] == "gate_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_retired_value_can_be_recreated(
    ctx: dict[str, object], gates_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    body = await _create_gate(owner_client, unique_gate_value(), gates_created)
    res = await owner_client.delete(f"/api/admin/gates/{body['id']}")
    assert res.status_code == 204
    # Partial unique index only covers active rows → re-create succeeds.
    recreated = await _create_gate(owner_client, body["value"], gates_created)  # type: ignore[arg-type]
    assert recreated["id"] != body["id"]


@pytest.mark.asyncio(loop_scope="session")
async def test_edit_or_delete_unknown_gate_is_gate_not_found(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    res = await owner_client.patch("/api/admin/gates/999999", json={"value": ".x"})
    assert res.status_code == 404
    assert res.json()["code"] == "gate_not_found"
    res = await owner_client.delete("/api/admin/gates/999999")
    assert res.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_and_client_are_forbidden_on_admin_gates(
    ctx: dict[str, object], client_client: AsyncClient
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    for http_client in (admin_client, client_client):
        assert (await http_client.get("/api/admin/gates")).status_code == 403
        assert (
            await http_client.post("/api/admin/gates", json={"value": ".nope"})
        ).status_code == 403
        assert (
            await http_client.patch("/api/admin/gates/1", json={"value": ".nope"})
        ).status_code == 403
        assert (await http_client.delete("/api/admin/gates/1")).status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_client_reads_catalog_active_only(
    ctx: dict[str, object],
    client_client: AsyncClient,
    gates_created: set[str],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    active = await _create_gate(owner_client, unique_gate_value(), gates_created)
    retired = await _create_gate(owner_client, unique_gate_value(), gates_created)
    assert (
        await owner_client.delete(f"/api/admin/gates/{retired['id']}")
    ).status_code == 204

    res = await client_client.get("/api/gates")
    assert res.status_code == 200, res.text
    values = [g["value"] for g in res.json()["items"]]
    assert active["value"] in values
    assert retired["value"] not in values


@pytest.mark.asyncio(loop_scope="session")
async def test_unauthenticated_gates_read_is_401(ctx: dict[str, object]) -> None:
    anon = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        assert (await anon.get("/api/gates")).status_code == 401
    finally:
        await anon.aclose()


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "bad_value",
    ["", "   ", ".z o", ".tab\there", "." + "x" * 20],
    ids=["empty", "whitespace-only", "inner-space", "inner-tab", "too-long"],
)
async def test_validation_rejects_bad_values(
    ctx: dict[str, object], bad_value: str
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    res = await owner_client.post("/api/admin/gates", json={"value": bad_value})
    assert res.status_code == 422


@pytest.mark.asyncio(loop_scope="session")
async def test_value_is_trimmed_but_otherwise_verbatim(
    ctx: dict[str, object], gates_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    raw = unique_gate_value()
    body = await _create_gate(owner_client, f"  {raw}  ", gates_created)
    gates_created.add(raw)
    assert body["value"] == raw  # trimmed; dot and case untouched
