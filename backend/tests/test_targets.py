"""Multi-target sending: gateway round-robin/filter/reload + repo + boot seed.

No telethon network: a recording fake client stands in for the Telethon client
(``_FakeClient``). The repo/seed tests hit the dev Postgres directly (self-
cleaning) like the rest of the suite.

Run (from backend/, venv active):  pytest tests/test_targets.py
"""

from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest
import pytest_asyncio
from app.config import settings
from app.core.capture import IncomingReply
from app.core.telegram import TelegramGateway, gateway
from app.db.base import async_session_factory
from app.db.models import SendTarget
from app.db.repos import targets as targets_repo
from app.services import targets as targets_service
from sqlalchemy import delete

_CHAT_A = -1001234567890  # supergroup id (overflows int4 — exercises BigInteger)


class _FakeClient:
    """Minimal Telethon stand-in: resolves ids by value, records sends, can fail
    specific ids. ``get_input_entity`` returns a stable, value-hashable entity so
    history can be keyed by it."""

    def __init__(
        self,
        *,
        fail: set[int] | None = None,
        cold: set[int] | None = None,
        history: dict | None = None,
    ) -> None:
        self.sent: list[tuple[object, str]] = []
        self._fail = fail or set()  # never resolve (account left the chat)
        self._cold = cold or set()  # resolve only AFTER get_dialogs warms cache
        self._history = history or {}  # entity -> list[(id, raw_text, out)]
        self._warmed = False
        self.dialogs_calls = 0
        self._mid = 1000

    async def get_input_entity(self, ident: object) -> tuple[str, object]:
        if ident in self._fail:
            raise ValueError(f"no entity for {ident}")
        if ident in self._cold and not self._warmed:
            raise ValueError(f"cold cache for {ident}")
        return ("e", ident)

    async def get_dialogs(self, limit: int = 0) -> list[object]:
        self.dialogs_calls += 1
        self._warmed = True  # access_hash now cached → cold ids resolve
        return []

    async def get_peer_id(self, entity: tuple[str, int]) -> int:
        return entity[1]

    async def send_message(
        self, entity: object, text: str, parse_mode: object = None
    ) -> SimpleNamespace:
        assert parse_mode is None  # load-bearing (byte-for-byte delivery)
        self.sent.append((entity, text))
        self._mid += 1
        return SimpleNamespace(id=self._mid)

    async def iter_messages(
        self, entity: object, limit: int = 0
    ) -> AsyncIterator[SimpleNamespace]:
        for mid, raw, out in self._history.get(entity, []):
            yield SimpleNamespace(id=mid, raw_text=raw, out=out)


def _gw(client: _FakeClient) -> TelegramGateway:
    gw = TelegramGateway()
    gw.client = client  # type: ignore[assignment]
    gw.authorized = True
    return gw


# --- gateway: round-robin / reload / capture filter / recent_outgoing --------


@pytest.mark.asyncio(loop_scope="session")
async def test_round_robin_distributes_across_targets() -> None:
    client = _FakeClient()
    gw = _gw(client)
    report = await gw.reload_targets([(1001, "a"), (1002, "b"), (1003, "c")])
    assert report == {"resolved": [1001, 1002, 1003], "failed": []}
    assert gw.ready is True
    for i in range(7):
        await gw.send(f"line-{i}")
    used = [entity[1] for entity, _ in client.sent]
    assert used == [1001, 1002, 1003, 1001, 1002, 1003, 1001]


@pytest.mark.asyncio(loop_scope="session")
async def test_reload_skips_unresolvable_and_tracks_status() -> None:
    gw = _gw(_FakeClient(fail={9999}))
    report = await gw.reload_targets([(1001, "a"), (9999, "bad"), (1002, "b")])
    assert report == {"resolved": [1001, 1002], "failed": [9999]}
    assert gw.target_ok is True
    assert gw.resolved_ids() == {1001, 1002}

    # Every target fails → not ready, target_ok False (sending would 503).
    report2 = await gw.reload_targets([(9999, "bad")])
    assert report2 == {"resolved": [], "failed": [9999]}
    assert gw.target_ok is False
    assert gw.ready is False


