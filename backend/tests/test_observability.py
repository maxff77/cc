"""Story 4.3 observability tests: the FloodWait alert threshold (AC 1), the
unmatched-replies growth alert (AC 3), the structured-log queryability of the
guardrail counters (AC 2), and the owner-only ``GET /api/observability``
surface (AC 2).

Conftest idiom: real ASGI app + dev Postgres, self-seeding/self-cleaning,
``FakeGateway`` + ``step()`` directly, events verified by monkeypatching the
broadcaster with a recorder list (2.2 lesson). Window arithmetic runs on
fresh ``SlidingAlert(now=FakeClock())`` instances (idiom: Scheduler/Watchdog
unit tests). ``_sent_by_tenant`` is process-lifetime by design — endpoint
asserts go against the test's FRESH tenant, never against global totals.

Run (from backend/, venv active):  pytest tests/test_observability.py
"""

import logging
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.core import alerts, capture, send_worker
from app.core.alerts import KIND_FLOOD_WAIT, KIND_UNMATCHED_REPLIES, SlidingAlert
from app.core.broadcaster import broadcaster
from app.core.capture import IncomingReply
from app.core.scheduler import Scheduler, scheduler
from app.core.watchdog import REASON_SESSION_LOST, watchdog
from app.db.base import async_session_factory
from app.db.models import SystemSetting, User
from app.main import app
from app.services import admission as admission_service
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from telethon.errors import FloodWaitError

from tests.conftest import FakeGateway, cleanup_users, login, seed_user

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


def _alerts_of(events: list[tuple], kind: str) -> list[dict]:
    return [
        data
        for _tenant, name, data in events
        if name == "guardrail.alert" and data["kind"] == kind
    ]


