"""Integration tests for the public (no-auth) landing endpoints.

`/api/public/gates` + `/api/public/plans` feed the logged-out sales landing.
Both are reachable WITHOUT a session and expose only marketing-safe fields:
gates -> category + gate names ONLY (never the real ``value``/``display_value``);
plans -> active-only, never ``antispam_seconds``, with the ∞ ``credits_unlimited``
display convention.

Drives the real ASGI app (httpx ``ASGITransport``) against the dev Postgres —
self-seeding directly in the DB, self-cleaning on teardown (same shape as
``test_admin_gates``). The catalog is GLOBAL, so the empty-catalog scenarios are
asserted by SHAPE (filtering to the rows this test seeds) rather than by an
absolute count — other rows may exist in the shared dev DB.

Run (from backend/, venv active):  pytest tests/test_public.py
"""

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import pytest
import pytest_asyncio
from app.api.public import UNLIMITED_CREDITS_THRESHOLD
from app.db.base import async_session_factory
from app.db.models import Gate, GateCategory, Plan
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete


@pytest_asyncio.fixture(loop_scope="session")
async def anon_client() -> AsyncIterator[AsyncClient]:
    """An http client with NO auth cookie — a logged-out visitor."""
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    yield http
    await http.aclose()


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_gate() -> AsyncIterator[dict[str, str]]:
    """One active gate in its own category, seeded directly in the DB.

    Teardown removes the gate (RESTRICT FK) before the category.
    """
    cat_name = f"Cat {uuid.uuid4().hex[:8]}"
    gate_name = f"Gate {uuid.uuid4().hex[:8]}"
    real_value = f".v{uuid.uuid4().hex[:6]}"
    display = f"Visible {uuid.uuid4().hex[:6]}"
    async with async_session_factory() as session:
        cat = GateCategory(name=cat_name)
        session.add(cat)
        await session.flush()
        gate = Gate(
            value=real_value,
            name=gate_name,
            display_value=display,
            category_id=cat.id,
        )
        session.add(gate)
        await session.commit()
        cat_id = cat.id
    yield {
        "category_name": cat_name,
        "gate_name": gate_name,
        "value": real_value,
        "display_value": display,
    }
    async with async_session_factory() as session:
        await session.execute(delete(Gate).where(Gate.category_id == cat_id))
        await session.execute(delete(GateCategory).where(GateCategory.id == cat_id))
        await session.commit()


async def _seed_plan(
    *, credits: int, is_active: bool, is_default: bool = False
) -> int:
    """Insert a plan directly and return its id (collision-free name)."""
    async with async_session_factory() as session:
        plan = Plan(
            name=f"Plan {uuid.uuid4().hex[:10]}",
            price_usd=Decimal("21.00"),
            duration_days=30,
            max_lines_per_batch=500,
            credits=credits,
            is_active=is_active,
            is_default=is_default,
        )
        session.add(plan)
        await session.commit()
        return plan.id


@pytest_asyncio.fixture(loop_scope="session")
async def seeded_plans() -> AsyncIterator[list[int]]:
    """Track plan ids seeded by a test; delete their rows on teardown."""
    ids: list[int] = []
    yield ids
    if ids:
        async with async_session_factory() as session:
            await session.execute(delete(Plan).where(Plan.id.in_(ids)))
            await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_gates_endpoint_is_public_and_names_only(
    anon_client: AsyncClient, seeded_gate: dict[str, str]
) -> None:
    # Reachable WITHOUT any auth cookie.
    res = await anon_client.get("/api/public/gates")
    assert res.status_code == 200, res.text
    body = res.json()

    # The seeded category appears with its gate name nested under it.
    cat = next(
        c for c in body["categories"] if c["name"] == seeded_gate["category_name"]
    )
    assert seeded_gate["gate_name"] in cat["gates"]
    assert body["total"] >= 1

    # HARD BOUNDARY: each category carries ONLY ``name`` + ``gates`` (a list of
    # plain name strings) — there is no per-gate object that could ever serialize
    # ``value``/``display_value``/``credit_cost``. Asserting the structural shape
    # is the real protection; the seeded real command + display string (random,
    # collision-free) must also be absent from the whole payload.
    for c in body["categories"]:
        assert set(c) == {"name", "gates"}
        assert all(isinstance(g, str) for g in c["gates"])
    raw = res.text
    assert seeded_gate["value"] not in raw
    assert seeded_gate["display_value"] not in raw


@pytest.mark.asyncio(loop_scope="session")
async def test_plans_endpoint_is_public_active_only_and_unlimited_flag(
    anon_client: AsyncClient, seeded_plans: list[int]
) -> None:
    active_unlimited = await _seed_plan(
        credits=UNLIMITED_CREDITS_THRESHOLD, is_active=True, is_default=True
    )
    active_finite = await _seed_plan(credits=500, is_active=True)
    inactive = await _seed_plan(credits=500, is_active=False)
    seeded_plans.extend([active_unlimited, active_finite, inactive])

    # Reachable WITHOUT any auth cookie.
    res = await anon_client.get("/api/public/plans")
    assert res.status_code == 200, res.text
    body = res.json()

    # Active-only: never expose ``antispam_seconds`` (internal pacing).
    raw = res.text
    assert "antispam_seconds" not in raw

    items_by_name = {item["name"]: item for item in body["items"]}

    async with async_session_factory() as session:
        unlimited_plan = await session.get(Plan, active_unlimited)
        finite_plan = await session.get(Plan, active_finite)
        inactive_plan = await session.get(Plan, inactive)

    # The inactive plan never appears; both active ones do.
    assert inactive_plan.name not in items_by_name
    assert unlimited_plan.name in items_by_name
    assert finite_plan.name in items_by_name

    # ∞ display convention: credits >= threshold -> True, else False.
    assert items_by_name[unlimited_plan.name]["credits_unlimited"] is True
    assert items_by_name[finite_plan.name]["credits_unlimited"] is False
    assert items_by_name[unlimited_plan.name]["is_default"] is True

    # Marketing-safe shape: exactly these keys, nothing else.
    assert set(items_by_name[finite_plan.name]) == {
        "name",
        "price_usd",
        "duration_days",
        "max_lines_per_batch",
        "credits",
        "credits_unlimited",
        "is_default",
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_empty_catalog_shapes() -> None:
    """With no seeded rows, both endpoints still return their well-formed shape.

    The dev DB is shared, so an empty catalog can't be asserted by an absolute
    count globally; instead use a throwaway client and verify the contract keys
    + that ``total`` agrees with the list length (it would be exactly 0 against a
    pristine DB — the documented empty-catalog response).
    """
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        gates = await http.get("/api/public/gates")
        assert gates.status_code == 200
        gbody = gates.json()
        assert set(gbody) == {"categories", "total"}
        assert gbody["total"] == len(
            [name for c in gbody["categories"] for name in c["gates"]]
        )

        plans = await http.get("/api/public/plans")
        assert plans.status_code == 200
        pbody = plans.json()
        assert set(pbody) == {"items", "total"}
        assert pbody["total"] == len(pbody["items"])
