"""Tests for the gate ``display_value`` ("Comando visible") feature.

Covers: the ``_validate_gate_display_value`` policy (unit), the create/update
round-trip (the field persists on the owner shape), and the privacy invariant —
the public ``/api/gates`` feed carries ``display_value`` and NEVER the real
``value``.

Run (from backend/, venv active):  pytest tests/test_gate_display_value.py
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.api.admin import _validate_gate_display_value
from app.db.base import async_session_factory
from app.db.models import Gate, GateCategory
from httpx import AsyncClient
from sqlalchemy import delete


def _unique_value() -> str:
    return f".d{uuid.uuid4().hex[:6]}"


@pytest_asyncio.fixture(loop_scope="session")
async def category() -> AsyncIterator[dict[str, object]]:
    """A category for gate creation; teardown removes its gates first (FK)."""
    async with async_session_factory() as session:
        row = GateCategory(name=f"Cat {uuid.uuid4().hex[:8]}")
        session.add(row)
        await session.commit()
        cat = {"id": row.id, "name": row.name}
    yield cat
    async with async_session_factory() as session:
        await session.execute(delete(Gate).where(Gate.category_id == cat["id"]))
        await session.execute(
            delete(GateCategory).where(GateCategory.id == cat["id"])
        )
        await session.commit()


# --- Validator (pure unit) ------------------------------------------------------


def test_display_value_trims_and_allows_spaces() -> None:
    # Spaces ARE allowed (owner-authored label); trimmed at the edges.
    assert _validate_gate_display_value("  Comando 01  ") == "Comando 01"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "x" * 81, "n\x00m", "n​m", "a\tb"],
    ids=["empty", "whitespace-only", "too-long", "nul-byte", "zero-width", "tab"],
)
def test_display_value_rejects_bad(bad: str) -> None:
    with pytest.raises(ValueError):
        _validate_gate_display_value(bad)


# --- Round-trip + privacy -------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_persists_display_value_and_public_omits_value(
    ctx: dict[str, object], category: dict[str, object]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    value = _unique_value()
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": value,
            "name": "Visa",
            "display_value": "Comando 01",
            "category_id": category["id"],
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    # Owner shape carries BOTH the real value and the visible command.
    assert body["value"] == value
    assert body["display_value"] == "Comando 01"

    # Public feed: display_value present, the real value NEVER exposed.
    pub = await owner_client.get("/api/gates")
    assert pub.status_code == 200, pub.text
    match = [g for g in pub.json()["items"] if g["id"] == body["id"]]
    assert len(match) == 1
    assert match[0]["display_value"] == "Comando 01"
    assert "value" not in match[0]


@pytest.mark.asyncio(loop_scope="session")
async def test_update_changes_display_value(
    ctx: dict[str, object], category: dict[str, object]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    created = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": _unique_value(),
            "name": "Visa",
            "display_value": "Antes",
            "category_id": category["id"],
        },
    )
    assert created.status_code == 201, created.text
    gate_id = created.json()["id"]

    res = await owner_client.patch(
        f"/api/admin/gates/{gate_id}",
        json={
            "value": created.json()["value"],
            "name": "Visa",
            "display_value": "Despues",
            "category_id": category["id"],
        },
    )
    assert res.status_code == 200, res.text
    assert res.json()["display_value"] == "Despues"


@pytest.mark.asyncio(loop_scope="session")
async def test_create_rejects_missing_display_value(
    ctx: dict[str, object], category: dict[str, object]
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]

    # display_value is REQUIRED — omitting it is a 422.
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": _unique_value(),
            "name": "Visa",
            "category_id": category["id"],
        },
    )
    assert res.status_code == 422
