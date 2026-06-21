"""Amazon "live" verdicts → Telegram channel forward (spec-amz-live-forward).

Covers the spec's I/O matrix:
- capture gate: a FRESH approved verdict forwards the VERBATIM redacted card
  EXACTLY ONCE; a re-edit of an already-live message never re-forwards;
- declined verdict and a non-cookie ✅ never forward;
- service: ``forward_live`` sends only when a channel is configured, parsing a
  numeric marked id; empty channel = no-op;
- ``gateway.send_to`` is best-effort — a client error / not-authorized returns
  False and never raises (so a forward failure can't break capture).

Reuses the cookie-mode harness from test_amazon_rotation (the real ASGI app
against dev Postgres + FakeGateway; capture goes direct to process_incoming).
"""

import uuid
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from app.core import capture, send_worker
from app.core.telegram import gateway
from app.db.base import async_session_factory
from app.db.repos import system_settings as system_settings_repo
from app.main import app
from app.services import live_forward
from httpx import ASGITransport, AsyncClient

from tests.conftest import cleanup_users, login, seed_user
from tests.test_amazon_rotation import (
    _APPROVED,
    _APPROVED_CARD,
    _DECLINED,
    _add_cookie,
    _amz_message_id,
    _drop_gate,
    _make_cookie_gate,
    _post_batch,
    _verdict_reply,
)


# Self-contained cookie-mode gate (mirrors test_amazon_rotation's fixtures so we
# don't import pytest fixtures by name — that trips ruff F811 on every use).
@pytest_asyncio.fixture(loop_scope="session")
async def owner_client() -> AsyncIterator[AsyncClient]:
    owner = await seed_user("owner", email_prefix="live")
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(http, owner.email)
    yield http
    await http.aclose()
    await cleanup_users({owner.email})


@pytest_asyncio.fixture(loop_scope="session")
async def cookie_gate(owner_client: AsyncClient) -> AsyncIterator[dict]:
    gate = await _make_cookie_gate(owner_client)
    yield gate
    await _drop_gate(gate["category_id"])


@pytest.fixture
def forwarded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every ``clean_text`` capture hands to ``forward_live``.

    Patches ``forward_live`` on the service module object (capture lazy-imports
    the same module), isolating the capture GATE from telegram entirely.
    """
    calls: list[str] = []

    async def fake_forward(text: str) -> None:
        calls.append(text)

    monkeypatch.setattr(live_forward, "forward_live", fake_forward)
    return calls


# --- Capture gate ------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_approved_live_forwards_verbatim_once(
    client_user, cookie_gate, fake_gateway, forwarded: list[str]
) -> None:
    """A fresh Approved verdict forwards the VERBATIM redacted card (no LIVE/DEAD
    rebrand) exactly once; a second identical Approved edit does NOT re-forward."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    await capture.process_incoming(_verdict_reply(20001, amz_id, _APPROVED))
    await send_worker._drain_verdicts()

    assert len(forwarded) == 1
    text = forwarded[0]
    assert _APPROVED_CARD in text  # the real card is present
    assert "Approved" in text  # verbatim verdict word (the original response)
    assert "LIVE 100%" not in text  # NOT the display_transform rebrand
    assert "Ranger Validation Engine" not in text

    # Re-edit of the now-live message → no second forward (once per live).
    await capture.process_incoming(_verdict_reply(20001, amz_id, _APPROVED))
    await send_worker._drain_verdicts()
    assert len(forwarded) == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_rebounce_approved_declined_approved_forwards_once(
    client_user, cookie_gate, fake_gateway, forwarded: list[str]
) -> None:
    """✅→❌→✅ on the SAME message forwards EXACTLY ONCE — the gate is "first ✅
    ever for this message" (has_ok_revision), NOT a status transition, so a
    re-approve after a decline does not re-forward (the documented re-bounce
    trap the credits charge also defends against)."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)
    mid = 21001  # one bot message, edited across the three verdicts

    await capture.process_incoming(_verdict_reply(mid, amz_id, _APPROVED))
    await send_worker._drain_verdicts()
    assert len(forwarded) == 1

    await capture.process_incoming(_verdict_reply(mid, amz_id, _DECLINED))
    await send_worker._drain_verdicts()
    await capture.process_incoming(_verdict_reply(mid, amz_id, _APPROVED))
    await send_worker._drain_verdicts()
    assert len(forwarded) == 1  # re-approve does NOT re-forward


@pytest.mark.asyncio(loop_scope="session")
async def test_declined_verdict_does_not_forward(
    client_user, cookie_gate, fake_gateway, forwarded: list[str]
) -> None:
    """A Declined verdict (not a live) is never forwarded."""
    http, _ = client_user
    await _add_cookie(http, cookie_gate["id"], f"ck-{uuid.uuid4().hex}")
    batch_id = await _post_batch(http, "4111111111111111", cookie_gate["id"])

    assert await send_worker.step() is True
    amz_id = await _amz_message_id(batch_id)

    await capture.process_incoming(_verdict_reply(20101, amz_id, _DECLINED))
    await send_worker._drain_verdicts()

    assert forwarded == []


@pytest.mark.asyncio(loop_scope="session")
async def test_non_cookie_ok_does_not_forward(
    client_user, gate, fake_gateway, forwarded: list[str]
) -> None:
    """A ✅ on a NON-cookie gate (cookie_mode False) is never forwarded — the
    forward is Amazon-cookie-mode only."""
    http, _ = client_user
    batch_id = await _post_batch(http, "plain-line", gate["id"])

    assert await send_worker.step() is True
    msg_id = await _amz_message_id(batch_id)

    await capture.process_incoming(_verdict_reply(20201, msg_id, "✅ aprobada"))
    await send_worker._drain_verdicts()

    assert forwarded == []


# --- forward_live service ----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_forward_live_sends_only_when_channel_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forward_live`` no-ops when no channel is configured and sends the
    verbatim text to the parsed numeric marked id when one is."""
    sent: list[tuple[int | str, str]] = []

    async def fake_send_to(identifier: int | str, text: str) -> bool:
        sent.append((identifier, text))
        return True

    monkeypatch.setattr(live_forward.gateway, "send_to", fake_send_to)

    async def _set(value: str) -> None:
        async with async_session_factory() as session:
            await system_settings_repo.set_value(
                session, live_forward.LIVE_FORWARD_KEY, value
            )
            await session.commit()

    try:
        await _set("")  # disabled
        await live_forward.forward_live("hello")
        assert sent == []

        await _set("-1001234567890")  # a marked channel id
        await live_forward.forward_live("hello")
        assert sent == [(-1001234567890, "hello")]  # int-parsed, verbatim
    finally:
        await _set("")  # don't leak the knob into other tests


