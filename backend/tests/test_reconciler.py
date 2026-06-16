"""Reply reconciler tests — recovering bot replies the Telethon update stream
dropped (catch_up gaps, missed ⏳→✅ edits).

Same idiom as test_attribution.py: real ASGI app against the dev Postgres,
self-seeding/cleaning, ``FakeGateway`` (incremental ids populate ``send_log``;
its ``incoming`` list stands in for chat history), events via a broadcaster
recorder. ASGITransport never runs the lifespan, so a pass is driven directly
through ``reconciler.reconcile_once`` (the ``step()`` idiom) and the re-fed
replies are processed via ``capture.process_incoming`` — no telethon anywhere.

The reconciler reads ``reconciler.gateway`` (its own module binding), so each
test patches THAT in addition to ``fake_gateway`` (which patches the worker's
binding so ``_drain`` fills send_log). Both point at one FakeGateway instance.

Run (from backend/, venv active):  pytest tests/test_reconciler.py
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core import capture, reconciler, send_worker
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.core.scheduler import scheduler
from app.core.watchdog import watchdog
from app.db.base import async_session_factory
from app.db.models import Batch, Response, SendLog, User
from httpx import AsyncClient
from sqlalchemy import select
from telethon.errors import FloodWaitError

from tests.conftest import FakeGateway

# --- Local helpers (mirrors of test_attribution.py) --------------------------


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


@pytest.fixture
def reconciler_gateway(
    fake_gateway: FakeGateway, monkeypatch: pytest.MonkeyPatch
) -> FakeGateway:
    """Point the reconciler's OWN gateway binding at the same FakeGateway the
    worker uses (``fake_gateway`` patched ``send_worker.gateway``)."""
    monkeypatch.setattr(reconciler, "gateway", fake_gateway)
    return fake_gateway


async def _post_batch(http: AsyncClient, text: str, gate_id: int) -> int:
    res = await http.post("/api/batches", json={"text": text, "gate_id": gate_id})
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


async def _drain_worker() -> None:
    while await send_worker.step():
        pass


async def _drain_capture_queue() -> None:
    """Process everything the reconciler re-fed (deterministic — no consumer
    task needed: the queue is module state and processing is synchronous)."""
    while not capture._queue.empty():
        await capture.process_incoming(capture._queue.get_nowait())


async def _sent_message_id(tenant_id: int) -> int:
    async with async_session_factory() as session:
        mid = (
            await session.execute(
                select(SendLog.message_id).where(SendLog.tenant_id == tenant_id)
            )
        ).scalar_one()
    assert mid is not None
    return int(mid)


async def _full_rows(message_id: int) -> list[Response]:
    async with async_session_factory() as session:
        return list(
            (
                await session.execute(
                    select(Response).where(
                        Response.message_id == message_id,
                        Response.kind == "full",
                    )
                )
            )
            .scalars()
            .all()
        )


def _captured(events: list[tuple]) -> list[tuple]:
    return [e for e in events if e[1] == "response.captured"]


# --- Recovery (the core promise) ---------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reconciler_recovers_a_dropped_reply(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """The live event never arrived; the reply IS in Telegram, addressed to
    our send. A pass recovers it: persisted + emitted, identical to live."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()  # send_log.message_id == 1, NO response yet
    our_id = await _sent_message_id(user.tenant_id)

    text = "✅ Aprobada CC: 4111 Status aprobada"
    reconciler_gateway.incoming = [(0, 1001, our_id, text)]

    fed = await reconciler.reconcile_once()
    assert fed == 1
    await _drain_capture_queue()

    fulls = await _full_rows(1001)
    assert len(fulls) == 1 and fulls[0].status == "ok"
    assert fulls[0].tenant_id == user.tenant_id
    captured = _captured(events)
    assert len(captured) == 1 and captured[0][2]["message_id"] == 1001


