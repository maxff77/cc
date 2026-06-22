"""Amazon cookie-mode — serialized send + cookie rotation (Phase 2).

A cookie-mode batch sends the atomic ``.cookie <active_value>`` then
``.amz <line>`` pair in ONE worker turn (no ``scheduler.pick_next`` between
them) and then HOLDS the tenant until the bot's ``⌿ Status:`` verdict for that
``.amz`` line arrives. The capture consumer classifies that reply
(Approved/Declined/cookie-dead/format-error), persists the durable row, and
hands the verdict back to the worker through the in-process
``core.cookie_verdict`` signal; the worker consumes/rotates/pauses.

These tests lock the engine invariants from the frozen spec:
  (a) atomic pair, no interleaved ``pick_next``, only ``.amz`` owns a send_log;
  (b) the serialize gate (``active_senders`` SQL skips an awaiting tenant while
      ``awaiting_verdict_until`` is future, another tenant still flows);
  (c) the classification matrix (Approved→Filtrada+ok+CC, Declined→rejected
      full row + nothing in Filtrada + cookie alive);
  (d) rotation (cookie→dead, next cookie picked never the first again, same
      line re-queued, NEW message_id, dead attempt stays attributed, Completa
      once);
  (e) exhaustion → ``cookies_exhausted`` pause → add cookie → resume → the
      failed line is the very next send;
  (f) format-error → line ``failed`` (``amazon_format_error``), cookie unchanged;
  (g) the cookie-confirmation reply → no response row, no unmatched bump, no
      ``alerts.note_unmatched``;
  (h) reconciler/edit replay: same dead reply twice → one re-queue, no spurious
      exhaustion;
  (i) timeout fires then the original verdict lands → the late verdict is
      dropped (attempt-fenced);
  (j) FloodWait on ``.amz`` after ``.cookie`` → no bare ``.amz`` retry, the line
      re-queues;
  (k) the cookie value appears in NO emitted event/log.

Same harness as test_send_hardening / test_special_mode_capture: the real ASGI
app against the dev Postgres, ``FakeGateway`` (no real Telegram), capture goes
DIRECT to ``capture.process_incoming``, events recorded by monkeypatching the
broadcaster.

Run (from backend/, venv active):  pytest tests/test_amazon_rotation.py
"""

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.core import alerts, capture, cookie_verdict, send_worker
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.db.base import async_session_factory
from app.db.models import (
    Batch,
    BatchLine,
    Gate,
    GateCategory,
    GateCookie,
    Response,
    SendLog,
    User,
)
from app.db.repos import batches as batches_repo
from app.db.repos import gate_cookies as gate_cookies_repo
from app.db.repos import responses as responses_repo
from app.main import app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, update
from telethon.errors import FloodWaitError

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

# An Approved reply — the owner-locked real sample (the CC card and the
# ``⌿ Status:`` verdict GLUED on ONE line with the U+233F separator + leading
# U+2607 bolt). The Filtrada datum is the bare pipe value, no Status noise.
_APPROVED_CARD = "377481016137504|05|2033|3845"
_APPROVED = (
    "☇ CC: 377481016137504|05|2033|3845⌿ Status: Approved ✅"
    "⌿ Response: Tarjeta vinculada correctamente. | Removed: ✅ Removido"
)
_DECLINED = "☇ CC: 377481016137504|05|2033|3845⌿ Status: Declined ❌"
# Cookie-dead: any Status token that is not Approved/Declined (catch-all).
_COOKIE_DEAD = "☇ Status: ❌ Cookies Inválidas"
# The bot's ``Format :`` help message (a malformed ``.amz`` line).
_FORMAT_ERROR = "[ヾ] Formato inválido ⌿ Format : .amz tarjeta|mm|yy|cvv"
# The side-band ``.cookie`` confirmation (reply to the ``.cookie`` send).
_COOKIE_CONFIRMATION = "[ヾ] almacenó tu cookie correctamente. ✅"


# --- Fixtures + helpers ------------------------------------------------------


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


async def _make_cookie_gate(owner_client: AsyncClient) -> dict:
    """A fresh active gate in a cookie-MODE category (via the owner API)."""
    cat = await owner_client.post(
        "/api/admin/gate-categories",
        json={"name": f"Amz {uuid.uuid4().hex[:8]}", "cookie_mode": True},
    )
    assert cat.status_code == 201, cat.text
    value = f".amz{uuid.uuid4().hex[:5]}"
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": value,
            "name": "Amazon Gate",
            "display_value": "Comando Amazon",
            "category_id": cat.json()["id"],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _drop_gate(category_id: int) -> None:
    async with async_session_factory() as session:
        gate_ids = list(
            (
                await session.execute(
                    select(Gate.id).where(Gate.category_id == category_id)
                )
            )
            .scalars()
            .all()
        )
        if gate_ids:
            # ``BatchLine.failed_cookie_id`` FKs ``gate_cookies.id`` with
            # ``ON DELETE SET NULL``, so deleting the cookies auto-nulls the
            # back-references on the surviving ``client_user`` batch_lines — no
            # manual pre-NULL needed (matches ``test_cookies._drop_gate``).
            await session.execute(
                delete(GateCookie).where(GateCookie.gate_id.in_(gate_ids))
            )
        await session.execute(delete(Gate).where(Gate.category_id == category_id))
        await session.execute(
            delete(GateCategory).where(GateCategory.id == category_id)
        )
        await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def owner_client() -> AsyncIterator[AsyncClient]:
    owner = await seed_user("owner", email_prefix="amz")
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, owner.email)
    yield http
    await http.aclose()
    await cleanup_users({owner.email})


@pytest_asyncio.fixture(loop_scope="session")
async def cookie_gate(owner_client: AsyncClient) -> AsyncIterator[dict]:
    """An active, cookie-mode gate."""
    gate = await _make_cookie_gate(owner_client)
    yield gate
    await _drop_gate(gate["category_id"])


