"""Gift-key purge (keys-view-declutter feature): ``delete_stale`` I/O matrix.

Inserts keys with controlled ``status`` / ``days`` / ``created_at`` directly,
runs the set-based purge, and asserts exactly which rows survive:

- expired UNCLAIMED (active, days elapsed) -> deleted
- revoked (any age)                        -> deleted
- fresh unclaimed (active, days not yet up) -> kept
- credits-only (active, days==0)            -> kept (days rule exempt)
- claimed                                   -> kept (audit trail)

Uses ``plan_factory`` for the RESTRICT ``plan_id`` FK; it cleans the keys it
referenced on teardown.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from app.db.base import async_session_factory
from app.db.models import GiftKey, Plan
from app.db.repos import gift_keys as gift_keys_repo
from sqlalchemy import delete, select

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def plan_id() -> AsyncIterator[int]:
    """A throwaway plan for the RESTRICT ``plan_id`` FK; cleans its keys + itself
    on teardown."""
    async with async_session_factory() as s:
        plan = Plan(
            name=f"purge-plan-{uuid.uuid4().hex[:8]}",
            price_usd=Decimal("5.00"),
            duration_days=30,
            max_lines_per_batch=100,
            credits=50,
            is_active=True,
        )
        s.add(plan)
        await s.commit()
        pid = plan.id
    yield pid
    async with async_session_factory() as s:
        await s.execute(delete(GiftKey).where(GiftKey.plan_id == pid))
        await s.execute(delete(Plan).where(Plan.id == pid))
        await s.commit()


async def _insert(plan_id: int, *, status: str, days: int, age_days: float) -> int:
    """Insert one key aged ``age_days`` in the past; return its id."""
    async with async_session_factory() as s:
        key = GiftKey(
            code=f"RangerX-{uuid.uuid4().hex[:12].upper()}",
            days=days,
            credits=50 if days == 0 else 0,
            plan_id=plan_id,
            status=status,
            created_at=datetime.now(UTC) - timedelta(days=age_days),
        )
        s.add(key)
        await s.commit()
        return key.id


async def _exists(key_id: int) -> bool:
    async with async_session_factory() as s:
        row = await s.execute(select(GiftKey.id).where(GiftKey.id == key_id))
        return row.scalar_one_or_none() is not None


async def test_delete_stale_matrix(plan_id: int) -> None:
    expired = await _insert(plan_id, status="active", days=7, age_days=8)
    revoked = await _insert(plan_id, status="revoked", days=7, age_days=1)
    fresh = await _insert(plan_id, status="active", days=7, age_days=2)
    credits_only = await _insert(plan_id, status="active", days=0, age_days=100)
    claimed = await _insert(plan_id, status="claimed", days=7, age_days=100)

    async with async_session_factory() as s:
        deleted = await gift_keys_repo.delete_stale(s)
        await s.commit()

    # At least our two stale rows (other tests' leftover keys may add to it).
    assert deleted >= 2
    assert not await _exists(expired), "expired unclaimed key should be purged"
    assert not await _exists(revoked), "revoked key should be purged"
    assert await _exists(fresh), "fresh unclaimed key must survive"
    assert await _exists(credits_only), "credits-only (days==0) key must survive"
    assert await _exists(claimed), "claimed key must never be purged"