@pytest.mark.asyncio(loop_scope="session")
async def test_reconciler_ignores_replies_to_other_sends(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
) -> None:
    """Targeted scan: a chat message NOT replying to one of our awaiting sends
    is never fed (so attribution/unmatched is never even exercised)."""
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()
    # reply_to points at an id we never sent → outside the awaiting set.
    reconciler_gateway.incoming = [(0, 2002, 999_999, "✅ unrelated")]

    fed = await reconciler.reconcile_once()
    assert fed == 0
    assert capture._queue.empty()


# --- Idempotency -------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_reconciler_refeed_already_captured_is_a_noop(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
    events: list[tuple],
) -> None:
    """A reply captured live, then re-read by a pass, writes no duplicate row
    and emits nothing (process_incoming's text-equality dedup)."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()
    our_id = await _sent_message_id(user.tenant_id)

    text = "✅ CC: 4111 Status a"
    await capture.process_incoming(
        IncomingReply(message_id=1003, reply_to_msg_id=our_id, text=text, edited=False)
    )
    assert len(await _full_rows(1003)) == 1
    assert len(_captured(events)) == 1

    # The line is now answered → not awaiting → no Telegram call at all.
    reconciler_gateway.incoming = [(0, 1003, our_id, text)]
    fed = await reconciler.reconcile_once()
    assert fed == 0
    assert reconciler_gateway.recent_incoming_calls == 0
    await _drain_capture_queue()
    assert len(await _full_rows(1003)) == 1  # still exactly one revision
    assert len(_captured(events)) == 1  # no re-emit


# --- Cheap when idle ---------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_nothing_awaiting_makes_zero_telegram_calls(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
) -> None:
    """All delivered sends already answered → the pass returns after one DB
    query and never touches Telegram."""
    http, user = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()
    our_id = await _sent_message_id(user.tenant_id)
    await capture.process_incoming(
        IncomingReply(
            message_id=1004, reply_to_msg_id=our_id, text="✅ ok", edited=False
        )
    )

    fed = await reconciler.reconcile_once()
    assert fed == 0
    assert reconciler_gateway.recent_incoming_calls == 0


# --- Account-safety skips ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_skips_scan_when_gateway_not_ready(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()  # awaiting exists
    reconciler_gateway.authorized = False  # → ready is False

    fed = await reconciler.reconcile_once()
    assert fed == 0
    assert reconciler_gateway.recent_incoming_calls == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_skips_scan_when_watchdog_paused(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()
    monkeypatch.setattr(watchdog, "_paused", True)

    fed = await reconciler.reconcile_once()
    assert fed == 0
    assert reconciler_gateway.recent_incoming_calls == 0


@pytest.mark.asyncio(loop_scope="session")
async def test_skips_scan_during_floodwait_window(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()
    monkeypatch.setattr(scheduler, "flood_remaining", lambda: 5.0)

    fed = await reconciler.reconcile_once()
    assert fed == 0
    assert reconciler_gateway.recent_incoming_calls == 0


# --- Read failure is swallowed -----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_read_error_is_swallowed_and_pass_survives(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
) -> None:
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()
    reconciler_gateway.recent_incoming_error = RuntimeError("history read boom")

    fed = await reconciler.reconcile_once()  # must NOT raise
    assert fed == 0


# --- reconcile_enqueue does not fake liveness --------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_sends_beyond_window_are_counted_not_silently_dropped(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A delivered-unanswered send whose batch predates the scan window is not
    recovered, but it IS surfaced in the pass log (no silent cap)."""
    http, _ = client_user
    await _post_batch(http, "in-window", gate["id"])
    await _drain_worker()  # in-window awaiting send → keeps the pass working
    old_batch = await _post_batch(http, "old", gate["id"])
    await _drain_worker()  # delivered, unanswered …
    async with async_session_factory() as session:
        batch = await session.get(Batch, old_batch)
        assert batch is not None
        batch.created_at = datetime.now(UTC) - timedelta(
            hours=reconciler._RECONCILE_WINDOW_HOURS + 10
        )  # … then aged out of the window
        await session.commit()

    reconciler_gateway.incoming = []  # nothing to recover this pass
    with caplog.at_level("INFO"):
        fed = await reconciler.reconcile_once()
    assert fed == 0
    assert "beyond_window=1" in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_read_floodwait_feeds_the_scheduler_governor(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    reconciler_gateway: FakeGateway,
) -> None:
    """A FloodWait on the history read must open the SAME global no-send window
    the worker honors (protect the shared account), not be silently eaten."""
    http, _ = client_user
    await _post_batch(http, "uno", gate["id"])
    await _drain_worker()
    reconciler_gateway.recent_incoming_error = FloodWaitError(
        request=None, capture=7
    )

    assert scheduler.flood_remaining() == 0.0
    fed = await reconciler.reconcile_once()  # must NOT raise
    assert fed == 0
    assert scheduler.flood_remaining() > 0.0  # window opened