async def _add_cookie(
    http: AsyncClient, gate_id: int, value: str, label: str | None = None
) -> int:
    res = await http.post(
        "/api/cookies",
        json={"gate_id": gate_id, "value": value, "label": label},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _lines_of(batch_id: int) -> list[BatchLine]:
    async with async_session_factory() as session:
        stmt = (
            select(BatchLine)
            .where(BatchLine.batch_id == batch_id)
            .order_by(BatchLine.position)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _batch_row(batch_id: int) -> Batch:
    async with async_session_factory() as session:
        row = await session.get(Batch, batch_id)
        assert row is not None
        return row


async def _send_log_rows(batch_id: int) -> list[SendLog]:
    async with async_session_factory() as session:
        stmt = (
            select(SendLog).where(SendLog.batch_id == batch_id).order_by(SendLog.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _cookies(gate_id: int) -> list[GateCookie]:
    async with async_session_factory() as session:
        stmt = (
            select(GateCookie)
            .where(GateCookie.gate_id == gate_id)
            .order_by(GateCookie.id)
        )
        return list((await session.execute(stmt)).scalars().all())


async def _full_rows(capture_session_id: int) -> list[Response]:
    async with async_session_factory() as session:
        return await responses_repo.list_full(session, capture_session_id, None)


async def _cc_rows(capture_session_id: int) -> list[Response]:
    async with async_session_factory() as session:
        return await responses_repo.list_cc(session, capture_session_id, None)


async def _capture_session_id(batch_id: int) -> int:
    async with async_session_factory() as session:
        row = await session.get(Batch, batch_id)
        assert row is not None and row.capture_session_id is not None
        return row.capture_session_id


async def _force_awaiting_elapsed(batch_id: int) -> None:
    """Push ``awaiting_verdict_until`` into the past (timeout-sweep trigger)."""
    async with async_session_factory() as session:
        await session.execute(
            update(Batch)
            .where(Batch.id == batch_id)
            .values(awaiting_verdict_until=func.now() - func.make_interval(0, 0, 0, 0, 0, 0, 5))
        )
        await session.commit()


def _verdict_reply(message_id: int, reply_to: int, text: str) -> IncomingReply:
    """A bot verdict reply (chat_id 0, the FakeGateway single-id-space sentinel).

    ``reply_to`` is the ``.amz`` message_id the worker awaits — attribution
    keys on ``(chat_id, reply_to_msg_id)`` → send_log → line/batch.
    """
    return IncomingReply(
        message_id=message_id,
        reply_to_msg_id=reply_to,
        text=text,
        edited=False,
        chat_id=0,
    )


async def _amz_message_id(batch_id: int) -> int:
    """The recorded ``.amz`` message_id (the awaited one) for a cookie-mode
    batch's single in-flight line."""
    rows = await _send_log_rows(batch_id)
    confirmed = [r for r in rows if r.message_id is not None]
    assert confirmed, "no confirmed .amz send_log row"
    return confirmed[-1].message_id


# =========================================================================
# (a) Atomic pair: .cookie then .amz, no interleaved pick_next, only .amz logs
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_pair_sent_no_picknext_between_only_amz_in_send_log(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One ``step()`` fires exactly two sends — ``.cookie <value>`` then
    ``.amz <line>`` — with ``scheduler.pick_next`` NOT called between them, only
    the ``.amz`` line owns a ``send_log`` row, and the batch ends up armed
    (``awaiting_verdict_until`` set + the awaited ``message_id``)."""
    http, _ = client_user
    cookie_value = f"cookieAAA-{uuid.uuid4().hex}"
    await _add_cookie(http, cookie_gate["id"], cookie_value)
    batch_id = await _post_batch(
        http, "4111111111111111\n4222222222222222", cookie_gate["id"]
    )

    # Trip a flag the instant pick_next is called AFTER the first send, so an
    # interleaved selection between .cookie and .amz would be caught.
    from app.core.scheduler import scheduler

    real_pick = scheduler.pick_next
    pick_calls_after_first_send: list[int] = []

    def spy_pick(active):  # type: ignore[no-untyped-def]
        pick_calls_after_first_send.append(len(fake_gateway.sent))
        return real_pick(active)

    monkeypatch.setattr(scheduler, "pick_next", spy_pick)

    assert await send_worker.step() is True

    # Exactly two sends, .cookie BEFORE .amz, value verbatim.
    assert len(fake_gateway.sent) == 2
    assert fake_gateway.sent[0] == f".cookie {cookie_value}"
    assert fake_gateway.sent[1].startswith(cookie_gate["value"])
    assert "4111111111111111" in fake_gateway.sent[1]
    # pick_next ran exactly once, and BEFORE any send (len 0) — never between
    # the .cookie (len 1) and the .amz.
    assert pick_calls_after_first_send == [0]

    # Only the .amz line owns a send_log row (.cookie is side-band).
    rows = await _send_log_rows(batch_id)
    assert len(rows) == 1
    assert rows[0].message_id is not None
    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"
    assert lines[1].state == "queued"  # the second line is NOT sent yet

    # The serialize gate is armed.
    batch = await _batch_row(batch_id)
    assert batch.awaiting_verdict_until is not None
    assert batch.awaiting_message_id == rows[0].message_id
    assert batch.awaiting_chat_id == 0
    assert batch.state == "sending"  # NOT completed — awaits the verdict


# =========================================================================
# (b) Serialize gate: active_senders skips the awaiting tenant; another flows
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_active_senders_skips_awaiting_tenant_other_tenant_still_sends(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """While the cookie-mode tenant awaits its verdict (future
    ``awaiting_verdict_until``), ``active_senders`` does not return it and a
    second ``step()`` does NOT re-send its line — but a DIFFERENT tenant's
    non-cookie line still goes out. Once the await clears, the tenant is picked
    again."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    cookie_batch = await _post_batch(http, "4111\n4222", cookie_gate["id"])

    # A second, NON-cookie-mode tenant with a plain line.
    other = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30),
        email_prefix="amz-other",
    )
    other_http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(other_http, other.email)
        other_batch = await _post_batch(other_http, "plain-line", gate["id"])

        # Step 1: the cookie-mode pair goes out (cookie tenant has a lower id and
        # the round-robin can land on either, so drain until the cookie pair is
        # armed). We force the cookie tenant first by stepping until armed.
        await send_worker.step()
        batch = await _batch_row(cookie_batch)
        # If the first pick was the OTHER tenant, step again to arm the cookie one.
        if batch.awaiting_verdict_until is None:
            await send_worker.step()
            batch = await _batch_row(cookie_batch)
        assert batch.awaiting_verdict_until is not None  # cookie tenant armed

        # active_senders must NOT return the awaiting cookie tenant.
        async with async_session_factory() as session:
            senders = await batches_repo.active_senders(session, global_interval=1.0)
        sender_tenants = {s.tenant_id for s in senders}
        cookie_tenant = (await _batch_row(cookie_batch)).tenant_id
        assert cookie_tenant not in sender_tenants  # serialize hold

        # The other tenant's non-cookie line still sends on the next step(s).
        sent_before = len(fake_gateway.sent)
        for _ in range(3):
            if await _batch_state(other_batch) == "completed":
                break
            await send_worker.step()
        assert await _batch_state(other_batch) == "completed"
        # The cookie line did NOT re-send while awaiting.
        cookie_rows = await _send_log_rows(cookie_batch)
        assert len([r for r in cookie_rows if r.message_id is not None]) == 1

        # Clear the await → the cookie tenant is selectable again.
        async with async_session_factory() as session:
            b = await session.get(Batch, cookie_batch)
            await batches_repo.clear_awaiting_verdict(session, b)
            await session.commit()
        async with async_session_factory() as session:
            senders = await batches_repo.active_senders(session, global_interval=1.0)
        assert cookie_tenant in {s.tenant_id for s in senders}
    finally:
        await other_http.aclose()
        async with async_session_factory() as session:
            await session.execute(
                delete(Batch).where(Batch.tenant_id == other.tenant_id)
            )
            await session.commit()
        await cleanup_users({other.email})


async def _batch_state(batch_id: int) -> str | None:
    async with async_session_factory() as session:
        return await batches_repo.get_batch_state(session, batch_id)


# =========================================================================
# (c) Classification: Approved → Filtrada ok + bare card; Declined → rejected
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_approved_verdict_saves_filtrada_card_and_consumes_line(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Approved → a full ``ok`` row + the BARE card in Filtrada (no Status/
    Response noise), CC dedup, the line consumed, cookie still active, the
    batch (single line) completes once the verdict lands."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    # The bot's Approved reply (the card glued to ⌿ Status: on one line).
    await capture.process_incoming(_verdict_reply(9001, amz_id, _APPROVED))
    # Apply the verdict signal (the worker's fast path).
    await send_worker._drain_verdicts()

    # Filtrada carries exactly the bare card — no Status/Approved/Response noise.
    cc_rows = await _cc_rows(csid)
    assert [r.text for r in cc_rows] == [_APPROVED_CARD]
    for token in ("Status", "Approved", "Response", "⌿"):
        assert token not in cc_rows[0].text

    # A full ok revision was persisted; the cookie stays active.
    full_rows = await _full_rows(csid)
    assert [(r.status) for r in full_rows] == ["ok"]
    cookies = await _cookies(cookie_gate["id"])
    assert all(c.status == "active" for c in cookies)

    # The line is consumed and the (single-line) batch completed.
    batch = await _batch_row(batch_id)
    assert batch.awaiting_verdict_until is None
    assert batch.state == "completed"

    # CC dedup: a SECOND identical Approved edit adds no new Filtrada row.
    await capture.process_incoming(_verdict_reply(9001, amz_id, _APPROVED))
    await send_worker._drain_verdicts()
    assert len(await _cc_rows(csid)) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_declined_verdict_rejected_full_row_nothing_in_filtrada_cookie_alive(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Declined → a full ``rejected`` row IS written (Completa + reconciler
    idempotency), NOTHING goes to Filtrada, the cookie stays ACTIVE, the line is
    consumed."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    await capture.process_incoming(_verdict_reply(9101, amz_id, _DECLINED))
    await send_worker._drain_verdicts()

    full_rows = await _full_rows(csid)
    assert [r.status for r in full_rows] == ["rejected"]  # Completa shows it
    assert await _cc_rows(csid) == []  # nothing in Filtrada
    cookies = await _cookies(cookie_gate["id"])
    assert all(c.status == "active" for c in cookies)  # cookie ALIVE
    batch = await _batch_row(batch_id)
    assert batch.awaiting_verdict_until is None
    assert batch.state == "completed"


# =========================================================================
# (d) Rotation: cookie dead → next cookie (never the first again), same line
#     re-queued, NEW message_id, dead attempt stays attributed, Completa once
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_dead_rotates_to_second_cookie_resend_new_message_id(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    first = await _add_cookie(http, cookie_gate["id"], f"ck-first-{uuid.uuid4().hex}")
    second = await _add_cookie(http, cookie_gate["id"], f"ck-second-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)

    # First attempt — uses the OLDEST active cookie (first).
    assert await send_worker.step() is True
    amz_id_1 = await _amz_message_id(batch_id)
    first_cookie_value = fake_gateway.sent[0]  # ".cookie <first value>"

    # The bot says the cookie is dead.
    await capture.process_incoming(_verdict_reply(9201, amz_id_1, _COOKIE_DEAD))
    await send_worker._drain_verdicts()

    # The first cookie is PURGED from the vault; the line is re-queued (same pos).
    cookies = {c.id: c.status for c in await _cookies(cookie_gate["id"])}
    assert first not in cookies  # hard-deleted on the dead verdict — gone
    assert cookies[second] == "active"
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"  # SAME line re-queued
    assert lines[0].position == 0  # same position
    # The dead attempt persisted a full revision (so the reconciler idempotency
    # holds) AND stays attributed (it has a line_id, not unmatched).
    dead_full = await _full_rows(csid)
    assert len(dead_full) == 1 and dead_full[0].status == "rejected"
    assert dead_full[0].line_id is not None
    assert capture.unmatched_total() == 0  # the dead attempt is NOT unmatched

    # Resend uses the SECOND cookie (never the first again) and a NEW message_id.
    assert await send_worker.step() is True
    # The .cookie of the resend is the second value, NOT the first.
    resend_cookie_value = fake_gateway.sent[-2]
    assert resend_cookie_value != first_cookie_value
    amz_id_2 = await _amz_message_id(batch_id)
    assert amz_id_2 != amz_id_1  # NEW message_id for the same line

    # Approve the resend → Completa shows the line ONCE (latest-revision per
    # message_id; the dead attempt and the approved attempt have distinct ids).
    await capture.process_incoming(_verdict_reply(9202, amz_id_2, _APPROVED))
    await send_worker._drain_verdicts()
    full_rows = await _full_rows(csid)
    # Two revisions exist (the dead attempt + the approved attempt), each keyed
    # on its own bot-reply message_id — but they share ONE line_id, so Completa
    # renders ONE visible line (latest-revision-per-message across attempts).
    line_ids = {r.line_id for r in full_rows}
    assert len(line_ids) == 1  # Completa shows the line once
    assert {r.status for r in full_rows} == {"rejected", "ok"}
    assert len(full_rows) == 2  # the dead attempt + the approved resend
    batch = await _batch_row(batch_id)
    assert batch.state == "completed"


# =========================================================================
# (d2) Manual delete of a cookie that a sent line references via
#      ``failed_cookie_id`` succeeds (204) — the FK is ON DELETE SET NULL, NOT
#      the old RESTRICT that raised the 500 behind "Ocurrió un error inesperado."
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_delete_cookie_referenced_by_sent_line_returns_204(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """Bug-#1 regression: every cookie-mode send stamps
    ``BatchLine.failed_cookie_id = <sent cookie>``, so a still-saved cookie is
    referenced. Deleting it must NOT raise ForeignKeyViolation → unmapped 500;
    the FK ``ON DELETE SET NULL`` nulls the reference and the delete returns 204."""
    http, _ = client_user
    cid = await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    # One send stamps the line's failed_cookie_id with the cookie just sent.
    assert await send_worker.step() is True
    lines = await _lines_of(batch_id)
    assert lines[0].failed_cookie_id == cid  # the line now references the cookie

    # The manual delete must succeed (no 500) — the FK SET NULL releases the ref.
    res = await http.delete(f"/api/cookies/{cid}")
    assert res.status_code == 204, res.text
    assert await _line_failed_cookie_id(lines[0].id) is None  # reference nulled
    assert await _cookies(cookie_gate["id"]) == []  # gone from the vault


# =========================================================================
# (e) Exhaustion: all-dead → pause cookies_exhausted → add cookie → resume →
#     the failed line is the very next send (stale future awaiting doesn't skip)
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_all_cookies_dead_pauses_exhausted_then_resume_sends_failed_line(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    http, _ = client_user
    only = await _add_cookie(http, cookie_gate["id"], f"ck-only-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    # The single cookie dies → no active cookie remains → pause cookies_exhausted.
    await capture.process_incoming(_verdict_reply(9301, amz_id, _COOKIE_DEAD))
    await send_worker._drain_verdicts()

    batch = await _batch_row(batch_id)
    assert batch.state == "paused"
    assert batch.pause_reason == "cookies_exhausted"
    # The WS frame carries the reason.
    paused = [
        d for _, e, d in events
        if e == "batch.state" and d.get("pause_reason") == "cookies_exhausted"
    ]
    assert paused, "no cookies_exhausted batch.state frame emitted"
    cookies = await _cookies(cookie_gate["id"])
    assert cookies == []  # the only cookie was purged → the vault is empty

    # The client adds a cookie and resumes — resume must clear the (stale,
    # possibly future) await fields AND re-queue the failed line in ONE txn.
    await _add_cookie(http, cookie_gate["id"], f"ck-rescue-{uuid.uuid4().hex}")
    res = await http.post(f"/api/batches/{batch_id}/resume")
    assert res.status_code == 204, res.text

    batch = await _batch_row(batch_id)
    assert batch.state == "sending"
    assert batch.awaiting_verdict_until is None  # no stale gate post-resume
    assert batch.pause_reason is None

    # The previously-failed line is the VERY NEXT thing sent (a stale future
    # awaiting_verdict_until must NOT skip it post-resume).
    sent_before = len(fake_gateway.sent)
    assert await send_worker.step() is True
    assert len(fake_gateway.sent) == sent_before + 2  # .cookie + .amz again
    assert "4111111111111111" in fake_gateway.sent[-1]


# =========================================================================
# (f) Format error → line failed (amazon_format_error), cookie unchanged
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_format_error_marks_line_failed_cookie_unchanged(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "garbage-line", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    await capture.process_incoming(_verdict_reply(9401, amz_id, _FORMAT_ERROR))
    await send_worker._drain_verdicts()

    lines = await _lines_of(batch_id)
    assert lines[0].state == "failed"
    assert lines[0].fail_code == "amazon_format_error"
    # The cookie was NOT rotated.
    cookies = await _cookies(cookie_gate["id"])
    assert all(c.status == "active" for c in cookies)
    # A terminal full marker was persisted; nothing in Filtrada.
    assert len(await _full_rows(csid)) == 1
    assert await _cc_rows(csid) == []
    batch = await _batch_row(batch_id)
    assert batch.awaiting_verdict_until is None
    assert batch.state == "completed"  # the only line is terminal → drained


# =========================================================================
# (g) Cookie-confirmation reply → no response row, no unmatched bump, no alert
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_confirmation_dropped_no_row_no_unmatched_no_alert(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``…almacenó tu cookie correctamente. ✅`` confirmation is content-
    sniffed away at the very top of ``process_incoming``: no response/CC row, no
    ``_unmatched_total`` bump, no ``alerts.note_unmatched``, no verdict signal."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)

    assert await send_worker.step() is True

    note_unmatched_calls = {"n": 0}

    async def spy_note_unmatched() -> None:
        note_unmatched_calls["n"] += 1

    monkeypatch.setattr(alerts, "note_unmatched", spy_note_unmatched)

    before = capture.unmatched_total()
    pending_before = cookie_verdict.pending()
    # The confirmation reply attributes to nothing (no send_log row for .cookie)
    # but is dropped by the content-sniff, NOT by the attribution-miss path.
    await capture.process_incoming(
        IncomingReply(
            message_id=9501,
            reply_to_msg_id=None,
            text=_COOKIE_CONFIRMATION,
            edited=False,
            chat_id=0,
        )
    )

    assert capture.unmatched_total() == before  # no unmatched bump
    assert note_unmatched_calls["n"] == 0  # no ban-guardrail alert
    assert cookie_verdict.pending() == pending_before  # no verdict signal
    # No response/CC row was written by the confirmation.
    assert await _full_rows(csid) == []
    assert await _cc_rows(csid) == []


# =========================================================================
# (h) Reconciler/edit replay: same cookie-dead reply twice → dead once, one
#     re-queue, no spurious exhaustion
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_replayed_cookie_dead_reply_rotates_once_no_spurious_exhaustion(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """The 45s reconciler (or an edit) re-feeds the SAME cookie-dead reply: the
    no-op-edit guard returns early on the second pass, so only ONE rotation
    fires — the first cookie dies once, the line is re-queued once, the SECOND
    cookie stays active (no Declined/dead treadmill, no spurious exhaustion)."""
    http, _ = client_user
    first = await _add_cookie(http, cookie_gate["id"], f"ck-A-{uuid.uuid4().hex}")
    second = await _add_cookie(http, cookie_gate["id"], f"ck-B-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    # First delivery of the dead reply → rotates (first cookie purged, line queued).
    await capture.process_incoming(_verdict_reply(9601, amz_id, _COOKIE_DEAD))
    await send_worker._drain_verdicts()
    cookies = {c.id: c.status for c in await _cookies(cookie_gate["id"])}
    assert first not in cookies and cookies[second] == "active"
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"

    pending_before = cookie_verdict.pending()
    # REPLAY the identical dead reply (same message_id, same text, same status):
    # the no-op-edit guard in capture returns early → NO second verdict signal.
    await capture.process_incoming(_verdict_reply(9601, amz_id, _COOKIE_DEAD))
    assert cookie_verdict.pending() == pending_before  # no new signal
    await send_worker._drain_verdicts()  # nothing to apply

    # The second cookie is STILL active — no spurious second rotation/exhaustion.
    cookies = {c.id: c.status for c in await _cookies(cookie_gate["id"])}
    assert first not in cookies and cookies[second] == "active"
    batch = await _batch_row(batch_id)
    assert batch.state == "sending"  # not paused cookies_exhausted
    assert batch.pause_reason is None


# =========================================================================
# (i) Timeout fires then the original verdict lands → the late verdict for the
#     superseded message_id is dropped (attempt-fenced)
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_timeout_retry_then_late_original_verdict_is_dropped(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """First the verdict times out (retry-once with a fresh awaited
    ``message_id``); then the ORIGINAL (superseded) verdict finally arrives. The
    worker drops it under the attempt-fence — no double-advance, no double-save,
    no rotating the healthy cookie."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)

    assert await send_worker.step() is True
    amz_id_1 = await _amz_message_id(batch_id)

    # The verdict times out → the sweep retries the line ONCE (intent reset +
    # re-queue), and the next step() resends with a NEW awaited message_id.
    await _force_awaiting_elapsed(batch_id)
    await send_worker._sweep_verdict_timeouts()
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"  # re-queued for the retry
    assert await send_worker.step() is True
    amz_id_2 = await _amz_message_id(batch_id)
    assert amz_id_2 != amz_id_1  # the resend supersedes the timed-out attempt

    # The ORIGINAL verdict (for amz_id_1) finally lands — it must be dropped.
    await capture.process_incoming(_verdict_reply(9701, amz_id_1, _APPROVED))
    await send_worker._drain_verdicts()

    # The batch is STILL awaiting the SECOND attempt's verdict (not advanced).
    batch = await _batch_row(batch_id)
    assert batch.awaiting_message_id == amz_id_2
    assert batch.awaiting_verdict_until is not None
    assert batch.state == "sending"  # not completed by the stale verdict

    # The current (second) attempt's verdict still consumes the line normally.
    await capture.process_incoming(_verdict_reply(9702, amz_id_2, _APPROVED))
    await send_worker._drain_verdicts()
    batch = await _batch_row(batch_id)
    assert batch.awaiting_verdict_until is None
    assert batch.state == "completed"


# =========================================================================
# (j) FloodWait on .amz after .cookie → no bare .amz retry, line re-queues
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_floodwait_on_amz_after_cookie_is_pair_abort_no_bare_amz_retry(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FloodWait on the ``.amz`` AFTER the ``.cookie`` already went out is a
    PAIR-ABORT: the line is released/re-queued, NO bare ``.amz`` retry fires,
    and the next attempt re-sends the FULL pair (``.cookie`` first)."""
    http, _ = client_user
    cookie_value = f"ck-{uuid.uuid4().hex}"
    await _add_cookie(http, cookie_gate["id"], cookie_value)
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    # The ``.cookie`` succeeds; only the ``.amz`` raises FloodWait — a gateway
    # that fails the FIRST non-``.cookie`` send so the FloodWait lands AFTER the
    # cookie went out (the pair-abort scenario, not a cookie-send failure).
    class AmzFloodGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.amz_failed = False

        async def send(self, text: str) -> tuple[int, int]:
            if not text.startswith(".cookie ") and not self.amz_failed:
                self.amz_failed = True
                raise FloodWaitError(request=None, capture=7)
            return await super().send(text)

    fake_gateway = AmzFloodGateway()
    monkeypatch.setattr(send_worker, "gateway", fake_gateway)

    assert await send_worker.step() is False  # pair aborted, nothing recorded

    # Exactly ONE send happened — the .cookie. No bare .amz retry.
    assert len(fake_gateway.sent) == 1
    assert fake_gateway.sent[0] == f".cookie {cookie_value}"
    # The line is back in the queue, intent unconfirmed, no await armed.
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"
    rows = await _send_log_rows(batch_id)
    assert all(r.message_id is None for r in rows)  # nothing confirmed
    batch = await _batch_row(batch_id)
    assert batch.awaiting_verdict_until is None
    # A global FloodWait window opened (the account is protected).
    from app.core.scheduler import scheduler

    assert scheduler.flood_remaining() > 0.0


# =========================================================================
# (k) The cookie value appears in NO emitted event/log
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_value_never_in_events_or_logs(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The credential never leaks: it appears in NO broadcaster event and in NO
    log line (only ``tenant_id``/``gate_id``/``cookie_id``/MASKED). Drives a full
    pair + a rotation (dead → resend) so the cookie touches the send + the
    rotation log paths."""
    caplog.set_level(logging.INFO)
    http, _ = client_user
    secret_a = f"SUPERSECRET-A-{uuid.uuid4().hex}"
    secret_b = f"SUPERSECRET-B-{uuid.uuid4().hex}"
    await _add_cookie(http, cookie_gate["id"], secret_a)
    await _add_cookie(http, cookie_gate["id"], secret_b)
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)
    await capture.process_incoming(_verdict_reply(9801, amz_id, _COOKIE_DEAD))
    await send_worker._drain_verdicts()
    assert await send_worker.step() is True  # resend with the second cookie
    amz_id_2 = await _amz_message_id(batch_id)
    await capture.process_incoming(_verdict_reply(9802, amz_id_2, _APPROVED))
    await send_worker._drain_verdicts()

    # The raw cookie value went to Telegram (in fake_gateway.sent) — that is the
    # ONLY place it may appear. It must NOT be in any emitted event payload …
    for _tenant, _event, data in events:
        assert secret_a not in repr(data)
        assert secret_b not in repr(data)
    # … nor in any captured log record.
    assert secret_a not in caplog.text
    assert secret_b not in caplog.text
    # Sanity: the masked marker and the safe identifiers ARE logged.
    assert "MASKED" in caplog.text or "cookie=" in caplog.text


# =========================================================================
# Review-loopback regression tests (2026-06-19 Spec Change Log — the three
# bad_spec findings re-implemented around the ATTEMPT-FENCE, not LINE_SENT).
# =========================================================================
#
# Root cause of all three: the first impl found "the line awaiting a verdict"
# by LINE STATE (``LINE_SENT``) instead of the attempt-fence
# (``Batch.awaiting_message_id`` → ``send_log`` → ``line_id``). A consumed
# (approved/declined) line correctly STAYS ``LINE_SENT`` (like any normal sent
# line), so a multi-line cookie batch holds several ``LINE_SENT`` rows while
# only ONE is actually in-flight — keying off ``LINE_SENT`` re-sends consumed
# lines (duplicate ``.amz``/Completa/CC/charge).


async def _awaiting_message_id(batch_id: int) -> int:
    """The batch's currently-armed awaited ``.amz`` message_id (the fence)."""
    batch = await _batch_row(batch_id)
    assert batch.awaiting_message_id is not None, "batch is not awaiting a verdict"
    return batch.awaiting_message_id


async def _line_failed_cookie_id(line_id: int) -> int | None:
    async with async_session_factory() as session:
        line = await session.get(BatchLine, line_id)
        assert line is not None
        return line.failed_cookie_id


# =========================================================================
# T1 (HIGH): a multi-line cookie batch never re-sends an already-CONSUMED line.
#     Line 1 sent+approved (consumed, stays LINE_SENT); line 2 in-flight,
#     awaiting. (a) timeout-sweep retries ONLY line 2; (b) verdict_timeout pause
#     + resume re-sends ONLY line 2.
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_multiline_timeout_sweep_retries_only_awaited_line_not_consumed(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """TWO lines, ONE cookie. Send+approve line 1 (now ``LINE_SENT``, consumed),
    send line 2 (in-flight, awaiting). Force line 2's verdict-timeout to elapse
    and run the sweep → ONLY line 2 is re-queued/retried; the consumed line 1 is
    NOT re-sent (no duplicate ``.amz`` / Completa / CC). This is THE HIGH bug:
    the sweep must resolve the awaited line via the attempt-fence, not by
    re-sending every ``LINE_SENT`` row."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(
        http, "4111111111111111\n4222222222222222", cookie_gate["id"]
    )
    csid = await _capture_session_id(batch_id)

    # Line 1: send the pair, approve it → consumed (stays LINE_SENT).
    assert await send_worker.step() is True
    amz_id_1 = await _awaiting_message_id(batch_id)
    await capture.process_incoming(_verdict_reply(11001, amz_id_1, _APPROVED))
    await send_worker._drain_verdicts()
    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"  # consumed line 1 STAYS sent
    assert lines[1].state == "queued"  # line 2 not yet sent
    # One full ok + one Filtrada card so far (line 1 only).
    assert [r.status for r in await _full_rows(csid)] == ["ok"]
    assert [r.text for r in await _cc_rows(csid)] == [_APPROVED_CARD]

    # Line 2: send the pair → in-flight, awaiting its own verdict.
    assert await send_worker.step() is True
    amz_id_2 = await _awaiting_message_id(batch_id)
    assert amz_id_2 != amz_id_1
    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"  # line 1 STILL consumed
    assert lines[1].state == "sent"  # line 2 in-flight (.amz went out)
    sent_count_before = len(fake_gateway.sent)

    # Line 2's verdict times out → the sweep retries ONCE. ONLY line 2 is
    # re-queued; line 1 (consumed) is untouched (the fence resolves line 2).
    await _force_awaiting_elapsed(batch_id)
    await send_worker._sweep_verdict_timeouts()
    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"  # 🔒 consumed line 1 NOT re-queued
    assert lines[1].state == "queued"  # ONLY the awaited line 2 retried
    batch = await _batch_row(batch_id)
    assert batch.awaiting_verdict_until is None  # await cleared for the retry

    # The retry step re-sends ONLY line 2 (the .amz carries line 2's text);
    # line 1's text never goes out again.
    assert await send_worker.step() is True
    new_sends = fake_gateway.sent[sent_count_before:]
    assert any("4222222222222222" in s for s in new_sends)  # line 2 re-sent
    assert all("4111111111111111" not in s for s in new_sends)  # NOT line 1

    # No duplicate Completa/CC for line 1 — still exactly one ok + one card.
    assert [r.status for r in await _full_rows(csid)] == ["ok"]
    assert [r.text for r in await _cc_rows(csid)] == [_APPROVED_CARD]


@pytest.mark.asyncio(loop_scope="session")
async def test_multiline_verdict_timeout_pause_resume_resends_only_awaited_line(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """TWO lines, ONE cookie. Line 1 consumed (approved, LINE_SENT); line 2
    in-flight. Drive line 2 to a ``verdict_timeout`` pause (TWO silent elapses:
    the first retries once, the second pauses), then resume → ONLY line 2 is
    re-sent; the consumed line 1 stays consumed (``requeue_failed_cookie_line``
    re-queues the AWAITED line via the fence, never every ``LINE_SENT`` row).

    🔴 CURRENTLY FAILS — surfaces a REAL impl bug (NOT a test artifact). The
    ``verdict_timeout`` pause runs through ``send_worker._pause_cookie_batch``,
    which calls ``clear_awaiting_verdict`` BEFORE pausing
    (send_worker.py:571). So by resume time all three await fields are NULL, and
    ``resume_batch`` → ``requeue_failed_cookie_line`` resolves the awaited line
    via the attempt-fence (``awaited_line_id``, which needs those fields) and
    finds NOTHING — the awaited line 2 stays ``LINE_SENT`` and is NEVER
    re-queued. The batch resumes 'sending' with no servable line and no await,
    so the worker never touches it again: line 2 is permanently STRANDED and the
    batch never completes. This contradicts ``requeue_failed_cookie_line``'s own
    docstring ("a ``verdict_timeout`` pause leaves the awaited line in
    ``LINE_SENT`` … resume MUST hand it back to the queue") and the spec's
    Acceptance Criteria. Root cause: the EngineFix FIX #3 added
    ``clear_awaiting_verdict`` to the pause paths but the ``verdict_timeout``
    RESUME path still relies on the (now-cleared) fence to find the line — the
    ``verdict_timeout`` pause must either NOT clear the await, or re-queue the
    awaited line itself before clearing (mirroring the ``cookies_exhausted``
    branch, which DOES re-queue before clearing)."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(
        http, "4111111111111111\n4222222222222222", cookie_gate["id"]
    )
    csid = await _capture_session_id(batch_id)

    # Line 1: send + approve → consumed.
    assert await send_worker.step() is True
    amz_id_1 = await _awaiting_message_id(batch_id)
    await capture.process_incoming(_verdict_reply(11101, amz_id_1, _APPROVED))
    await send_worker._drain_verdicts()

    # Line 2: send the pair → in-flight.
    assert await send_worker.step() is True

    # First elapse → retry once (line 2 re-queued, verdict_timeout_retries → 1).
    await _force_awaiting_elapsed(batch_id)
    await send_worker._sweep_verdict_timeouts()
    assert await send_worker.step() is True  # the retry resend (new amz id)
    # Second elapse → pause verdict_timeout (the bot stayed silent).
    await _force_awaiting_elapsed(batch_id)
    await send_worker._sweep_verdict_timeouts()

    batch = await _batch_row(batch_id)
    assert batch.state == "paused"
    assert batch.pause_reason == "verdict_timeout"

    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"  # line 1 consumed throughout

    # Resume → ONLY line 2 is re-queued (the awaited line, via the fence) and is
    # the very next send; line 1 is never re-sent.
    res = await http.post(f"/api/batches/{batch_id}/resume")
    assert res.status_code == 204, res.text
    batch = await _batch_row(batch_id)
    assert batch.state == "sending"
    assert batch.pause_reason is None
    assert batch.awaiting_verdict_until is None
    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"  # 🔒 line 1 still consumed
    assert lines[1].state == "queued"  # ONLY line 2 back in the queue

    sent_count_before = len(fake_gateway.sent)
    assert await send_worker.step() is True
    new_sends = fake_gateway.sent[sent_count_before:]
    assert any("4222222222222222" in s for s in new_sends)  # line 2 re-sent
    assert all("4111111111111111" not in s for s in new_sends)  # NOT line 1
    # Line 1 produced exactly one Completa/CC the whole time (no duplicate).
    assert [r.status for r in await _full_rows(csid)] == ["ok"]
    assert [r.text for r in await _cc_rows(csid)] == [_APPROVED_CARD]


@pytest.mark.asyncio(loop_scope="session")
async def test_verdict_timeout_retry_budget_is_durable_across_restart(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """The verdict-timeout retry-once budget survives a worker restart.

    Regression for the deferred crash-loop bug: the budget used to live in the
    process-memory ``send_worker._timeout_retried`` set, reset on restart — so a
    permanently-silent line in a crash loop got a FRESH retry (a fresh
    ``.cookie``+``.amz`` on the shared account) EVERY restart instead of pausing
    after the single mandated retry. It is now durable on
    ``BatchLine.verdict_timeout_retries``. Simulate the restart by wiping every
    send-worker process-memory singleton (boot recovery re-arms the await with a
    fresh 90s but cannot restore an in-memory flag); the durable column must
    still drive the SECOND elapse to a pause, NOT another resend.
    """
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    # Send the pair → in-flight. First silent elapse → the ONE durable retry.
    assert await send_worker.step() is True
    await _force_awaiting_elapsed(batch_id)
    await send_worker._sweep_verdict_timeouts()
    lines = await _lines_of(batch_id)
    assert lines[0].verdict_timeout_retries == 1  # 🔒 durable: the budget is burned
    assert await send_worker.step() is True  # the retry resend (new amz id)

    # Simulate a worker RESTART: wipe every send-worker process-memory singleton.
    # The old in-memory ``_timeout_retried`` flag would vanish here (granting a
    # bogus fresh retry on the shared account); the durable column does NOT.
    send_worker._sent_by_tenant.clear()
    cookie_verdict.reset()

    # Boot recovery re-arms the await with a fresh 90s; force it elapsed again.
    await _force_awaiting_elapsed(batch_id)
    sent_before = len(fake_gateway.sent)
    await send_worker._sweep_verdict_timeouts()

    # SECOND elapse ⇒ pause ``verdict_timeout`` (durable budget already 1) — NOT
    # another ``.cookie``+``.amz`` resend on the shared account.
    batch = await _batch_row(batch_id)
    assert batch.state == "paused"
    assert batch.pause_reason == "verdict_timeout"
    assert fake_gateway.sent[sent_before:] == []


# =========================================================================
# T2 (MED): a ``cookie_dead`` verdict marks the cookie ACTUALLY SENT for the
#     in-flight attempt (``BatchLine.failed_cookie_id``), never a re-derived
#     "oldest active" — even when which cookie is oldest-active changes during
#     the await.
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_dead_marks_the_sent_cookie_not_oldest_active(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """≥2 active cookies. The in-flight line records the cookie it ACTUALLY sent
    in ``BatchLine.failed_cookie_id``. A ``cookie_dead`` verdict marks THAT
    cookie dead (not the re-derived oldest-active), the next pick is a DIFFERENT
    active cookie, and a concurrent change to which cookie is oldest-active
    (here: a brand-new cookie inserted with a LOWER-than-sent vault position is
    impossible — ids are monotonic — so we instead DELETE the sent cookie's
    successor mid-await to shuffle "oldest active") does NOT burn the wrong one:
    the fence is the stamped id, read back under the batch lock."""
    http, _ = client_user
    first = await _add_cookie(http, cookie_gate["id"], f"ck-1-{uuid.uuid4().hex}")
    second = await _add_cookie(http, cookie_gate["id"], f"ck-2-{uuid.uuid4().hex}")
    third = await _add_cookie(http, cookie_gate["id"], f"ck-3-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    # First attempt sends with the OLDEST active cookie (``first``) and STAMPS it.
    assert await send_worker.step() is True
    amz_id = await _awaiting_message_id(batch_id)
    lines = await _lines_of(batch_id)
    line_id = lines[0].id
    # 🔒 The line records the cookie ACTUALLY sent (proves the fence is the
    # stamp, not "oldest active" re-derived at verdict time).
    assert await _line_failed_cookie_id(line_id) == first

    # CONCURRENT vault change during the 90s await: delete ``second`` (the cookie
    # that WOULD be the next oldest-active). After this, the next-oldest active
    # is ``third`` — but the cookie to mark dead must STILL be ``first`` (the
    # stamped one), never re-derived from a now-shuffled "oldest active".
    res = await http.request(
        "DELETE", f"/api/cookies/{second}"
    )
    assert res.status_code in (200, 204), res.text

    # The bot says the cookie is dead.
    await capture.process_incoming(_verdict_reply(11201, amz_id, _COOKIE_DEAD))
    await send_worker._drain_verdicts()

    cookies = {c.id: c.status for c in await _cookies(cookie_gate["id"])}
    # 🔒 The STAMPED cookie (``first``) is PURGED from the vault — not ``third``
    # (the post-delete oldest active), not ``second`` (already gone).
    assert first not in cookies  # the stamped cookie was hard-deleted
    assert cookies.get(third) == "active"
    assert second not in cookies  # deleted mid-await

    # The same line is re-queued; the resend picks a DIFFERENT active cookie
    # (``third``, the only one left) — never the just-dead ``first``.
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"
    third_value = next(
        c.value for c in await _cookies(cookie_gate["id"]) if c.id == third
    )
    assert await send_worker.step() is True
    resend_cookie_send = fake_gateway.sent[-2]  # ".cookie <value>"
    assert resend_cookie_send == f".cookie {third_value}"  # the other active one
    # The new in-flight attempt stamps ``third``.
    assert await _line_failed_cookie_id(line_id) == third


# =========================================================================
# T3 (MED): a manual client pause DURING a verdict-await tears the gate down,
#     re-queues the awaited line, drops a verdict that arrives while paused, and
#     re-sends the awaited line on resume.
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_manual_pause_during_await_clears_gate_drops_verdict_resends(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """A cookie-mode line is in-flight awaiting. The client PAUSES (api pause):
    the await fields are cleared and the awaited line is re-queued. A verdict
    arriving WHILE paused is DROPPED (no rotation, no consume, no mutation of the
    paused batch). On resume the awaited line is re-sent fresh."""
    http, _ = client_user
    first = await _add_cookie(http, cookie_gate["id"], f"ck-A-{uuid.uuid4().hex}")
    second = await _add_cookie(http, cookie_gate["id"], f"ck-B-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)

    # Send the pair → in-flight, awaiting.
    assert await send_worker.step() is True
    amz_id = await _awaiting_message_id(batch_id)
    lines = await _lines_of(batch_id)
    line_id = lines[0].id

    # Manual pause DURING the await: gate torn down + awaited line re-queued.
    res = await http.post(f"/api/batches/{batch_id}/pause")
    assert res.status_code == 204, res.text
    batch = await _batch_row(batch_id)
    assert batch.state == "paused"
    assert batch.pause_reason is None  # a CLIENT pause, not a cookie pause
    assert batch.awaiting_message_id is None  # 🔒 gate cleared
    assert batch.awaiting_chat_id is None
    assert batch.awaiting_verdict_until is None
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"  # 🔒 awaited line re-queued for resume

    # A verdict (cookie_dead) lands WHILE the batch is paused. It must be DROPPED
    # — no rotation (the cookie stays active), no consume, no mutation. The
    # attempt-fence already failed (await cleared); the state-gate is the
    # second guard.
    await capture.process_incoming(_verdict_reply(11301, amz_id, _COOKIE_DEAD))
    await send_worker._drain_verdicts()
    cookies = {c.id: c.status for c in await _cookies(cookie_gate["id"])}
    assert cookies[first] == "active" and cookies[second] == "active"  # NO rotation
    batch = await _batch_row(batch_id)
    assert batch.state == "paused"  # still paused — not consumed/exhausted
    assert batch.pause_reason is None
    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"  # still queued, not consumed/failed
    # Capture persisted the dead-verdict full row (reconciler idempotency), but
    # the worker dropped the rotation — so NO Filtrada, NO line-failure, NO
    # second cookie burned.
    assert await _cc_rows(csid) == []

    # Resume → the awaited line is the very next thing re-sent (fresh pair).
    res = await http.post(f"/api/batches/{batch_id}/resume")
    assert res.status_code == 204, res.text
    batch = await _batch_row(batch_id)
    assert batch.state == "sending"
    sent_before = len(fake_gateway.sent)
    assert await send_worker.step() is True
    new_sends = fake_gateway.sent[sent_before:]
    assert len(new_sends) == 2  # .cookie + .amz fresh pair
    assert new_sends[0].startswith(".cookie ")
    assert "4111111111111111" in new_sends[1]
    # The re-sent line is in-flight again, armed with a NEW awaited message_id.
    batch = await _batch_row(batch_id)
    assert batch.awaiting_message_id is not None
    assert batch.awaiting_message_id != amz_id


# =========================================================================
# Audit regression (2026-06-22): three engine-safety fixes around the
# ``_record_sent`` → ``_arm_await`` boundary + the account-swap latch.
# =========================================================================
#
# M1 — fold the await-arm INTO the record txn (no standalone ``_arm_await``):
#      a crash can no longer leave a cookie line 'sent' with an un-armed await
#      (LINE_SENT is invisible to boot recovery → permanent 409 lockout).
# H1 — stamp ``failed_cookie_id`` DURABLY at cookie-pick time: a boot-recovery
#      re-arm (which never re-stamps) still lets a ``cookie_dead`` verdict purge
#      the RIGHT cookie instead of skipping the delete and resending forever.
# M2 — fence capture on ``REASON_ACCOUNT_CHANGED``: live/replayed replies on a
#      swapped account are dropped (no cross-tenant mis-attribution), while a
#      reply-rate / session-lost latch deliberately does NOT fence capture.


@pytest.mark.asyncio(loop_scope="session")
async def test_cookie_arm_folded_into_record_no_separate_arm_step(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """M1: the cookie await-arm is folded into ``_record_sent``'s SINGLE txn (the
    standalone two-phase ``_arm_await`` is gone), so the 'sent' state, the
    recorded ``message_id``, the armed await and the ``failed_cookie_id`` stamp
    are one atomic outcome — no crash window can strand a 'sent' cookie line with
    an un-armed await (which boot recovery, LINE_SENDING-only, never heals)."""
    # The separate arm function must no longer exist (the fold is the fix).
    assert not hasattr(send_worker, "_arm_await")

    http, _ = client_user
    only = await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    assert await send_worker.step() is True

    # One atomic outcome: line 'sent' + await armed + cookie stamped, together.
    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"
    assert lines[0].failed_cookie_id == only
    batch = await _batch_row(batch_id)
    assert batch.awaiting_message_id == await _amz_message_id(batch_id)
    assert batch.awaiting_verdict_until is not None

    # A properly-armed cookie line is NOT a stuck LINE_SENDING line — boot
    # recovery never sees it (so it is never re-queued/double-sent).
    async with async_session_factory() as session:
        stuck = await batches_repo.stuck_sending_lines(session)
    assert all(s.batch_id != batch_id for s in stuck)


@pytest.mark.asyncio(loop_scope="session")
async def test_boot_rearm_preserves_cookie_stamp_so_dead_verdict_purges_not_loops(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """H1: a crash AFTER the ``.amz`` delivered but BEFORE the record txn leaves
    the line LINE_SENDING with ``failed_cookie_id`` ALREADY stamped (the durable
    pick-time stamp). Boot recovery confirms the ``.amz`` and re-arms the await
    WITHOUT re-stamping — but because the stamp survived, a later ``cookie_dead``
    verdict PURGES the right cookie instead of skipping the delete and resending
    the same dead cookie forever (the resend storm on the shared account)."""
    http, _ = client_user
    only = await _add_cookie(http, cookie_gate["id"], f"ck-only-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    # Normal send → line 'sent', armed, failed_cookie_id stamped durably.
    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)
    amz_text = next(s for s in fake_gateway.sent if not s.startswith(".cookie "))
    lines = await _lines_of(batch_id)
    line_id = lines[0].id
    assert await _line_failed_cookie_id(line_id) == only

    # Rewind to the H1 crash window: line back to LINE_SENDING, the send_log
    # message_id un-recorded, the await un-armed — but the pick-time stamp KEPT.
    async with async_session_factory() as session:
        line = await session.get(BatchLine, line_id)
        line.state = "sending"
        await session.execute(
            update(SendLog).where(SendLog.batch_id == batch_id).values(message_id=None)
        )
        batch = await session.get(Batch, batch_id)
        await batches_repo.clear_awaiting_verdict(session, batch)
        await session.commit()
    # The delivered .amz is visible to recent_outgoing so boot recovery confirms.
    fake_gateway.outgoing = [(0, amz_id, amz_text)]

    await send_worker._boot_recovery()

    # Boot recovery confirmed the .amz and RE-ARMED the cookie await…
    batch = await _batch_row(batch_id)
    assert batch.awaiting_message_id == amz_id
    assert batch.awaiting_verdict_until is not None
    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"
    # …and the durable pick-time stamp SURVIVED the re-arm (re-arm never stamps).
    assert await _line_failed_cookie_id(line_id) == only

    # cookie_dead now PURGES the stamped cookie (no NULL-stamp skip → no loop).
    await capture.process_incoming(_verdict_reply(12101, amz_id, _COOKIE_DEAD))
    await send_worker._drain_verdicts()
    assert await _cookies(cookie_gate["id"]) == []  # 🔒 dead cookie purged
    batch = await _batch_row(batch_id)
    assert batch.state == "paused"
    assert batch.pause_reason == "cookies_exhausted"


@pytest.mark.asyncio(loop_scope="session")
async def test_account_changed_latch_fences_capture_but_session_lost_does_not(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    fake_gateway: FakeGateway,
) -> None:
    """M2: while the global pause is latched for an ACCOUNT SWAP, capture drops
    replies (a swapped account's restarted message-id sequence can mis-attribute
    one tenant's ✅/CC into another's). A reply-rate / session-lost latch does NOT
    fence capture — buffered replies must keep feeding the rate watchdog."""
    from app.core.watchdog import (
        REASON_ACCOUNT_CHANGED,
        REASON_SESSION_LOST,
        watchdog,
    )

    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])
    csid = await _capture_session_id(batch_id)
    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    # (1) Account-swap latch: an otherwise-attributable verdict is DROPPED.
    watchdog._paused = True
    watchdog._reason = REASON_ACCOUNT_CHANGED
    try:
        before = capture.unmatched_total()
        await capture.process_incoming(_verdict_reply(12201, amz_id, _APPROVED))
        assert await _full_rows(csid) == []  # nothing persisted
        assert await _cc_rows(csid) == []
        assert capture.unmatched_total() == before  # no unmatched bump
        batch = await _batch_row(batch_id)
        assert batch.state == "sending"  # not consumed/advanced
        assert batch.awaiting_verdict_until is not None  # gate still armed
    finally:
        watchdog.reset()

    # (2) A session-lost latch must NOT fence capture: the SAME verdict now
    # attributes, persists and consumes the line.
    watchdog._paused = True
    watchdog._reason = REASON_SESSION_LOST
    try:
        await capture.process_incoming(_verdict_reply(12201, amz_id, _APPROVED))
        await send_worker._drain_verdicts()
        assert [r.status for r in await _full_rows(csid)] == ["ok"]  # persisted
        batch = await _batch_row(batch_id)
        assert batch.state == "completed"  # consumed
    finally:
        watchdog.reset()


# =========================================================================
# Code-review regression (2026-06-22): two findings from the adversarial
# review of the audit fixes above.
# =========================================================================


@pytest.mark.asyncio(loop_scope="session")
async def test_pause_racing_between_amz_send_and_record_still_arms_await(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blind-Hunter regression: a manual pause can commit PAUSED between the
    ``.amz`` send and ``_record_sent`` (the worker holds NO lock during the send).
    At that moment the await is not yet armed, so ``pause_batch`` cannot re-queue
    the in-flight line. ``_record_sent`` must STILL arm the await on the
    now-paused batch — otherwise the 'sent' line is stranded (the timeout sweep
    needs the await; a manual resume re-queues only a cookie-pause), a permanent
    409 lockout and the exact M1 failure mode. The folded arm therefore arms on
    SENDING *or* PAUSED (matching the old unconditional ``_arm_await``)."""
    http, _ = client_user
    only = await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    # The instant the ``.amz`` goes out (BEFORE _record_sent runs), commit the
    # batch to PAUSED — exactly the manual-pause-mid-send race.
    class PauseOnAmzGateway(FakeGateway):
        async def send(self, text: str) -> tuple[int, int]:
            result = await super().send(text)
            if not text.startswith(".cookie "):
                async with async_session_factory() as s:
                    b = await s.get(Batch, batch_id)
                    assert b is not None
                    b.state = "paused"
                    await s.commit()
            return result

    monkeypatch.setattr(send_worker, "gateway", PauseOnAmzGateway())
    assert await send_worker.step() is True  # the .amz went out

    lines = await _lines_of(batch_id)
    assert lines[0].state == "sent"
    assert lines[0].failed_cookie_id == only
    batch = await _batch_row(batch_id)
    assert batch.state == "paused"
    # 🔒 The await is armed despite the paused state → the in-flight line is
    # recoverable (real verdict consumes it, or the timeout sweep resends).
    assert batch.awaiting_message_id == await _amz_message_id(batch_id)
    assert batch.awaiting_verdict_until is not None


@pytest.mark.asyncio(loop_scope="session")
async def test_pick_time_cookie_stamp_is_durable_before_record_phase(
    client_user: tuple[AsyncClient, User],
    cookie_gate: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """H1 isolation (Edge-Case-Hunter): the cookie stamp is committed at PICK
    time, BEFORE the sends — independent of ``_record_sent``'s re-stamp. A
    FloodWait on the ``.amz`` aborts the pair (release + re-queue) so
    ``_record_sent`` NEVER runs; the line must STILL carry ``failed_cookie_id``
    from the pick-time commit. If the pick-time stamp were reverted the stamp
    would be null here — the regression the end-to-end boot test cannot isolate
    (that test runs a full step where ``_record_sent`` also stamps)."""
    http, _ = client_user
    only = await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    class AmzFloodGateway(FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.amz_failed = False

        async def send(self, text: str) -> tuple[int, int]:
            if not text.startswith(".cookie ") and not self.amz_failed:
                self.amz_failed = True
                raise FloodWaitError(request=None, capture=7)
            return await super().send(text)

    monkeypatch.setattr(send_worker, "gateway", AmzFloodGateway())
    assert await send_worker.step() is False  # pair aborted, nothing recorded

    lines = await _lines_of(batch_id)
    assert lines[0].state == "queued"  # released + re-queued
    rows = await _send_log_rows(batch_id)
    assert all(r.message_id is None for r in rows)  # _record_sent never ran
    # 🔒 Stamped from the PICK-time commit, despite no record phase having run.
    assert lines[0].failed_cookie_id == only
