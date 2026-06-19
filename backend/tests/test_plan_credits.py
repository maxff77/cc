"""Credits feature tests: per-tenant balance, per-gate credit cost, the
capture charge (once per ✅, clamped, idempotent), the create/append guard, the
plan grant on assign/renew, owner recharge and field-bound validation.

Same idiom as test_attribution.py: real ASGI app against the dev Postgres,
self-seeding/self-cleaning, FakeGateway populates send_log, capture goes DIRECT
to ``capture.process_incoming`` (no telethon), events recorded by
monkeypatching the broadcaster.

Run (from backend/, venv active):  pytest tests/test_plan_credits.py
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Plan, Tenant, User
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.conftest import (
    FakeGateway,
    cleanup_users,
    login,
    seed_user,
    unique_email,
)

PASSWORD = "seed-pass-123"  # noqa: S105 — throwaway test credential

# Valid plan fields for the catalog POST (mirrors test_plans_catalog.py shape).
_PLAN_BASE = {
    "price_usd": "9.99",
    "duration_days": 30,
    "antispam_seconds": "4",
    "max_lines_per_batch": 100,
}


# --- Local helpers -----------------------------------------------------------


@pytest.fixture
def events(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Record every broadcaster emission as ``(tenant_id|None, event, data)``."""
    recorded: list[tuple] = []

    async def emit(tenant_id: int, event: str, data: dict) -> None:
        recorded.append((tenant_id, event, data))

    async def emit_global(event: str, data: dict) -> None:
        recorded.append((None, event, data))

    monkeypatch.setattr(broadcaster, "emit", emit)
    monkeypatch.setattr(broadcaster, "emit_global", emit_global)
    return recorded


def _credits_updates(events: list[tuple]) -> list[tuple]:
    return [e for e in events if e[1] == "credits.updated"]


async def _set_balance(tenant_id: int, value: int) -> None:
    async with async_session_factory() as session:
        tenant = await session.get(Tenant, tenant_id)
        assert tenant is not None
        tenant.credit_balance = value
        await session.commit()


async def _balance(tenant_id: int) -> int:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(Tenant.credit_balance).where(Tenant.id == tenant_id)
            )
        ).scalar_one()


async def _set_gate_cost(gate_id: int, cost: int) -> None:
    async with async_session_factory() as session:
        from app.db.models import Gate

        gate = await session.get(Gate, gate_id)
        assert gate is not None
        gate.credit_cost = cost
        await session.commit()


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _drain() -> None:
    from app.core import send_worker

    while await send_worker.step():
        pass


async def _delete_plan(plan_id: int) -> None:
    async with async_session_factory() as session:
        plan = await session.get(Plan, plan_id)
        if plan is not None:
            await session.delete(plan)
            await session.commit()


