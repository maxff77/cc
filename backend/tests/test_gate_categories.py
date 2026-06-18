"""Integration tests for gate categories (Story 2.2, Task 0).

Owner-only CRUD on ``/api/admin/gate-categories`` + the category fields the
gates API gained. Same shape as ``test_admin_gates``: drives the real ASGI
app against the dev Postgres, self-seeding, self-cleaning (gates first, then
categories — the FK is RESTRICT).

Run (from backend/, venv active):  pytest tests/test_gate_categories.py
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import Gate, GateCategory
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from tests.conftest import cleanup_users, login, seed_user


def unique_category_name() -> str:
    return f"Cat {uuid.uuid4().hex[:8]}"


def unique_gate_value() -> str:
    return f".c{uuid.uuid4().hex[:6]}"


@pytest_asyncio.fixture(loop_scope="session")
async def categories_created() -> AsyncIterator[set[str]]:
    """Track category names created by a test; delete rows (and any gates
    still referencing them) on teardown."""
    names: set[str] = set()
    yield names
    if names:
        async with async_session_factory() as session:
            ids = list(
                (
                    await session.execute(
                        select(GateCategory.id).where(GateCategory.name.in_(names))
                    )
                )
                .scalars()
                .all()
            )
            if ids:
                await session.execute(
                    delete(Gate).where(Gate.category_id.in_(ids))
                )
                await session.execute(
                    delete(GateCategory).where(GateCategory.id.in_(ids))
                )
            await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def client_client() -> AsyncIterator[AsyncClient]:
    """A logged-in CLIENT-role http client (ctx only seeds owner + admin)."""
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    yield http
    await http.aclose()
    await cleanup_users({user.email})


async def _create_category(
    owner_client: AsyncClient, created: set[str], name: str | None = None
) -> dict[str, object]:
    name = name if name is not None else unique_category_name()
    created.add(name)
    res = await owner_client.post("/api/admin/gate-categories", json={"name": name})
    assert res.status_code == 201, res.text
    return res.json()


async def _create_gate(
    owner_client: AsyncClient, category_id: int, value: str | None = None
) -> dict[str, object]:
    value = value if value is not None else unique_gate_value()
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": value,
            "name": f"Gate {value}",
            "display_value": f"Visible {value}",
            "category_id": category_id,
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_creates_and_lists_categories(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    body = await _create_category(owner_client, categories_created)
    assert body["id"] > 0
    assert body["created_at"] is not None

    listed = await owner_client.get("/api/admin/gate-categories")
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert body["name"] in [c["name"] for c in items]
    assert listed.json()["total"] == len(items)


@pytest.mark.asyncio(loop_scope="session")
async def test_duplicate_category_name_is_409(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    body = await _create_category(owner_client, categories_created)
    dup = await owner_client.post(
        "/api/admin/gate-categories", json={"name": body["name"]}
    )
    assert dup.status_code == 409
    assert dup.json()["code"] == "category_exists"


@pytest.mark.asyncio(loop_scope="session")
async def test_rename_persists(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    body = await _create_category(owner_client, categories_created)
    new_name = unique_category_name()
    categories_created.add(new_name)
    res = await owner_client.patch(
        f"/api/admin/gate-categories/{body['id']}", json={"name": new_name}
    )
    assert res.status_code == 200, res.text
    assert res.json()["name"] == new_name

    listed = await owner_client.get("/api/admin/gate-categories")
    assert new_name in [c["name"] for c in listed.json()["items"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_special_mode_create_default_rename_and_toggle(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    """special_mode defaults off, persists a rename, and toggles explicitly."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    # Default off when the field is omitted.
    body = await _create_category(owner_client, categories_created)
    assert body["special_mode"] is False

    # Create with the flag on.
    name = unique_category_name()
    categories_created.add(name)
    res = await owner_client.post(
        "/api/admin/gate-categories", json={"name": name, "special_mode": True}
    )
    assert res.status_code == 201, res.text
    cat = res.json()
    assert cat["special_mode"] is True

    # A plain rename (no special_mode key) must NOT reset the flag.
    new_name = unique_category_name()
    categories_created.add(new_name)
    res = await owner_client.patch(
        f"/api/admin/gate-categories/{cat['id']}", json={"name": new_name}
    )
    assert res.status_code == 200, res.text
    assert res.json()["special_mode"] is True

    # Explicit toggle off, then back on.
    res = await owner_client.patch(
        f"/api/admin/gate-categories/{cat['id']}",
        json={"name": new_name, "special_mode": False},
    )
    assert res.json()["special_mode"] is False
    res = await owner_client.patch(
        f"/api/admin/gate-categories/{cat['id']}",
        json={"name": new_name, "special_mode": True},
    )
    assert res.json()["special_mode"] is True

    # The list view carries the flag too.
    listed = await owner_client.get("/api/admin/gate-categories")
    match = [c for c in listed.json()["items"] if c["id"] == cat["id"]]
    assert match and match[0]["special_mode"] is True


