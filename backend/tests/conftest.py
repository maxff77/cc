"""Pytest fixtures + shared helpers for the backend suite.

The suite drives the real ASGI app (httpx ``ASGITransport``) against the dev
Postgres: self-seeding with unique emails, direct DB mutation for state setup,
self-cleaning on teardown. The seed/login/cleanup helpers live HERE so every
test module shares one copy of the tenant+user schema wiring — a model or repo
signature change is fixed in one place.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from app.core import alerts, capture, cookie_verdict, send_worker
from app.core.scheduler import scheduler
from app.core.telegram import gateway
from app.core.watchdog import watchdog
from app.db.base import async_session_factory
from app.db.models import (
    Batch,
    BatchLine,
    CaptureSession,
    Gate,
    GateCategory,
    Response,
    SendLog,
    Tenant,
    User,
)
from app.db.repos import users as users_repo
from app.main import app
from app.services.auth import hash_password
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

PASSWORD = "seed-pass-123"  # noqa: S105 — throwaway test credential


class FakeGateway:
    """In-memory stand-in for ``app.core.telegram.gateway`` (Story 2.2).

    The architecture's "fake Telegram client fixture" starts here — Stories
    2.3/2.4/2.5 reuse it. Records sent texts, returns incrementing message
    ids, and is programmable to raise: push exceptions onto ``errors`` and
    each ``send`` pops+raises one before succeeding (e.g. a
    ``FloodWaitError(request=None, capture=0)`` once, then success).

    ``send`` returns ``(chat_id, message_id)``; ``send_chat_id`` (default 0 —
    the single-id-space sentinel) is the chat every send is attributed to, so
    a test exercising per-chat collisions can flip it between sends.

    ``recent_outgoing`` (Story 2.5 boot reconciliation) returns the programmable
    ``outgoing`` list of ``(chat_id, message_id, text)`` newest-first, or raises
    ``recent_outgoing_error`` when one is set.

    ``recent_incoming`` (reply reconciler) returns the programmable ``incoming``
    list of ``(chat_id, message_id, reply_to_msg_id, text)`` filtered to chats
    present in ``floors`` with ``message_id >= floors[chat_id]``, or raises
    ``recent_incoming_error`` when set; ``recent_incoming_calls`` counts
    invocations (a pass with nothing awaiting must NOT call it).
    """

    def __init__(self) -> None:
        self.authorized = True
        self.target_ok = True
        self.sent: list[str] = []
        self.errors: list[Exception] = []
        self.send_chat_id = 0
        self.outgoing: list[tuple[int, int, str]] = []
        self.recent_outgoing_error: Exception | None = None
        self.incoming: list[tuple[int, int, int | None, str]] = []
        self.recent_incoming_error: Exception | None = None
        self.recent_incoming_calls = 0
        self._next_id = 0

    @property
    def ready(self) -> bool:
        return self.authorized and self.target_ok

    async def send(self, text: str) -> tuple[int, int]:
        if self.errors:
            raise self.errors.pop(0)
        self.sent.append(text)
        self._next_id += 1
        return (self.send_chat_id, self._next_id)

    async def recent_outgoing(self, limit: int = 50) -> list[tuple[int, int, str]]:
        if self.recent_outgoing_error is not None:
            raise self.recent_outgoing_error
        return list(self.outgoing[:limit])

    async def recent_incoming(
        self, floors: dict[int, int], limit: int
    ) -> list[tuple[int, int, int | None, str]]:
        self.recent_incoming_calls += 1
        if self.recent_incoming_error is not None:
            raise self.recent_incoming_error
        return [
            m
            for m in self.incoming
            if m[0] in floors and m[1] >= floors[m[0]]
        ][:limit]


def unique_email(role: str, *, prefix: str = "test") -> str:
    """Collision-free throwaway address, prefixed per test module."""
    return f"{prefix}-{role}-{uuid.uuid4().hex[:8]}@cc.test"


async def seed_user(
    role: str,
    *,
    expires_at: datetime | None = None,
    email_prefix: str = "test",
) -> User:
    """Create a fresh user (own tenant) directly, bypassing the API."""
    async with async_session_factory() as session:
        tenant = await users_repo.create_tenant(session, name=f"t-{uuid.uuid4().hex}")
        user = await users_repo.create_user(
            session,
            tenant_id=tenant.id,
            email=unique_email(role, prefix=email_prefix),
            password_hash=hash_password(PASSWORD),
            role=role,
            expires_at=expires_at,
        )
        await session.commit()
        return user


async def login(client: AsyncClient, email: str) -> None:
    """Log ``email`` in with the seeded password; asserts success."""
    res = await client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert res.status_code == 200, res.text


@pytest_asyncio.fixture(loop_scope="session")
async def ctx() -> AsyncIterator[dict[str, object]]:
    """Seed an owner + an admin, log each in, and clean up afterwards.

    Shared by the admin API modules (test_admin_users, test_admin_lifecycle).
    Tests add the per-test emails they seed to ``ctx["created"]`` so the
    teardown removes them too.
    """
    created: set[str] = set()
    owner = await seed_user("owner")
    admin = await seed_user("admin")
    created.update({owner.email, admin.email})

    transport = ASGITransport(app=app)
    owner_client = AsyncClient(transport=transport, base_url="http://test")
    admin_client = AsyncClient(transport=transport, base_url="http://test")
    await login(owner_client, owner.email)
    await login(admin_client, admin.email)

    yield {
        "owner_client": owner_client,
        "admin_client": admin_client,
        "owner": owner,
        "admin": admin,
        "created": created,
    }

    await owner_client.aclose()
    await admin_client.aclose()
    await cleanup_users(created)


async def cleanup_users(emails: set[str]) -> None:
    """Delete every user created during the test, plus its tenant."""
    async with async_session_factory() as session:
        for email in emails:
            user = await users_repo.get_by_email(session, email)
            if user is None:
                continue
            tenant = await session.get(Tenant, user.tenant_id)
            await session.delete(user)
            if tenant is not None:
                await session.delete(tenant)
        await session.commit()


# --- Batch fixtures (promoted from test_batches.py for Story 2.3) -----------


@pytest.fixture(autouse=True)
def reset_scheduler() -> Iterator[None]:
    """Wipe the scheduler singleton's cursor/governor around every test.

    The Story 2.4 scheduler is process memory by design — without this, a
    FloodWait raised in one test would leave ``g_min`` raised (changing ETA
    math) and the rotation cursor would leak across modules.
    """
    scheduler.reset()
    yield
    scheduler.reset()


@pytest.fixture(autouse=True)
def reset_capture() -> Iterator[None]:
    """Wipe the capture pipeline's module state around every test.

    ``_queue``/``_unmatched_total`` are process memory by design (Story 3.1)
    — without this, an unconsumed reply or a bumped unmatched counter would
    leak across modules (the same trap as the 2.4 governor).
    """
    capture.reset()
    yield
    capture.reset()


@pytest.fixture(autouse=True)
def reset_send_worker() -> Iterator[None]:
    """Wipe the send worker's process-memory singletons around every test.

    The conftest predates cookie-mode (Phase 2), so its siblings
    (``reset_scheduler``/``reset_capture``) never covered the send worker's own
    globals: the per-tenant sent counter (``_sent_by_tenant``) and the
    capture→worker verdict-signal queue (``cookie_verdict._queue``). Without this
    they leak across the session-scoped run — a verdict one module signals, or a
    bumped sent count, bleeds into LATER tests (the isolation flake; the same
    trap as the 2.4 governor). The verdict-timeout retry budget is DURABLE on
    ``BatchLine`` now, so there is no process-memory retry set left to reset.
    """
    send_worker._sent_by_tenant.clear()
    cookie_verdict.reset()
    yield
    send_worker._sent_by_tenant.clear()
    cookie_verdict.reset()


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def clean_send_capture_domain() -> AsyncIterator[None]:
    """Give every test a clean send/capture slate (DB-isolation).

    The DB-level sibling of ``reset_send_worker``. The send-worker queries are
    GLOBAL by design — ``count_active_senders`` spans every tenant, and reply
    attribution keys on ``(chat_id, message_id)``, which the ``FakeGateway``
    restarts at 1 each test. A test that posts a batch via a session-scoped
    tenant (owner/admin) and never stops/drains it, or whose ``send_log`` rows
    outlive it, leaks into a LATER test's global count or attribution (the
    isolation flake — e.g. ``test_paused_tenant_is_excluded_then_rejoins`` /
    ``test_delete_guarded_by_live_batch_then_cascades_clean``). Wipe AFTER each
    test (post-body, so it never deletes the running test's own rows); every
    test has this teardown, so the NEXT test starts clean. Tenant-scoped
    fixtures (users/gates) survive — only the per-test send/capture rows are
    deleted, child→parent so it is FK-safe.

    Best-effort: the suite shares ONE session-scoped event loop + connection
    pool, so a pooled connection can occasionally carry a prior test's broken
    transaction; swallow any cleanup error rather than turn an unrelated test
    into an ERROR (the next wipe catches up).
    """
    yield
    try:
        async with async_session_factory() as session:
            for model in (Response, SendLog, BatchLine, Batch, CaptureSession):
                await session.execute(delete(model))
            await session.commit()
    except Exception:  # noqa: BLE001 — isolation cleanup is best-effort
        pass


@pytest.fixture(autouse=True)
def reset_watchdog() -> Iterator[None]:
    """Wipe the watchdog singleton's window + latch around every test.

    Story 4.1 state is process memory by design — a latched pause or a
    half-full send window leaking across tests would silently block every
    later ``step()`` (the same trap as the 2.4 governor). Memory only — the
    DB row is owned by the explicit persistence tests.
    """
    watchdog.reset()
    yield
    watchdog.reset()


@pytest.fixture(autouse=True)
def reset_alerts() -> Iterator[None]:
    """Wipe the guardrail alert windows around every test.

    Story 4.3 state is process memory by design — without this, FloodWaits
    raised in one test would saturate the alert window (or leave its latch
    set) for every later module (the same trap as the 2.4 governor).
    """
    alerts.reset()
    yield
    alerts.reset()


@pytest.fixture(autouse=True)
def authorized_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """The batches routes gate on the real singleton's flags — flip them on.

    ASGITransport never runs the lifespan, so no Telethon client exists; the
    endpoints only persist rows. Individual tests re-flip ``authorized``
    to exercise the 503.
    """
    monkeypatch.setattr(gateway, "authorized", True)
    monkeypatch.setattr(gateway, "target_ok", True)


@pytest.fixture
def fake_gateway(monkeypatch: pytest.MonkeyPatch) -> FakeGateway:
    """Swap the send worker's gateway for the in-memory fake."""
    fake = FakeGateway()
    monkeypatch.setattr(send_worker, "gateway", fake)
    return fake


@pytest_asyncio.fixture(loop_scope="session")
async def gate(ctx: dict[str, object]) -> AsyncIterator[dict]:
    """An active gate in its own category, created via the owner API."""
    owner_client: AsyncClient = ctx["owner_client"]  # type: ignore[assignment]
    cat = await owner_client.post(
        "/api/admin/gate-categories", json={"name": f"Lote {uuid.uuid4().hex[:8]}"}
    )
    assert cat.status_code == 201, cat.text
    value = f".b{uuid.uuid4().hex[:6]}"
    res = await owner_client.post(
        "/api/admin/gates",
        json={
            "value": value,
            "name": "Visa Lote",
            "display_value": "Comando Lote",
            "category_id": cat.json()["id"],
        },
    )
    assert res.status_code == 201, res.text
    yield res.json()
    async with async_session_factory() as session:
        await session.execute(
            delete(Gate).where(Gate.category_id == cat.json()["id"])
        )
        await session.execute(
            delete(GateCategory).where(GateCategory.id == cat.json()["id"])
        )
        await session.commit()


@pytest_asyncio.fixture(loop_scope="session")
async def client_user() -> AsyncIterator[tuple[AsyncClient, User]]:
    """A logged-in client (valid plan) + its user row; self-cleaning.

    Tenant deletion in cleanup cascades over batches/batch_lines (FK CASCADE),
    so batches created during the test die with it.
    """
    user = await seed_user(
        "client", expires_at=datetime.now(UTC) + timedelta(days=30)
    )
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, user.email)
    yield http, user
    await http.aclose()
    await cleanup_users({user.email})