# --- Capture charge (the core of the feature) --------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_first_ok_charges_once_per_message(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """A costed gate debits its cost the first time a message reaches ✅, and
    NOT again on a later ✅ edit of the same message (once per message)."""
    http, user = client_user
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(user.tenant_id, 50)

    await _post_batch(http, "uno", gate["id"])  # snapshots cost 10 onto the batch
    await _drain()  # send_log.message_id == 1

    await capture.process_incoming(
        IncomingReply(
            message_id=7101, reply_to_msg_id=1, text="✅ CC: 4111 Status a",
            edited=False,
        )
    )
    assert await _balance(user.tenant_id) == 40
    updates = _credits_updates(events)
    assert len(updates) == 1
    assert updates[0] == (user.tenant_id, "credits.updated", {"balance": 40})

    # A later ✅ edit with NEW cc is still the same message → no second charge.
    await capture.process_incoming(
        IncomingReply(
            message_id=7101,
            reply_to_msg_id=1,
            text="✅ CC: 4111 Status a\nCC: 5500 Status b",
            edited=True,
        )
    )
    assert await _balance(user.tenant_id) == 40
    assert len(_credits_updates(events)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_rejected_then_ok_charges_exactly_once(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """❌ never charges; the first ✅ (even via a ❌→✅ transition) charges once,
    and a ✅→❌→✅ re-bounce does NOT re-charge (a transition-based check would
    double-charge — the guard is 'no prior ✅ row')."""
    http, user = client_user
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(user.tenant_id, 100)
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    # ❌ first — no charge.
    await capture.process_incoming(
        IncomingReply(
            message_id=7201, reply_to_msg_id=1, text="❌ Rechazada", edited=False
        )
    )
    assert await _balance(user.tenant_id) == 100

    # ❌ → ✅ : first ✅ → charge once.
    await capture.process_incoming(
        IncomingReply(
            message_id=7201, reply_to_msg_id=1, text="✅ Aprobada", edited=True
        )
    )
    assert await _balance(user.tenant_id) == 90

    # ✅ → ❌ → ✅ : the second ✅ must NOT charge again.
    await capture.process_incoming(
        IncomingReply(
            message_id=7201, reply_to_msg_id=1, text="❌ Otra vez no", edited=True
        )
    )
    await capture.process_incoming(
        IncomingReply(
            message_id=7201, reply_to_msg_id=1, text="✅ Aprobada de nuevo",
            edited=True,
        )
    )
    assert await _balance(user.tenant_id) == 90
    assert len(_credits_updates(events)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_free_gate_never_charges(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """A gate with credit_cost 0 (the default) never debits and never emits a
    credits.updated, regardless of balance."""
    http, user = client_user
    await _set_balance(user.tenant_id, 30)  # gate cost stays 0
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    await capture.process_incoming(
        IncomingReply(
            message_id=7301, reply_to_msg_id=1, text="✅ CC: 4111 Status a",
            edited=False,
        )
    )
    assert await _balance(user.tenant_id) == 30
    assert _credits_updates(events) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_overrun_clamps_to_zero_and_persists(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Balance 5 on a cost-10 gate: a ✅ clamps the balance to 0 (never
    negative) and the response is still persisted."""
    from app.db.repos import responses as responses_repo

    http, user = client_user
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(user.tenant_id, 5)
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    await capture.process_incoming(
        IncomingReply(
            message_id=7401, reply_to_msg_id=1, text="✅ CC: 1 Status", edited=False
        )
    )
    assert await _balance(user.tenant_id) == 0
    assert _credits_updates(events) == [
        (user.tenant_id, "credits.updated", {"balance": 0})
    ]
    # Capture parity: the revision is persisted despite the clamp.
    async with async_session_factory() as session:
        latest = await responses_repo.last_full_revision(
            session, chat_id=0, message_id=7401
        )
        assert latest is not None and latest.status == "ok"


# --- Create / append guard ---------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_create_on_costed_gate_blocked_at_zero(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A costed gate with balance 0 rejects batch creation (insufficient_credits)
    and queues nothing; a free gate at balance 0 still sends."""
    http, user = client_user
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(user.tenant_id, 0)

    res = await http.post(
        "/api/batches", json={"text": "uno", "gate_id": gate["id"]}
    )
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "insufficient_credits"

    # Nothing was queued.
    async with async_session_factory() as session:
        from app.db.repos import batches as batches_repo

        assert await batches_repo.get_live_batch(session, user.tenant_id) is None

    # A free gate (cost 0) still works at balance 0.
    await _set_gate_cost(gate["id"], 0)
    ok = await http.post(
        "/api/batches", json={"text": "uno", "gate_id": gate["id"]}
    )
    assert ok.status_code == 201, ok.text


@pytest.mark.asyncio(loop_scope="session")
async def test_staff_bypass_costed_gate_at_zero_balance(
    ctx: dict[str, object], gate: dict
) -> None:
    """Owner/admin "house" tenants are never metered: they can send on a costed
    gate even at balance 0 (they receive no plan grant and must be able to test
    a costed gate). Clients stay blocked (covered above)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    await _set_gate_cost(gate["id"], 10)  # owner tenant balance is 0

    res = await owner_client.post(
        "/api/batches", json={"text": "uno", "gate_id": gate["id"]}
    )
    assert res.status_code == 201, res.text


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_batch_never_charged_on_ok(
    ctx: dict[str, object],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """Owner/admin "house" tenants are fully exempt from credits: a captured ✅
    on their own costed batch (priority > 0) never debits the balance nor emits
    credits.updated — only client batches (priority == 0) are metered."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    owner: User = ctx["owner"]  # type: ignore[assignment]
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(owner.tenant_id, 50)

    await _post_batch(owner_client, "uno", gate["id"])  # priority 2, cost 10
    await _drain()  # send_log.message_id == 1

    await capture.process_incoming(
        IncomingReply(
            message_id=7901, reply_to_msg_id=1, text="✅ CC: 4111 Status a",
            edited=False,
        )
    )

    assert await _balance(owner.tenant_id) == 50  # untouched — fully exempt
    assert _credits_updates(events) == []


@pytest.mark.asyncio(loop_scope="session")
async def test_append_blocked_when_balance_drained(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A live costed batch whose tenant hit 0 rejects further appended lines."""
    http, user = client_user
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(user.tenant_id, 5)
    await _post_batch(http, "uno", gate["id"])  # starts fine (balance > 0)

    await _set_balance(user.tenant_id, 0)  # simulate the balance draining to 0
    res = await http.post(
        "/api/batches", json={"text": "dos", "gate_id": gate["id"]}
    )
    assert res.status_code == 403, res.text
    assert res.json()["code"] == "insufficient_credits"


# --- Plan grant + owner recharge (admin API) ---------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_plan_assign_and_renew_grant_credits(
    ctx: dict[str, object],
) -> None:
    """Assigning a plan with credits=100 sets the tenant balance to 100; a plan
    renewal tops it up by another 100."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    plan_res = await owner_client.post(
        "/api/admin/plans",
        json={"name": f"Credit {uuid.uuid4().hex[:6]}", "credits": 100, **_PLAN_BASE},
    )
    assert plan_res.status_code == 201, plan_res.text
    plan = plan_res.json()
    assert plan["credits"] == 100
    plan_id = plan["id"]

    email = unique_email("client")
    created.add(email)
    create = await owner_client.post(
        "/api/admin/users",
        json={"email": email, "password": PASSWORD, "role": "client",
              "plan_id": plan_id},
    )
    assert create.status_code == 201, create.text
    assert create.json()["credit_balance"] == 100
    user_id = create.json()["id"]

    renew = await owner_client.post(
        f"/api/admin/users/{user_id}/renew", json={"plan_id": plan_id}
    )
    assert renew.status_code == 200, renew.text
    assert renew.json()["credit_balance"] == 200

    await cleanup_users({email})
    await _delete_plan(plan_id)


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_recharge_sets_balance_and_emits(
    ctx: dict[str, object], events: list[tuple]
) -> None:
    """Owner recharge sets the absolute balance and pushes credits.updated; a
    negative value is rejected (invalid_credits)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    email = unique_email("client")
    created.add(email)
    create = await owner_client.post(
        "/api/admin/users",
        json={"email": email, "password": PASSWORD, "role": "client",
              "plan_days": 30},
    )
    assert create.status_code == 201, create.text
    user_id = create.json()["id"]
    tenant_id = create.json()["tenant_id"]
    assert create.json()["credit_balance"] == 0  # no plan, no grant

    res = await owner_client.post(
        f"/api/admin/users/{user_id}/credits", json={"credit_balance": 200}
    )
    assert res.status_code == 200, res.text
    assert res.json()["credit_balance"] == 200
    assert (tenant_id, "credits.updated", {"balance": 200}) in events

    bad = await owner_client.post(
        f"/api/admin/users/{user_id}/credits", json={"credit_balance": -1}
    )
    assert bad.status_code == 400, bad.text
    assert bad.json()["code"] == "invalid_credits"

    await cleanup_users({email})