def test_as_identifier_parses_safely() -> None:
    """A signed ASCII-int → int; an @username, multi-dash, or unicode digits →
    str (so a bad channel falls to resolve_one, never an int() crash → 500)."""
    assert live_forward.as_identifier("-1001234567890") == -1001234567890
    assert live_forward.as_identifier("@canal") == "@canal"
    assert live_forward.as_identifier("--5") == "--5"
    assert live_forward.as_identifier("5-") == "5-"
    assert live_forward.as_identifier("١٢٣") == "١٢٣"  # unicode digits → str


# --- admin endpoint ----------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_non_owner_cannot_set_channel(client_user) -> None:
    """AC6: a non-owner (client role) is forbidden from the owner-only knob."""
    http, _ = client_user
    res = await http.put(
        "/api/admin/live-channel", json={"live_forward_channel": ""}
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio(loop_scope="session")
async def test_owner_can_disable_channel(owner_client: AsyncClient) -> None:
    """Owner PUT empty → 200, persisted as disabled (no gateway needed)."""
    res = await owner_client.put(
        "/api/admin/live-channel", json={"live_forward_channel": ""}
    )
    assert res.status_code == 200, res.text
    assert res.json()["live_forward_channel"] == ""
    got = await owner_client.get("/api/admin/live-channel")
    assert got.status_code == 200
    assert got.json()["live_forward_channel"] == ""


# --- send_to best-effort -----------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_send_to_swallows_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A telethon error during the forward send is swallowed → returns False,
    never raises (capture must never see a forward failure)."""

    class Boom:
        async def send_message(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("flood / disconnect / whatever")

    monkeypatch.setattr(gateway, "client", Boom())
    monkeypatch.setattr(gateway, "authorized", True)

    assert await gateway.send_to(123, "x") is False


@pytest.mark.asyncio(loop_scope="session")
async def test_send_to_returns_false_when_not_authorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No authorized session → no send, returns False (no crash)."""
    monkeypatch.setattr(gateway, "authorized", False)
    assert await gateway.send_to(123, "x") is False
