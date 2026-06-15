"""Standalone Telegram bot command enumerator (reconnaissance utility).

Brute-forces undocumented bot commands by sending ``/<combo>`` messages and
recording which ones get a reply. The target bot stays SILENT on an unknown
command, so **any reply = a valid hidden command**.

This is a self-contained recon tool, NOT part of the SaaS app. It does NOT
import ``app.*`` and ``backend/app/`` never imports it. It lives under
``scripts/`` only for convenience (venv + ``backend/.env`` are at hand).

⚠️  Run it with a SEPARATE Telegram account/session — NEVER the production
``anon.session``. Tens of thousands of messages risk FloodWait/ban, which on
the shared production account would take down real client sending (project
invariant: protect the single shared account). The default session file is
``enum.session`` in the CWD, deliberately not the production path.

Efficiency model (decoupled send/receive):
    The slow way is send -> wait-for-reply -> send (blocking round-trip). This
    script instead fires commands at a fixed pace and collects replies
    asynchronously in the same event loop. Each sent message id maps to its
    combo (``sent_map``); the bot replies AS A REPLY, so ``reply_to_msg_id``
    points back at the command message and resolves the combo -- the same
    attribution trick production uses (send_log.message_id). Throughput is
    bound only by the send interval, never by reply latency.

Resume: combos are generated deterministically (itertools.product, sorted
charset) so progress is a single integer index, checkpointed to a JSON state
file. Ctrl+C persists the index; the next run continues exactly where it
stopped (no re-sending, no lost progress).

Usage (from backend/, venv active):
    # dry run -- print the combo order, send nothing
    python -m scripts.enum_commands --target @somebot --dry-run

    # real sweep of all 3-letter a-z combos (26^3 = 17,576, ~7.3h at 1.5s)
    python -m scripts.enum_commands --target @somebot

    # resume is automatic -- just run the same command again

    # later, optional second pass including digits
    python -m scripts.enum_commands --target @somebot --charset alnum \
        --state enum_state_alnum.json --out enum_hits_alnum.txt --only-with-digit

Reads TELEGRAM_API_ID / TELEGRAM_API_HASH from backend/.env (or env / flags).
Get API credentials at https://my.telegram.org/apps.
"""

import argparse
import asyncio
import itertools
import json
import os
import signal
import string
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from telethon import TelegramClient, events, utils
from telethon.errors import (
    AuthKeyError,
    FloodWaitError,
    UnauthorizedError,
)

# Same backend/.env the app reads; extra="ignore" so unrelated keys are fine.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

_CHARSETS = {
    "letters": string.ascii_lowercase,            # a-z (26)
    "alnum": string.ascii_lowercase + string.digits,  # a-z0-9 (36)
}
_DIGITS = set(string.digits)

# Persist the checkpoint at least this often (in number of sends) so a crash
# (not just a clean Ctrl+C) loses at most this many combos of progress.
_CHECKPOINT_EVERY = 25
# Drop sent_map entries older than this many sends. Replies arrive within
# seconds at a >=1s pace, so this window never discards a still-pending combo
# while keeping memory bounded over a full 17k-combo run.
_SENT_MAP_WINDOW = 300
# FloodWait padding so we resume comfortably after Telegram's stated cooldown.
_FLOOD_BUFFER_SECONDS = 5