def test_reconcile_enqueue_does_not_feed_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reconciled reply is historical — it must NOT call watchdog.note_reply
    (unlike the live enqueue), so it never falsifies the reply-rate signal."""
    calls = {"n": 0}
    monkeypatch.setattr(watchdog, "note_reply", lambda: calls.__setitem__("n", calls["n"] + 1))

    reply = IncomingReply(message_id=1, reply_to_msg_id=1, text="x", edited=False)
    capture.reconcile_enqueue(reply)
    assert calls["n"] == 0
    assert capture._queue.get_nowait() is reply

    capture.enqueue(reply)  # the LIVE path DOES feed it (contrast)
    assert calls["n"] == 1
    capture._queue.get_nowait()


# --- 🔒 Per-chat message-id collision (the multi-target regression) -----------


@pytest.mark.asyncio(loop_scope="session")
async def test_same_message_id_in_two_chats_attributes_independently(
    client_user: tuple[AsyncClient, User],
    gate: dict,
) -> None:
    """🔒 Regression for the lost-replies incident: supergroup message ids are
    PER-CHAT, so two sends to two destinations can share an id. A bot reply in
    each chat (both quoting the colliding id) must attribute to ITS OWN line —
    keying on message_id alone collapsed both onto one line and left the other
    forever "awaiting", which read as ~58% of replies lost."""
    http, user = client_user
    await _post_batch(http, "uno\ndos", gate["id"])
    await _drain_worker()  # two sends; send_log ids 1 and 2 in fake chat 0

    # Force the collision the single-fake-chat worker can't produce: both
    # lines' sends now carry message_id 50, but in DIFFERENT chats (111 / 222).
    async with async_session_factory() as session:
        rows = list(
            (
                await session.execute(
                    select(SendLog)
                    .where(SendLog.tenant_id == user.tenant_id)
                    .order_by(SendLog.line_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 2
        rows[0].chat_id, rows[0].message_id = 111, 50
        rows[1].chat_id, rows[1].message_id = 222, 50
        line_a, line_b = rows[0].line_id, rows[1].line_id
        await session.commit()

    # One reply per chat, BOTH replying to the colliding id 50.
    await capture.process_incoming(
        IncomingReply(
            chat_id=111,
            message_id=900,
            reply_to_msg_id=50,
            text="✅ CC: 4111 Status a",
            edited=False,
        )
    )
    await capture.process_incoming(
        IncomingReply(
            chat_id=222,
            message_id=901,
            reply_to_msg_id=50,
            text="✅ CC: 5222 Status b",
            edited=False,
        )
    )

    a = await _full_rows(900)
    b = await _full_rows(901)
    assert len(a) == 1 and a[0].line_id == line_a
    assert len(b) == 1 and b[0].line_id == line_b
    assert line_a != line_b  # NOT collapsed onto one line
