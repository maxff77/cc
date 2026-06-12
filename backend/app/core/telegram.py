"""Telethon gateway — the ONLY module importing telethon anywhere in ``app/``.

Architecture boundary (enforced by review): one process (cc-core/uvicorn) owns
``anon.session``, and inside it only this module talks MTProto. Everything
else goes through the ``gateway`` singleton.

The app must BOOT without Telegram: missing/zero credentials, a missing or
unauthorized session file, or an unresolvable target leave ``authorized``/
``target_ok`` False — login/admin keep working, the worker idles and
``POST /api/batches`` answers 503 ``telegram_unauthorized``. Re-auth is
operational (run ``scripts/telegram_auth.py`` on the VPS).

Session-loss detection (Story 4.1): an ``AuthKeyError``/deauthorization
surfacing on the HOT path (``send``/``recent_outgoing``) flips
``authorized=False`` and crosses the boundary as the domain
``SessionLostError`` — the worker releases its claimed line and latches the
watchdog's global pause. Boot-time unauthorized is deliberately NOT a
watchdog trigger (recorded decision): it is not a silent failure (warning +
503 on every new send since 2.2) and would false-alert on every fresh deploy
before the first auth.
"""

import logging
from collections.abc import Callable
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.errors.rpcbaseerrors import AuthKeyError, UnauthorizedError

from app.config import settings

# Import direction is telegram → capture ONLY for the plain dataclass (no
# cycle: capture.py never imports telethon nor this module).
from app.core.capture import IncomingReply

# Re-exported so the worker can catch FloodWaitError without importing
# telethon itself (the boundary: telethon imports live ONLY in this module).
__all__ = ["FloodWaitError", "SessionLostError", "TelegramGateway", "gateway"]

logger = logging.getLogger(__name__)


class SessionLostError(Exception):
    """The Telegram session died (Story 4.1) — a DOMAIN exception.

    Raised instead of the underlying telethon error so the worker never
    imports telethon classes (the architecture boundary). ``UnauthorizedError``
    is the base of every 401 (AUTH_KEY_UNREGISTERED, SESSION_REVOKED,
    SESSION_EXPIRED, USER_DEACTIVATED…) and ``AuthKeyError`` the 406 base
    (AUTH_KEY_DUPLICATED) — the AC's literal "AuthKeyError or deauthorization".
    """


# The telethon shapes that mean "the session is gone" on a hot call.
_AUTH_LOSS_ERRORS = (UnauthorizedError, AuthKeyError)