@pytest.mark.asyncio(loop_scope="session")
async def test_rename_to_existing_name_is_409(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    a = await _create_category(owner_client, categories_created)
    b = await _create_category(owner_client, categories_created)
    res = await owner_client.patch(
        f"/api/admin/gate-categories/{b['id']}", json={"name": a["name"]}
    )
    assert res.status_code == 409
    assert res.json()["code"] == "category_exists"


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_empty_category_is_204(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    body = await _create_category(owner_client, categories_created)
    res = await owner_client.delete(f"/api/admin/gate-categories/{body['id']}")
    assert res.status_code == 204

    listed = await owner_client.get("/api/admin/gate-categories")
    assert body["name"] not in [c["name"] for c in listed.json()["items"]]


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_with_active_gates_is_category_in_use(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    category = await _create_category(owner_client, categories_created)
    await _create_gate(owner_client, category["id"])  # type: ignore[arg-type]

    res = await owner_client.delete(f"/api/admin/gate-categories/{category['id']}")
    assert res.status_code == 409
    assert res.json()["code"] == "category_in_use"


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_after_retiring_gates_is_204(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    """Retired gates don't block deletion (active ones do — previous test)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    category = await _create_category(owner_client, categories_created)
    # A second category must exist so retired gate rows keep a home (the dev
    # DB always has the migration's seed row, but don't depend on it).
    await _create_category(owner_client, categories_created)
    gate = await _create_gate(owner_client, category["id"])  # type: ignore[arg-type]
    assert (
        await owner_client.delete(f"/api/admin/gates/{gate['id']}")
    ).status_code == 204

    res = await owner_client.delete(f"/api/admin/gate-categories/{category['id']}")
    assert res.status_code == 204


@pytest.mark.asyncio(loop_scope="session")
async def test_gate_create_requires_valid_category(
    ctx: dict[str, object], categories_created: set[str]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    # Missing category_id entirely → 422 (required field).
    res = await owner_client.post(
        "/api/admin/gates",
        json={"value": unique_gate_value(), "name": "X", "display_value": "X"},
    )
    assert res.status_code == 422

    # Unknown / out-of-int4 category ids → 404 category_not_found.
    for bad_id in (999999999, 0, 99999999999999999999):
        res = await owner_client.post(
            "/api/admin/gates",
            json={
                "value": unique_gate_value(),
                "name": "X",
                "display_value": "X",
                "category_id": bad_id,
            },
        )
        assert res.status_code in (404, 422), res.text
        if res.status_code == 404:
            assert res.json()["code"] == "category_not_found"


@pytest.mark.asyncio(loop_scope="session")
async def test_admin_and_client_are_forbidden_on_categories(
    ctx: dict[str, object], client_client: AsyncClient
) -> None:
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]

    for http_client in (admin_client, client_client):
        assert (
            await http_client.get("/api/admin/gate-categories")
        ).status_code == 403
        assert (
            await http_client.post(
                "/api/admin/gate-categories", json={"name": "Nope"}
            )
        ).status_code == 403
        assert (
            await http_client.patch(
                "/api/admin/gate-categories/1", json={"name": "Nope"}
            )
        ).status_code == 403
        assert (
            await http_client.delete("/api/admin/gate-categories/1")
        ).status_code == 403


@pytest.mark.asyncio(loop_scope="session")
async def test_client_gates_feed_carries_category_fields(
    ctx: dict[str, object],
    client_client: AsyncClient,
    categories_created: set[str],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    category = await _create_category(owner_client, categories_created)
    gate = await _create_gate(owner_client, category["id"])  # type: ignore[arg-type]

    res = await client_client.get("/api/gates")
    assert res.status_code == 200, res.text
    match = [g for g in res.json()["items"] if g["id"] == gate["id"]]
    assert len(match) == 1
    assert match[0]["category_id"] == category["id"]
    assert match[0]["category_name"] == category["name"]
    # display_value feature (2026-06-16): clients see the owner-authored
    # "Comando visible", NEVER the real value (which is omitted from this feed).
    assert "value" not in match[0]
    assert match[0]["display_value"] == gate["display_value"]
    assert match[0]["name"] == gate["name"]


@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_or_out_of_range_category_id_is_404(
    ctx: dict[str, object],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    for cid in ("999999999", "0", "99999999999999999999"):
        res = await owner_client.patch(
            f"/api/admin/gate-categories/{cid}", json={"name": "X"}
        )
        assert res.status_code == 404, res.text
        assert res.json()["code"] == "category_not_found"
        res = await owner_client.delete(f"/api/admin/gate-categories/{cid}")
        assert res.status_code == 404


@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "bad_name",
    ["", "   ", "x" * 81, "n\x00m", "n\u200bm"],
    ids=["empty", "whitespace-only", "too-long", "nul-byte", "zero-width-space"],
)
async def test_validation_rejects_bad_category_names(
    ctx: dict[str, object], bad_name: str
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    res = await owner_client.post(
        "/api/admin/gate-categories", json={"name": bad_name}
    )
    assert res.status_code == 422