class FakeClock:
    """Deterministic stand-in for ``time.monotonic`` (window unit tests)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def _msg_id() -> int:
    """Collision-free message id against the shared dev Postgres."""
    return secrets.randbelow(2**40) + 2**41


def _unmatched_reply(*, attempts: int) -> IncomingReply:
    """A bot message nothing can attribute (no reply_to, unknown id)."""
    return IncomingReply(
        message_id=_msg_id(),
        reply_to_msg_id=None,
        text="✅ Aprobada",
        edited=False,
        attempts=attempts,
    )


async def _noop_persist() -> None:
    """Quiet ``watchdog._persist`` (the row is owned by 4.1's tests)."""
    return None


# --- SlidingAlert window/latch units (AC 1, AC 3) ------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_alert_fires_when_threshold_crossed(
    events: list[tuple], caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock()
    alert = SlidingAlert(KIND_FLOOD_WAIT, 3, 600.0, now=clock)

    await alert.note("detail")
    clock.advance(100.0)
    await alert.note("detail")
    assert _alerts_of(events, KIND_FLOOD_WAIT) == []  # below threshold
    assert not alert.is_alerting()

    clock.advance(100.0)
    with caplog.at_level(logging.WARNING, logger="app.core.alerts"):
        await alert.note("detail")
    fired = _alerts_of(events, KIND_FLOOD_WAIT)
    assert len(fired) == 1
    assert fired[0]["count"] == 3
    assert fired[0]["window_seconds"] == 600.0
    assert fired[0]["at"] is not None
    assert alert.is_alerting()
    assert "event=guardrail_alert kind=flood_wait count=3" in caplog.text


@pytest.mark.asyncio(loop_scope="session")
async def test_sustained_saturation_never_spams(events: list[tuple]) -> None:
    clock = FakeClock()
    alert = SlidingAlert(KIND_FLOOD_WAIT, 3, 600.0, now=clock)
    for _ in range(3):
        await alert.note("detail")
    # The window stays saturated: more events, still exactly ONE alert.
    for _ in range(5):
        clock.advance(10.0)
        await alert.note("detail")
    assert len(_alerts_of(events, KIND_FLOOD_WAIT)) == 1
    assert alert.is_alerting()


@pytest.mark.asyncio(loop_scope="session")
async def test_alert_rearms_once_the_window_drains(events: list[tuple]) -> None:
    clock = FakeClock()
    alert = SlidingAlert(KIND_FLOOD_WAIT, 3, 600.0, now=clock)
    for _ in range(3):
        await alert.note("detail")
    assert len(_alerts_of(events, KIND_FLOOD_WAIT)) == 1

    # Everything slides out: the latch re-arms (a read reports it honestly)…
    clock.advance(601.0)
    assert alert.count_in_window() == 0
    assert not alert.is_alerting()

    # …and a fresh saturation fires a SECOND alert.
    for _ in range(3):
        await alert.note("detail")
    assert len(_alerts_of(events, KIND_FLOOD_WAIT)) == 2


# --- Governor counters (AC 1, AC 2) ---------------------------------------------


def test_governor_counts_events_and_raises_until_the_ceiling() -> None:
    sched = Scheduler()
    assert sched.flood_events_total == 0
    assert sched.governor_raises == 0
    # 3.0 ×1.5 per event: six raises reach the 30s ceiling…
    for _ in range(6):
        sched.note_flood_wait(0.0)
    assert sched.g_min == 30.0
    assert sched.flood_events_total == 6
    assert sched.governor_raises == 6
    # …the seventh FloodWait is still an EVENT but no longer a raise.
    sched.note_flood_wait(0.0)
    assert sched.flood_events_total == 7
    assert sched.governor_raises == 6
    sched.reset()
    assert sched.flood_events_total == 0


# --- Worker wiring: FloodWaits feed counters, logs and the alert (AC 1, AC 2) ---


@pytest.mark.asyncio(loop_scope="session")
async def test_worker_floodwaits_feed_counters_logs_and_alert(
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Three real FloodWaits on one line: governor counters move, the
    flood_wait log carries the queryable totals, flood.wait events stay
    per-event, and the threshold (3 in window) alerts the owner ONCE."""
    http, user = client_user
    res = await http.post("/api/batches", json={"text": "uno", "gate_id": gate["id"]})
    assert res.status_code == 201, res.text

    fake_gateway.errors = [
        FloodWaitError(request=None, capture=0),
        FloodWaitError(request=None, capture=0),
        FloodWaitError(request=None, capture=0),
    ]
    with caplog.at_level(logging.INFO, logger="app.core.send_worker"):
        assert await send_worker.step() is True  # retried through 3 floods, sent
    assert len(fake_gateway.sent) == 1  # gate-prefixed text of the one line
    assert fake_gateway.sent[0].endswith("uno")

    assert scheduler.flood_events_total == 3
    assert scheduler.governor_raises == 3
    assert scheduler.g_min == pytest.approx(10.125)  # 3.0 ×1.5³

    flood_events = [e for e in events if e[1] == "flood.wait"]
    assert len(flood_events) == 3
    fired = _alerts_of(events, KIND_FLOOD_WAIT)
    assert len(fired) == 1
    assert fired[0]["count"] == 3

    # AC 2 — the structured logs answer every guardrail question:
    assert "flood_total=3" in caplog.text
    assert "raises_total=3" in caplog.text
    assert "event=line_sent" in caplog.text
    assert f"tenant={user.tenant_id}" in caplog.text
    assert "tenant_total=" in caplog.text


# --- Capture wiring: the unmatched bucket alert (AC 2, AC 3) ---------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_unmatched_growth_alerts_once_at_threshold(
    events: list[tuple], caplog: pytest.LogCaptureFixture
) -> None:
    """Five FINAL unmatched replies saturate the window: bucket at 5, one
    single ``guardrail.alert`` (kind unmatched_replies), greppable logs."""
    with caplog.at_level(logging.WARNING, logger="app.core.capture"):
        for _ in range(5):
            await capture.process_incoming(_unmatched_reply(attempts=2))
    assert capture.unmatched_total() == 5
    fired = _alerts_of(events, KIND_UNMATCHED_REPLIES)
    assert len(fired) == 1
    assert fired[0]["count"] == 5
    assert "event=unmatched_reply" in caplog.text  # AC 2


@pytest.mark.asyncio(loop_scope="session")
async def test_attribution_retries_never_feed_the_alert(
    events: list[tuple],
) -> None:
    """A first-attempt unmatched reply re-enqueues (the send→record race):
    neither the bucket nor the alert window may count it."""
    await capture.process_incoming(_unmatched_reply(attempts=0))
    assert capture.unmatched_total() == 0
    assert alerts.unmatched_alert.count_in_window() == 0
    assert _alerts_of(events, KIND_UNMATCHED_REPLIES) == []


# --- GET /api/observability (AC 2) -----------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_observability_endpoint_owner_only(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
) -> None:
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    admin_client: AsyncClient = ctx["admin_client"]  # type: ignore[assignment]
    client_http, _user = client_user

    res = await admin_client.get("/api/observability")
    assert res.status_code == 403 and res.json()["code"] == "forbidden"
    res = await client_http.get("/api/observability")
    assert res.status_code == 403 and res.json()["code"] == "forbidden"

    res = await owner_client.get("/api/observability")
    assert res.status_code == 200


@pytest.mark.asyncio(loop_scope="session")
async def test_observability_reports_every_slice(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    fake_gateway: FakeGateway,
    events: list[tuple],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One delivered line, two FloodWaits, one final unmatched reply and a
    latched watchdog — the GET reports each counter where the logs put it."""
    monkeypatch.setattr(watchdog, "_persist", _noop_persist)
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    http, user = client_user

    res = await http.post("/api/batches", json={"text": "uno", "gate_id": gate["id"]})
    assert res.status_code == 201, res.text
    assert await send_worker.step() is True  # BEFORE the latch below

    # Mirror the worker's FloodWait wiring: governor counters AND alert window.
    scheduler.note_flood_wait(0.0)
    await alerts.note_flood_wait()
    scheduler.note_flood_wait(0.0)
    await alerts.note_flood_wait()
    await capture.process_incoming(_unmatched_reply(attempts=2))
    await watchdog.session_lost("AuthKeyUnregisteredError: dead")

    res = await owner_client.get("/api/observability")
    assert res.status_code == 200
    body = res.json()

    # Per-tenant sends: the FRESH tenant has exactly its one delivery
    # (the global counter is process-lifetime — never asserted exactly).
    assert body["sent_by_tenant"][str(user.tenant_id)] == 1
    assert body["sent_total"] >= 1

    assert body["flood"]["events_total"] == 2
    assert body["flood"]["governor_raises"] == 2
    assert body["flood"]["g_min"] == pytest.approx(6.75)  # 3.0 ×1.5²
    assert body["flood"]["events_in_window"] == 2
    assert body["flood"]["alert_active"] is False  # below the threshold of 3

    assert body["unmatched"]["total"] == 1
    assert body["unmatched"]["events_in_window"] == 1
    assert body["unmatched"]["alert_active"] is False

    assert body["watchdog"]["paused"] is True
    assert body["watchdog"]["reason"] == REASON_SESSION_LOST
    assert body["watchdog"]["paused_at"] is not None


# --- Admission queue depth (AC 2) -------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def clean_cap() -> AsyncIterator[None]:
    """Wipe the admission cap around the test (global knob, shared DB)."""
    async with async_session_factory() as session:
        await session.execute(
            delete(SystemSetting).where(SystemSetting.key == admission_service.CAP_KEY)
        )
        await session.commit()
    yield
    async with async_session_factory() as session:
        await session.execute(
            delete(SystemSetting).where(SystemSetting.key == admission_service.CAP_KEY)
        )
        await session.commit()


@pytest.mark.asyncio(loop_scope="session")
async def test_observability_reports_admission_depth(
    ctx: dict[str, object],
    client_user: tuple[AsyncClient, User],
    gate: dict,
    clean_cap: None,
) -> None:
    """cap=1, one admitted sender + one queued: the GET reports the knob,
    the occupied slots and the FIFO depth."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    http_a, _user_a = client_user

    res = await owner_client.put(
        "/api/admin/admission", json={"max_active_senders": 1}
    )
    assert res.status_code == 200, res.text

    res = await http_a.post(
        "/api/batches", json={"text": "uno", "gate_id": gate["id"]}
    )
    assert res.status_code == 201 and res.json()["state"] == "sending"

    user_b = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http_b = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(http_b, user_b.email)
        res = await http_b.post(
            "/api/batches", json={"text": "dos", "gate_id": gate["id"]}
        )
        assert res.status_code == 201 and res.json()["state"] == "waiting"

        res = await owner_client.get("/api/observability")
        assert res.status_code == 200
        admission = res.json()["admission"]
        assert admission["max_active_senders"] == 1
        assert admission["admitted"] == 1
        assert admission["waiting"] == 1
    finally:
        await http_b.aclose()
        await cleanup_users({user_b.email})
