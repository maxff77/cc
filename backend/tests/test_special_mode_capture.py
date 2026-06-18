"""Special-mode capture pipeline (special-mode gate categories).

A gate category flagged ``special_mode`` makes its capture sessions parse replies
by the ``Approveds! ✅: N`` count (N≥1 ⇒ ok, else rejected) instead of bare
✅-glyph presence, and strips the Approveds!/Deads!/Credits stats from the stored
reply (keeping ``Time:``).

Same harness as test_plan_credits: real ASGI app + dev Postgres, FakeGateway
populates send_log, capture goes DIRECT to ``capture.process_incoming``.

Run (from backend/, venv active):  pytest tests/test_special_mode_capture.py
"""

import pytest
from app.core import capture
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import Gate, GateCategory, Tenant, User
from app.db.repos import responses as responses_repo
from httpx import AsyncClient
from sqlalchemy import select

from tests.conftest import FakeGateway

# The user's canonical special-mode stats line (the false-positive example).
_STATS = "↳ Approveds! ✅: {a} ヾ⌿ Deads! ❌: {d} ヾ⌿ Credits: 999996044 ヾ⌿ Time: 32.95s"


async def _set_category_special(category_id: int, value: bool) -> None:
    async with async_session_factory() as session:
        cat = await session.get(GateCategory, category_id)
        assert cat is not None
        cat.special_mode = value
        await session.commit()


async def _set_gate_cost(gate_id: int, cost: int) -> None:
    async with async_session_factory() as session:
        g = await session.get(Gate, gate_id)
        assert g is not None
        g.credit_cost = cost
        await session.commit()


async def _set_balance(tenant_id: int, value: int) -> None:
    async with async_session_factory() as session:
        t = await session.get(Tenant, tenant_id)
        assert t is not None
        t.credit_balance = value
        await session.commit()


async def _balance(tenant_id: int) -> int:
    async with async_session_factory() as session:
        return (
            await session.execute(
                select(Tenant.credit_balance).where(Tenant.id == tenant_id)
            )
        ).scalar_one()


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _drain() -> None:
    from app.core import send_worker

    while await send_worker.step():
        pass


async def _latest(message_id: int):
    async with async_session_factory() as session:
        return await responses_repo.last_full_revision(
            session, chat_id=0, message_id=message_id
        )


@pytest.mark.asyncio(loop_scope="session")
async def test_special_mode_rejects_approveds_zero_no_cc_no_charge(
    client_user: tuple[AsyncClient, User], gate: dict, fake_gateway: FakeGateway
) -> None:
    """A ✅ glyph inside "Approveds! ✅: 0" is NOT an approval: status rejected,
    no CC extracted, no credit charged, stats (incl. Credits) stripped, Time kept."""
    http, user = client_user
    await _set_category_special(gate["category_id"], True)
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(user.tenant_id, 50)
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    await capture.process_incoming(
        IncomingReply(
            message_id=8101,
            reply_to_msg_id=1,
            text=_STATS.format(a=0, d=1),
            edited=False,
        )
    )

    latest = await _latest(8101)
    assert latest is not None and latest.status == "rejected"
    for token in ("Approveds", "Deads", "Credits", "999996044"):
        assert token not in latest.text
    assert "Time: 32.95s" in latest.text
    async with async_session_factory() as session:
        assert (
            await responses_repo.cc_count(session, latest.capture_session_id) == 0
        )
    assert await _balance(user.tenant_id) == 50  # rejected ⇒ never charged


@pytest.mark.asyncio(loop_scope="session")
async def test_special_mode_zero_then_one_flips_to_ok_and_charges_once(
    client_user: tuple[AsyncClient, User], gate: dict, fake_gateway: FakeGateway
) -> None:
    """Review-4 CRITICAL regression: a 0→1 Approveds edit whose stripped text is
    byte-identical (same Time:) must STILL flip rejected→ok and charge once — the
    no-op dedup keys on (text, status), not text alone, so the strip can't erase
    the validity signal."""
    http, user = client_user
    await _set_category_special(gate["category_id"], True)
    await _set_gate_cost(gate["id"], 10)
    await _set_balance(user.tenant_id, 50)
    await _post_batch(http, "uno", gate["id"])
    await _drain()

    # Rev 0 — Approveds 0 ⇒ rejected, no charge.
    await capture.process_incoming(
        IncomingReply(
            message_id=8201,
            reply_to_msg_id=1,
            text=_STATS.format(a=0, d=1),
            edited=False,
        )
    )
    rev0 = await _latest(8201)
    assert rev0 is not None and rev0.status == "rejected"
    assert await _balance(user.tenant_id) == 50

    # Rev 1 — Approveds 1 (same Time:) ⇒ stored text identical after the strip,
    # but the rejected→ok flip must NOT be swallowed as a no-op edit.
    await capture.process_incoming(
        IncomingReply(
            message_id=8201,
            reply_to_msg_id=1,
            text=_STATS.format(a=1, d=0),
            edited=True,
        )
    )
    rev1 = await _latest(8201)
    assert rev1 is not None and rev1.status == "ok"
    assert await _balance(user.tenant_id) == 40  # charged exactly once