class _Creds(BaseSettings):
    """Telethon credentials, read only by this script (never by the app)."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_api_id: int
    telegram_api_hash: str


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="enum_commands",
        description="Enumerate hidden Telegram bot commands via /<combo> probing.",
    )
    p.add_argument(
        "--target",
        required=True,
        help="Destination bot/chat: @username or numeric id.",
    )
    p.add_argument(
        "--session",
        default="enum.session",
        help="Telethon session file (SEPARATE account; default enum.session). "
        "Never point this at the production anon.session.",
    )
    p.add_argument("--length", type=int, default=3, help="Combo length (default 3).")
    p.add_argument(
        "--charset",
        choices=sorted(_CHARSETS),
        default="letters",
        help="letters=a-z (default), alnum=a-z0-9.",
    )
    p.add_argument(
        "--only-with-digit",
        action="store_true",
        help="With --charset alnum, skip combos that contain no digit "
        "(i.e. those already covered by a prior letters-only pass).",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=1.5,
        help="Seconds between sends (default 1.5; <1.0 warns: ban risk).",
    )
    p.add_argument("--prefix", default="/", help="Command prefix (default '/').")
    p.add_argument(
        "--state",
        default="enum_state.json",
        help="Checkpoint file for resume (default enum_state.json).",
    )
    p.add_argument(
        "--out",
        default="enum_hits.txt",
        help="Append-only hits file (default enum_hits.txt).",
    )
    p.add_argument(
        "--grace",
        type=float,
        default=45.0,
        help="Seconds to keep collecting replies after the last send (default 45).",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the combo order and exit; connect to nothing, send nothing.",
    )
    p.add_argument("--api-id", type=int, default=None, help="Override TELEGRAM_API_ID.")
    p.add_argument("--api-hash", default=None, help="Override TELEGRAM_API_HASH.")
    return p.parse_args(argv)


def _config_signature(args: argparse.Namespace) -> str:
    """Identity of the search space; a resumed run must match it exactly."""
    return "|".join(
        [
            f"charset={args.charset}",
            f"length={args.length}",
            f"prefix={args.prefix}",
            f"only_with_digit={int(args.only_with_digit)}",
        ]
    )


def _iter_combos(args: argparse.Namespace):
    """Deterministic combo stream: sorted charset, itertools.product order.

    Index N is stable across runs for a given config signature, which is what
    makes the single-integer checkpoint a valid resume point.
    """
    alphabet = sorted(_CHARSETS[args.charset])
    for tup in itertools.product(alphabet, repeat=args.length):
        combo = "".join(tup)
        if args.only_with_digit and _DIGITS.isdisjoint(combo):
            continue
        yield combo


def _total_combos(args: argparse.Namespace) -> int:
    n = len(_CHARSETS[args.charset]) ** args.length
    if args.only_with_digit:
        letters_only = len(_CHARSETS["letters"]) ** args.length
        return n - letters_only
    return n


def _load_state(path: Path, signature: str) -> int:
    """Return the next combo index to send, validating the config signature."""
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        sys.exit(f"cannot read state file {path}: {exc}")
    if data.get("signature") != signature:
        sys.exit(
            f"state file {path} was written for a DIFFERENT search "
            f"(signature {data.get('signature')!r} != {signature!r}).\n"
            "Use a fresh --state file for this configuration, or delete the old one."
        )
    return int(data.get("next_index", 0))


def _save_state(path: Path, signature: str, next_index: int) -> None:
    """Atomically persist the checkpoint (write-temp-then-rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps({"signature": signature, "next_index": next_index}, indent=2)
    )
    os.replace(tmp, path)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


