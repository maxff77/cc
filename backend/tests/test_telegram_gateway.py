"""TelegramGateway bridge/registration unit tests (review 3-1).

No telethon network anywhere: ``_bridge`` takes any event-shaped object and
``_register_capture_handlers`` is exercised against a recording fake client.
Covers the boot-gap fix (events enqueue UNFILTERED while the target is
unresolved — a dropped catch_up replay would be lost forever because telethon
advances the persisted pts state) and the register-exactly-once guard.

Run (from backend/, venv active):  pytest tests/test_telegram_gateway.py
"""

from types import SimpleNamespace
from typing import Any

from app.core.capture import IncomingReply
from app.core.telegram import TelegramGateway


def _event(
    *,
    chat_id: int = 5,
    msg_id: int = 7,
    reply_to: int | None = 3,
    text: str = "✅ hola",
    out: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        out=out,
        chat_id=chat_id,
        raw_text=text,
        message=SimpleNamespace(id=msg_id, reply_to_msg_id=reply_to),
    )


def test_bridge_enqueues_during_boot_gap_then_filters_after_resolution() -> None:
    gateway = TelegramGateway()
    received: list[IncomingReply] = []
    gateway.register_capture(received.append)

    # Boot gap: _target_ids is empty — the event is enqueued, NOT dropped
    # (attribution via send_log is the real authority; review 3-1).
    gateway._bridge(_event(chat_id=5, msg_id=7), edited=False)
    assert [r.message_id for r in received] == [7]
    assert received[0].reply_to_msg_id == 3
    assert received[0].edited is False
    assert received[0].attempts == 0

    # Targets resolved (multi-target set): other chats are filtered out again …
    gateway._target_ids = {99, 42}
    gateway._bridge(_event(chat_id=5, msg_id=8), edited=False)
    assert len(received) == 1
    # … and ANY registered destination passes through (edits flagged).
    gateway._bridge(_event(chat_id=99, msg_id=9, reply_to=None), edited=True)
    gateway._bridge(_event(chat_id=42, msg_id=10, reply_to=None), edited=False)
    assert [r.message_id for r in received] == [7, 9, 10]
    assert received[1].edited is True
    assert received[1].reply_to_msg_id is None


def test_bridge_ignores_own_outgoing_even_in_boot_gap() -> None:
    gateway = TelegramGateway()
    received: list[IncomingReply] = []
    gateway.register_capture(received.append)
    gateway._bridge(_event(out=True), edited=False)
    assert received == []


class _RecordingClient:
    """Just enough of a TelegramClient to count handler registrations."""

    def __init__(self) -> None:
        self.handlers: list[tuple[Any, Any]] = []

    def add_event_handler(self, callback: Any, event: Any) -> None:
        self.handlers.append((callback, event))


def test_capture_handlers_register_exactly_once() -> None:
    """The _handlers_registered guard (review 3-1): a re-called registration
    (public connect() may run again) must not double-register — duplicates
    would double-enqueue every event."""
    gateway = TelegramGateway()
    gateway.register_capture(lambda reply: None)
    gateway.client = _RecordingClient()  # type: ignore[assignment]

    gateway._register_capture_handlers()
    gateway._register_capture_handlers()

    assert len(gateway.client.handlers) == 2  # one NewMessage + one MessageEdited
    assert gateway._handlers_registered is True


def test_capture_handlers_skip_without_callback_then_register_later() -> None:
    """Returning early without a callback must NOT set the flag — a later
    call (capture installed) still registers."""
    gateway = TelegramGateway()
    gateway.client = _RecordingClient()  # type: ignore[assignment]

    gateway._register_capture_handlers()  # no callback installed yet
    assert gateway.client.handlers == []
    assert gateway._handlers_registered is False

    gateway.register_capture(lambda reply: None)
    gateway._register_capture_handlers()
    assert len(gateway.client.handlers) == 2