@pytest.mark.asyncio(loop_scope="session")
async def test_reload_warms_cache_for_cold_numeric_id() -> None:
    # A bare supergroup id can't resolve on a cold session — reload_targets must
    # warm the entity cache once (get_dialogs) and retry, or it 503s every boot.
    client = _FakeClient(cold={-1001234567890})
    gw = _gw(client)
    report = await gw.reload_targets([(-1001234567890, "CC1")])
    assert report == {"resolved": [-1001234567890], "failed": []}
    assert gw.resolved_ids() == {-1001234567890}
    assert client.dialogs_calls == 1  # warmed exactly once


@pytest.mark.asyncio(loop_scope="session")
async def test_reload_when_unauthorized_clears_targets() -> None:
    gw = _gw(_FakeClient())
    gw.authorized = False
    report = await gw.reload_targets([(1001, "a")])
    assert report == {"resolved": [], "failed": [1001]}
    assert gw.ready is False


def test_bridge_filters_on_target_id_set() -> None:
    gw = TelegramGateway()
    received: list[IncomingReply] = []
    gw.register_capture(received.append)
    gw._target_ids = {10, 20}

    def _ev(chat_id: int, mid: int) -> SimpleNamespace:
        return SimpleNamespace(
            out=False,
            chat_id=chat_id,
            raw_text="✅",
            message=SimpleNamespace(id=mid, reply_to_msg_id=5),
        )

    gw._bridge(_ev(10, 1), edited=False)  # registered → through
    gw._bridge(_ev(99, 2), edited=False)  # other chat → dropped
    gw._bridge(_ev(20, 3), edited=False)  # registered → through
    assert [r.message_id for r in received] == [1, 3]


@pytest.mark.asyncio(loop_scope="session")
async def test_recent_outgoing_aggregates_across_targets() -> None:
    history = {
        ("e", 1): [(105, "x", True), (104, "incoming", False)],
        ("e", 2): [(106, "y", True)],
    }
    gw = _gw(_FakeClient(history=history))
    await gw.reload_targets([(1, "a"), (2, "b")])
    out = await gw.recent_outgoing(limit=10)
    # outgoing-only, deduped by account-global id, newest-first.
    assert out == [(106, "y"), (105, "x")]


# --- repo + boot seed (DB) ---------------------------------------------------


@pytest_asyncio.fixture(loop_scope="session")
async def clean_targets() -> AsyncIterator[None]:
    async def _wipe() -> None:
        async with async_session_factory() as session:
            await session.execute(delete(SendTarget))
            await session.commit()

    await _wipe()
    yield
    await _wipe()


@pytest.mark.asyncio(loop_scope="session")
async def test_repo_crud_roundtrip(clean_targets: None) -> None:
    async with async_session_factory() as session:
        created = await targets_repo.create(session, chat_id=_CHAT_A, label="CC1")
        await session.commit()
        target_id = created.id

    async with async_session_factory() as session:
        assert await targets_repo.count(session) == 1
        got = await targets_repo.get_by_chat_id(session, _CHAT_A)
        assert got is not None and got.id == target_id
        assert [t.chat_id for t in await targets_repo.list_enabled(session)] == [_CHAT_A]
        got.enabled = False
        await session.commit()

    async with async_session_factory() as session:
        assert await targets_repo.list_enabled(session) == []
        target = await targets_repo.get_by_id(session, target_id)
        assert target is not None
        await targets_repo.delete(session, target)
        await session.commit()
        assert await targets_repo.get_by_chat_id(session, _CHAT_A) is None


@pytest.mark.asyncio(loop_scope="session")
async def test_ensure_seeded_from_env(
    clean_targets: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "telegram_target", "@thebot")

    async def fake_resolve(identifier: object) -> int:
        assert identifier == "thebot"  # the @ is stripped before resolving
        return -1009999

    monkeypatch.setattr(gateway, "resolve_one", fake_resolve)

    async with async_session_factory() as session:
        await targets_service.ensure_seeded(session)

    async with async_session_factory() as session:
        seeded = await targets_repo.get_by_chat_id(session, -1009999)
        assert seeded is not None and seeded.label == "thebot"
        # Idempotent: a second call with rows present is a no-op.
        await targets_service.ensure_seeded(session)
        assert await targets_repo.count(session) == 1