class TelegramGateway:
    """Single owner of the Telethon client + the resolved send target."""

    def __init__(self) -> None:
        self.client: TelegramClient | None = None
        self.authorized: bool = False
        # True by default so tests can drive sending by flipping only
        # ``authorized``; ``connect()`` sets it for real.
        self.target_ok: bool = True
        self._entity: object | None = None
        # Capture bridge (Story 3.1): installed BEFORE connect() by the
        # lifespan; the marked peer id of the target, captured at resolve
        # time, is what the handlers filter on (None ⇒ boot gap: events are
        # enqueued unfiltered — attribution is the real authority).
        self._capture: Callable[[IncomingReply], None] | None = None
        self._target_id: int | None = None
        self._handlers_registered = False

    @property
    def ready(self) -> bool:
        """True iff the gateway can actually deliver messages."""
        return self.authorized and self.target_ok and self._entity is not None

    async def connect(self) -> None:
        """Connect + resolve the target. NEVER raises — failures log and leave
        the gateway unauthorized (the app boots regardless)."""
        if not settings.telegram_api_id or not settings.telegram_api_hash:
            logger.warning(
                "telegram credentials missing — sending stays down (503)"
            )
            self.authorized = False
            return
        try:
            # The client is constructed ONCE and reused on a re-call (review
            # 3-1): connect() is public and may run again after a transient
            # failure — a fresh client per call would orphan the previous one
            # (still connected and dispatching, double-enqueueing every event).
            if self.client is None:
                self.client = TelegramClient(
                    settings.telegram_session_path,
                    settings.telegram_api_id,
                    settings.telegram_api_hash,
                    catch_up=True,
                )
            # Handlers BEFORE connect() (review 3-1): with catch_up=True
            # telethon starts dispatching missed updates as soon as the
            # connection is up AND advances the persisted pts state — anything
            # dispatched before registration would be lost forever (the next
            # boot's catch-up will not redeliver it).
            self._register_capture_handlers()
            await self.client.connect()
            self.authorized = await self.client.is_user_authorized()
        except Exception:
            logger.exception("telegram connect failed — sending stays down")
            self.authorized = False
            return
        if not self.authorized:
            logger.warning(
                "anon.session missing/unauthorized — run scripts/telegram_auth.py"
            )
            return
        await self._resolve_target()

    async def _resolve_target(self) -> None:
        """Resolve ``telegram_target`` once; failure marks ``target_ok=False``."""
        target = settings.telegram_target.strip().lstrip("@")
        if not target or self.client is None:
            logger.warning("TELEGRAM_TARGET not set — sending stays down")
            self.target_ok = False
            return
        try:
            self._entity = await self.client.get_input_entity(target)
            # Marked peer id (matches event.chat_id) — the capture filter.
            self._target_id = await self.client.get_peer_id(self._entity)
            self.target_ok = True
        except Exception:
            logger.exception("could not resolve TELEGRAM_TARGET %r", target)
            self.target_ok = False

    def register_capture(self, callback: Callable[[IncomingReply], None]) -> None:
        """Install the capture callback (Story 3.1). Called BEFORE ``connect()``
        — the handlers themselves are registered once inside it."""
        self._capture = callback

    def _register_capture_handlers(self) -> None:
        """Register ``NewMessage``/``MessageEdited`` handlers EXACTLY ONCE —
        enforced by ``_handlers_registered`` (review 3-1: connect() is public
        and re-callable; duplicate handlers would double-enqueue every event,
        double-count the unmatched bucket and double the DB work per reply).

        Runs BEFORE ``client.connect()`` so no catch_up replay is dispatched
        unobserved. No ``chats=`` filter on purpose (the legacy web decision):
        filtering lives in the handler body on the resolved ``_target_id``, so
        the registration survives a future multi-target without
        re-registration. Telethon stays confined to this module — events cross
        the boundary only as the plain ``IncomingReply`` dataclass.
        """
        if (
            self._handlers_registered
            or self.client is None
            or self._capture is None
        ):
            return

        async def _on_new(event: Any) -> None:
            self._bridge(event, edited=False)

        async def _on_edit(event: Any) -> None:
            self._bridge(event, edited=True)

        self.client.add_event_handler(_on_new, events.NewMessage())
        self.client.add_event_handler(_on_edit, events.MessageEdited())
        self._handlers_registered = True

    def _bridge(self, event: Any, *, edited: bool) -> None:
        """Filter + convert one telethon event into an ``IncomingReply``."""
        if event.out:
            return  # our own outgoing messages are never bot replies
        # _target_id is None only in the boot gap (handlers live from BEFORE
        # connect(); the target resolves after the authorization check):
        # enqueue UNFILTERED rather than drop (review 3-1) — attribution via
        # send_log is the real authority, so a cross-chat message simply lands
        # in the monitored unmatched bucket, while a dropped catch_up replay
        # would be lost forever (telethon advances the persisted pts state).
        if self._target_id is not None and event.chat_id != self._target_id:
            return
        capture = self._capture
        if capture is None:
            return
        reply_to = event.message.reply_to_msg_id
        capture(
            IncomingReply(
                message_id=int(event.message.id),
                reply_to_msg_id=int(reply_to) if reply_to is not None else None,
                text=event.raw_text or "",
                edited=edited,
            )
        )

    async def send(self, text: str) -> int:
        """Send ``text`` to the resolved target; return the Telegram message id.

        The id feeds ``send_log`` (Story 2.5 write-ahead → Story 3.1
        attribution). ``FloodWaitError`` propagates to the worker (the worker
        owns retry policy); an auth-loss error is converted to the domain
        ``SessionLostError`` (Story 4.1) after flipping ``authorized`` off so
        new ``POST /api/batches`` 503 on their own.
        """
        if self.client is None or self._entity is None:
            raise RuntimeError("telegram gateway not ready")
        try:
            # parse_mode=None (2.5 deferred fix): delivered text must equal
            # line.text BYTE-FOR-BYTE — Telethon's default markdown rendering
            # strips `**`/`__`/backticks from data lines, corrupting what the
            # bot receives AND breaking both boot reconciliation's equality
            # check and 3.1's attribution assumptions.
            message = await self.client.send_message(
                self._entity, text, parse_mode=None
            )
        except _AUTH_LOSS_ERRORS as e:
            self.authorized = False
            logger.error("event=session_lost source=send error=%s: %s", type(e).__name__, e)
            raise SessionLostError(f"{type(e).__name__}: {e}") from e
        return int(message.id)

    async def recent_outgoing(self, limit: int = 50) -> list[tuple[int, str]]:
        """Recent messages WE sent to the target, newest first: ``(id, text)``.

        Story 2.5 boot reconciliation: a line a crash left in 'sending' is
        confirmed against these instead of blindly re-queued (never
        double-sent). Raises when the client isn't ready — the caller owns
        the fallback (telethon stays confined to this module).
        """
        if self.client is None or self._entity is None:
            raise RuntimeError("telegram gateway not ready")
        try:
            # Plain history + client-side ``m.out`` filter (2.5 deferred fix):
            # ``from_user="me"`` switches Telethon to messages.Search, whose
            # weaker consistency can miss a message sent seconds before a
            # crash — exactly the message reconciliation exists to find.
            # getHistory is strongly consistent. ``raw_text`` (not ``text``)
            # so the comparison sees what was sent, never a markdown render.
            # Bot replies interleave with our sends (~1:1), so scan a wider
            # raw window and stop once ``limit`` OUTGOING messages are found.
            messages: list[tuple[int, str]] = []
            async for message in self.client.iter_messages(
                self._entity, limit=limit * 4
            ):
                if not message.out:
                    continue
                messages.append((int(message.id), message.raw_text or ""))
                if len(messages) >= limit:
                    break
        except _AUTH_LOSS_ERRORS as e:
            # Same conversion as send() — the boot-recovery caller falls into
            # its existing reconcile_unverified fallback; the first real send
            # then latches the watchdog (Story 4.1 recorded decision: the
            # WORKER triggers, it owns the claimed-line context).
            self.authorized = False
            logger.error(
                "event=session_lost source=recent_outgoing error=%s: %s",
                type(e).__name__,
                e,
            )
            raise SessionLostError(f"{type(e).__name__}: {e}") from e
        return messages

    async def disconnect(self) -> None:
        """Disconnect on shutdown (no-op when never connected)."""
        if self.client is not None:
            await self.client.disconnect()


# Module-level singleton, same idiom as ``settings`` (wired in main's lifespan).
gateway = TelegramGateway()