@pytest.mark.asyncio(loop_scope="session")
async def test_recharge_is_owner_only(ctx: dict[str, object]) -> None:
    """A non-owner admin cannot recharge credits (owner-only)."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    created: set[str] = ctx["created"]  # type: ignore[assignment]

    email = unique_email("client")
    created.add(email)
    create = await owner_client.post(
        "/api/admin/users",
        json={"email": email, "password": PASSWORD, "role": "client",
              "plan_days": 30},
    )
    user_id = create.json()["id"]

    res = await admin_client.post(
        f"/api/admin/users/{user_id}/credits", json={"credit_balance": 50}
    )
    assert res.status_code == 403, res.text

    await cleanup_users({email})


# --- Field-bound validation --------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_gate_credit_cost_rejected(
    ctx: dict[str, object], gate: dict
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": f".x{uuid.uuid4().hex[:6]}",
            "name": "Neg",
            "display_value": "Neg",
            "category_id": gate["category_id"],
            "credit_cost": -1,
        },
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_gate"


@pytest.mark.asyncio(loop_scope="session")
async def test_invalid_plan_credits_rejected(ctx: dict[str, object]) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    res = await owner_client.post(
        "/api/admin/plans",
        json={"name": f"Bad {uuid.uuid4().hex[:6]}", "credits": -5, **_PLAN_BASE},
    )
    assert res.status_code == 400, res.text
    assert res.json()["code"] == "invalid_plan"


@pytest.mark.asyncio(loop_scope="session")
async def test_public_gate_and_me_expose_credits(
    client_user: tuple[AsyncClient, User], gate: dict
) -> None:
    """The public gate list carries credit_cost (client-safe) and /me carries
    the tenant's credit_balance."""
    http, user = client_user
    await _set_gate_cost(gate["id"], 7)
    await _set_balance(user.tenant_id, 42)

    gates = await http.get("/api/gates")
    assert gates.status_code == 200, gates.text
    row = next(g for g in gates.json()["items"] if g["id"] == gate["id"])
    assert row["credit_cost"] == 7
    assert "value" not in row  # the real command stays owner-only

    me = await http.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["credit_balance"] == 42