class Enumerator:
    """Owns the send loop, the reply handler, and shared attribution state."""

    def __init__(self, client: TelegramClient, args: argparse.Namespace):
        self.client = client
        self.args = args
        self.out_path = Path(args.out)
        self.state_path = Path(args.state)
        self.signature = _config_signature(args)
        # message_id -> (combo, send_seq) for reply attribution + windowed prune.
        self.sent_map: dict[int, tuple[str, int]] = {}
        self.send_seq = 0
        self.last_combo: str | None = None
        self.target_id: int | None = None
        self.hits = 0
        self._stop = asyncio.Event()

    # -- output -----------------------------------------------------------
    def _record_hit(self, combo: str, reply_text: str, marker: str = "") -> None:
        self.hits += 1
        snippet = " ".join(reply_text.split())[:200]
        tag = f"{marker} " if marker else ""
        line = f"{_now()}\t{tag}{self.args.prefix}{combo}\t{snippet}\n"
        with self.out_path.open("a", encoding="utf-8") as fh:
            fh.write(line)
        print(f"  HIT {tag}{self.args.prefix}{combo}  ->  {snippet}")

    # -- reply handling ---------------------------------------------------
    def _handle_reply(self, event) -> None:
        if event.out or event.chat_id != self.target_id:
            return
        text = event.raw_text or ""
        reply_to = getattr(event.message, "reply_to_msg_id", None)
        entry = self.sent_map.get(reply_to) if reply_to is not None else None
        if entry is not None:
            self._record_hit(entry[0], text)
        elif self.last_combo is not None:
            # No reply_to_msg_id: best-effort attribute to the latest send.
            # Flagged '?' so it can be reconciled manually.
            self._record_hit(self.last_combo, text, marker="?")

    # -- send loop --------------------------------------------------------
    async def _send_one(self, entity, combo: str) -> None:
        """Send one combo, retrying forever on FloodWait (same combo)."""
        while not self._stop.is_set():
            try:
                msg = await self.client.send_message(
                    entity, f"{self.args.prefix}{combo}", parse_mode=None
                )
                self.send_seq += 1
                self.sent_map[msg.id] = (combo, self.send_seq)
                self.last_combo = combo
                self._prune_sent_map()
                return
            except FloodWaitError as exc:
                wait = exc.seconds + _FLOOD_BUFFER_SECONDS
                print(f"  FloodWait {exc.seconds}s -> sleeping {wait}s, retry {combo!r}")
                await self._sleep(wait)

    def _prune_sent_map(self) -> None:
        if len(self.sent_map) <= _SENT_MAP_WINDOW:
            return
        cutoff = self.send_seq - _SENT_MAP_WINDOW
        for mid in [m for m, (_, seq) in self.sent_map.items() if seq < cutoff]:
            del self.sent_map[mid]

    async def _sleep(self, seconds: float) -> None:
        """Cancelable sleep: wakes early if a stop signal arrives."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            pass

    async def run(self, entity) -> None:
        args = self.args
        signature = self.signature
        total = _total_combos(args)
        start_index = _load_state(self.state_path, signature)
        if start_index >= total:
            print(f"already complete: {start_index}/{total} combos sent. Nothing to do.")
            return

        print(
            f"sweep: {total} combos, resuming at index {start_index} "
            f"({total - start_index} remaining), interval {args.interval}s, "
            f"target_id {self.target_id}"
        )
        index = start_index
        try:
            for combo in itertools.islice(_iter_combos(args), start_index, None):
                if self._stop.is_set():
                    break
                await self._send_one(entity, combo)
                index += 1
                if index % _CHECKPOINT_EVERY == 0:
                    _save_state(self.state_path, signature, index)
                    print(f"  ... {index}/{total} sent, {self.hits} hits so far")
                await self._sleep(args.interval)
        finally:
            _save_state(self.state_path, signature, index)
            print(f"checkpoint saved at index {index}.")

        if not self._stop.is_set():
            print(f"all combos sent. Draining replies for {args.grace}s ...")
            await self._sleep(args.grace)
        print(f"done. {self.hits} hits written to {self.out_path}.")

    def request_stop(self) -> None:
        self._stop.set()


async def _resolve_target(client: TelegramClient, target: str):
    """Resolve @username/id to a send entity + the marked chat id for filtering."""
    raw = target.strip()
    spec: str | int = int(raw) if raw.lstrip("-").isdigit() else raw
    entity = await client.get_input_entity(spec)
    return entity, utils.get_peer_id(entity)


async def _run(args: argparse.Namespace, creds: _Creds) -> None:
    client = TelegramClient(args.session, creds.telegram_api_id, creds.telegram_api_hash)
    enum = Enumerator(client, args)

    # Register handlers BEFORE connect so no early reply is missed.
    client.add_event_handler(enum._handle_reply, events.NewMessage())
    client.add_event_handler(enum._handle_reply, events.MessageEdited())

    await client.connect()
    try:
        if not await client.is_user_authorized():
            await client.start(phone=lambda: input("phone (international format): "))
        me = await client.get_me()
        print(f"authorized as {me.first_name} (id={me.id}) using session {args.session}")

        entity, enum.target_id = await _resolve_target(client, args.target)

        # Ctrl+C / SIGTERM -> graceful stop (persist checkpoint, drain, exit).
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, enum.request_stop)
            except NotImplementedError:
                pass  # add_signal_handler is unsupported on some platforms

        await enum.run(entity)
    finally:
        await client.disconnect()


def main() -> None:
    args = _parse_args(sys.argv[1:])

    if args.interval < 1.0:
        print(
            f"WARNING: interval {args.interval}s < 1.0s — high FloodWait/ban risk "
            "on a user account. Continuing in 3s; Ctrl+C to abort.",
            file=sys.stderr,
        )

    if args.dry_run:
        total = _total_combos(args)
        print(f"DRY RUN: {total} combos, charset={args.charset}, length={args.length}")
        preview = list(itertools.islice(_iter_combos(args), 10))
        last = None
        for combo in _iter_combos(args):
            last = combo
        print(f"  first: {', '.join(args.prefix + c for c in preview)} ...")
        print(f"  last:  {args.prefix}{last}")
        return

    if Path(args.session).name == "anon.session":
        sys.exit(
            "refusing to use 'anon.session' (the production session). Pick a "
            "SEPARATE session file via --session (default enum.session)."
        )

    api_id = args.api_id
    api_hash = args.api_hash
    if api_id is None or api_hash is None:
        try:
            env_creds = _Creds()
        except ValidationError:
            sys.exit(
                "missing TELEGRAM_API_ID / TELEGRAM_API_HASH. Set them in "
                "backend/.env or pass --api-id/--api-hash "
                "(get them at https://my.telegram.org/apps)."
            )
        api_id = api_id if api_id is not None else env_creds.telegram_api_id
        api_hash = api_hash if api_hash is not None else env_creds.telegram_api_hash
    creds = _Creds.model_construct(telegram_api_id=api_id, telegram_api_hash=api_hash)

    # Restrictive perms on the new session file from birth (mirrors
    # scripts.telegram_auth: an interrupted login must not leave it readable).
    os.umask(0o077)

    try:
        asyncio.run(_run(args, creds))
    except (AuthKeyError, UnauthorizedError) as exc:
        sys.exit(
            f"\nTelegram auth lost ({type(exc).__name__}): the session is no longer "
            "authorized. Delete the session file and re-run to log in again."
        )


if __name__ == "__main__":
    main()
