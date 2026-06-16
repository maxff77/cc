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
        # ``authorized``; ``reload_targets()`` sets it for real (True iff at
        # least one destination resolved).
        self.target_ok: bool = True
        # Resolved send destinations: (input_entity, marked_peer_id) pairs.
        # ``send()`` round-robins over them; ``_target_ids`` is the capture
        # filter (set membership). EMPTY set ⇒ boot gap: events enqueue
        # unfiltered — attribution via send_log is the real authority.
        self._entities: list[tuple[object, int]] = []
        self._target_ids: set[int] = set()
        self._send_index: int = 0
        # Capture bridge (Story 3.1): installed BEFORE connect() by the lifespan.
        self._capture: Callable[[IncomingReply], None] | None = None
        self._handlers_registered = False

    @property
    def ready(self) -> bool:
        """True iff the gateway can actually deliver messages."""
        return self.authorized and bool(self._entities)

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
        # Targets are resolved separately via ``reload_targets()``, driven by
        # the lifespan from the DB list (multi-target sending) — the gateway
        # stays agnostic of the DB. connect() only owns the client + auth.

    async def reload_targets(
        self, targets: list[tuple[int, str]]
    ) -> dict[str, list[int]]:
        """Resolve ``(chat_id, label)`` destinations into the active send set.

        Atomically replaces ``_entities``/``_target_ids`` (a single rebind, so an
        in-flight ``send()``/``recent_outgoing()`` keeps iterating its own
        snapshot). A target that no longer resolves (the account left the chat,
        a bad id) is SKIPPED and reported, never fatal — ``target_ok`` becomes
        True iff at least one resolved. Safe to call at runtime (owner edit).
        Returns ``{"resolved": [peer_ids], "failed": [chat_ids]}``.

        A bare supergroup/channel id (``-100…``) only resolves when telethon has
        its access_hash cached; a COLD session (fresh process) does not. So on
        the first resolution failure we warm the entity cache ONCE via
        ``get_dialogs`` and retry the stragglers — otherwise a saved numeric
        target would silently 503 after every restart. Duplicate peer ids
        (two stored rows for one chat) are kept once.
        """
        if self.client is None or not self.authorized:
            self._entities = []
            self._target_ids = set()
            self._send_index = 0
            self.target_ok = False
            return {"resolved": [], "failed": [cid for cid, _ in targets]}
        resolved: list[tuple[object, int]] = []
        seen_peers: set[int] = set()
        pending = list(targets)
        warmed = False
        while True:
            still_failing: list[tuple[int, str]] = []
            for chat_id, label in pending:
                peer = await self._resolve_peer(chat_id)
                if peer is None:
                    still_failing.append((chat_id, label))
                    continue
                entity, peer_id = peer
                if peer_id in seen_peers:
                    continue  # two stored ids for one chat → keep it once
                seen_peers.add(peer_id)
                resolved.append((entity, peer_id))
            pending = still_failing
            if not pending or warmed:
                break
            warmed = True
            try:
                # Warm telethon's session entity cache (access_hash) so bare
                # numeric channel/supergroup ids resolve on the retry pass.
                await self.client.get_dialogs(limit=200)
            except Exception:
                logger.warning("get_dialogs cache-warm failed")
        for chat_id, label in pending:
            logger.warning(
                "could not resolve send target chat_id=%s label=%r", chat_id, label
            )
        self._entities = resolved
        self._target_ids = {peer_id for _, peer_id in resolved}
        self._send_index = 0
        self.target_ok = bool(resolved)
        if not resolved:
            logger.warning("no send targets resolved — sending stays down (503)")
        return {
            "resolved": [peer_id for _, peer_id in resolved],
            "failed": [cid for cid, _ in pending],
        }

    async def _resolve_peer(
        self, identifier: int | str
    ) -> tuple[object, int] | None:
        """Resolve one id/@username to ``(input_entity, marked_peer_id)`` or None."""
        try:
            entity = await self.client.get_input_entity(identifier)
            return entity, int(await self.client.get_peer_id(entity))
        except Exception:
            return None

    async def resolve_one(self, identifier: int | str) -> int | None:
        """Resolve a single target (id or @username) to its marked chat id, or
        ``None``. Validates an owner's pick before persisting and seeds from the
        legacy ``TELEGRAM_TARGET`` env."""
        if self.client is None or not self.authorized:
            return None
        peer = await self._resolve_peer(identifier)
        if peer is None:
            logger.warning("could not resolve target identifier %r", identifier)
            return None
        return peer[1]

    def resolved_ids(self) -> set[int]:
        """Marked chat ids the gateway currently has resolved (live status)."""
        return set(self._target_ids)

    async def list_dialogs(self, limit: int = 100) -> list[tuple[int, str]]:
        """Discovery: chats the account is in, as ``(chat_id, title)``.

        Feeds the owner's "pick a destination" UI. Raises when not authorized —
        the caller maps it to ``telegram_unauthorized``."""
        if self.client is None or not self.authorized:
            raise RuntimeError("telegram gateway not authorized")
        out: list[tuple[int, str]] = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            out.append((int(dialog.id), dialog.name or str(dialog.id)))
        return out

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
        # _target_ids is EMPTY only in the boot gap (handlers live from BEFORE
        # connect(); targets resolve after the authorization check): enqueue
        # UNFILTERED rather than drop (review 3-1) — attribution via send_log is
        # the real authority, so a cross-chat message simply lands in the
        # monitored unmatched bucket, while a dropped catch_up replay would be
        # lost forever (telethon advances the persisted pts state). Once
        # resolved, only messages from a registered destination pass (the set
        # generalizes the original single-target equality to multi-target).
        if self._target_ids and event.chat_id not in self._target_ids:
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
        # Snapshot the list (a concurrent reload_targets rebinds it): one send
        # works against one consistent view.
        entities = self._entities
        if self.client is None or not entities:
            raise RuntimeError("telegram gateway not ready")
        # Round-robin per message across the resolved destinations (spreads
        # per-chat load — the operator's manual "send to CC1..CC6 in turn").
        # The cursor is process memory, like the scheduler's: a restart/reload
        # resets it and rotation re-establishes itself. Advance only on SUCCESS
        # so a FloodWait retries the SAME chat. This NEVER changes pacing — the
        # global interval is the scheduler's; this only picks WHICH chat.
        idx = self._send_index % len(entities)
        entity, _ = entities[idx]
        try:
            # parse_mode=None (2.5 deferred fix): delivered text must equal
            # line.text BYTE-FOR-BYTE — Telethon's default markdown rendering
            # strips `**`/`__`/backticks from data lines, corrupting what the
            # bot receives AND breaking both boot reconciliation's equality
            # check and 3.1's attribution assumptions.
            message = await self.client.send_message(
                entity, text, parse_mode=None
            )
        except _AUTH_LOSS_ERRORS as e:
            self.authorized = False
            logger.error("event=session_lost source=send error=%s: %s", type(e).__name__, e)
            raise SessionLostError(f"{type(e).__name__}: {e}") from e
        self._send_index = idx + 1
        return int(message.id)

    async def recent_outgoing(self, limit: int = 50) -> list[tuple[int, str]]:
        """Recent messages WE sent to the target, newest first: ``(id, text)``.

        Story 2.5 boot reconciliation: a line a crash left in 'sending' is
        confirmed against these instead of blindly re-queued (never
        double-sent). Raises when the client isn't ready — the caller owns
        the fallback (telethon stays confined to this module).
        """
        entities = self._entities  # snapshot vs a concurrent reload rebind
        if self.client is None or not entities:
            raise RuntimeError("telegram gateway not ready")
        try:
            # Aggregate recent OUTGOING messages across ALL destinations
            # (round-robin spreads our sends over them). message_id is
            # account-global, so dedup by id + sort newest-first reproduces the
            # shape boot reconciliation expects from a single chat.
            # Plain history + client-side ``m.out`` filter (2.5 deferred fix):
            # ``from_user="me"`` switches Telethon to messages.Search, whose
            # weaker consistency can miss a message sent seconds before a crash
            # — getHistory is strongly consistent. ``raw_text`` (not ``text``)
            # so the comparison sees what was sent, never a markdown render.
            # Bot replies interleave ~1:1, so scan a wider raw window per chat
            # and stop once ``limit`` OUTGOING are found in it.
            seen: set[int] = set()
            merged: list[tuple[int, str]] = []
            for entity, _ in entities:
                kept = 0
                async for message in self.client.iter_messages(
                    entity, limit=limit * 4
                ):
                    if not message.out:
                        continue
                    mid = int(message.id)
                    if mid in seen:
                        continue
                    seen.add(mid)
                    merged.append((mid, message.raw_text or ""))
                    kept += 1
                    if kept >= limit:
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
        merged.sort(key=lambda t: t[0], reverse=True)
        return merged

    async def recent_incoming(
        self, floor_id: int, limit: int
    ) -> list[tuple[int, int | None, str]]:
        """Inbound (bot) messages from the target(s), newest-first down to
        ``floor_id`` — the reply reconciler's history scan that recovers
        replies the live update stream dropped (catch_up gaps, missed edits).

        Mirror of ``recent_outgoing``: aggregates across destinations, dedups
        by the account-global id, keeps only messages we did NOT send (``not
        out``), and STOPS once an id falls below ``floor_id``. A bot reply's id
        is always greater than the send it answers (it is sent later on the
        account-global sequence), so ``floor_id = min(awaiting sends)`` bounds
        the scan to the relevant window; ``limit`` is a hard per-target safety
        cap on the raw scan. ``raw_text`` (never ``text``) so the comparison
        sees the literal message, never a markdown render — same as
        ``recent_outgoing``. Auth-loss converts to the domain
        ``SessionLostError`` exactly like the other hot reads.
        """
        entities = self._entities  # snapshot vs a concurrent reload rebind
        if self.client is None or not entities:
            raise RuntimeError("telegram gateway not ready")
        try:
            seen: set[int] = set()
            merged: list[tuple[int, int | None, str]] = []
            for entity, _ in entities:
                async for message in self.client.iter_messages(
                    entity, limit=limit
                ):
                    mid = int(message.id)
                    if mid < floor_id:
                        break  # newest-first: nothing older can match
                    if message.out or mid in seen:
                        continue
                    seen.add(mid)
                    reply_to = message.reply_to_msg_id
                    merged.append(
                        (
                            mid,
                            int(reply_to) if reply_to is not None else None,
                            message.raw_text or "",
                        )
                    )
        except _AUTH_LOSS_ERRORS as e:
            self.authorized = False
            logger.error(
                "event=session_lost source=recent_incoming error=%s: %s",
                type(e).__name__,
                e,
            )
            raise SessionLostError(f"{type(e).__name__}: {e}") from e
        merged.sort(key=lambda t: t[0], reverse=True)
        return merged

    async def disconnect(self) -> None:
        """Disconnect on shutdown (no-op when never connected)."""
        if self.client is not None:
            await self.client.disconnect()


# Module-level singleton, same idiom as ``settings`` (wired in main's lifespan).
gateway = TelegramGateway()
