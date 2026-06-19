"""Redaction of operator-identifying lines and sensitive stats from captured
bot replies.

Two kinds of scrubbing live here:

1. **"Checked By" + Credits — GLOBAL, every category.** The checker bot echoes
   a ``⌿ Checked By : <name> [User]`` line (leaks the operator's real name) and
   a ``Credits: <n>`` segment (leaks the owner's account balance). Neither may
   surface in any tenant-facing view or export. We strip both in two places —
   at **capture** (``core/capture.py``) so they never persist going forward, and
   on **read** (detail/snapshot/export builders) so replies captured *before*
   this redaction existed are scrubbed too, with no data migration. Both are
   done by ``redact_reply_text`` (idempotent — safe to apply at capture and
   again on read).

2. **Approveds!/Deads! stats — SPECIAL-MODE categories only.** Gates in a
   category the owner flagged ``special_mode`` emit an aggregate stats line:
   ``↳ Approveds! ✅: 0 ヾ⌿ Deads! ❌: 1 ヾ⌿ Credits: … ヾ⌿ Time: 32.95s``.
   In those sessions the capture pipeline derives the reply's status from the
   ``Approveds! ✅: N`` count (``parse_approveds``) and then strips the
   Approveds!/Deads! segments from the stored reply (``strip_special_stats``,
   capture only — no historical special-mode rows exist). ``Time:`` is kept.

The bot joins stats segments with the ``ヾ⌿`` separator; removing a segment
leaves separator artifacts, so every stripper runs ``_tidy_separators`` to
collapse runs and drop dangling separators (e.g. ``↳ Time: 32.95s``).
"""

import re

# Marks the bot's operator-attribution line, e.g. "⌿ Checked By : Richard [User]".
_CHECKED_BY = re.compile(r"(?i)\bchecked\s*by\b")

# The bot's stats-line separator.
_SEP = "ヾ⌿"

# Stats-segment matchers. Case-insensitive and tolerant of the optional ✅/❌
# glyph (the label word is specific enough not to over-match). The value runs to
# the next separator/end-of-line. ``Credits`` consumes a digit run WITH grouping
# punctuation (``[\d.,]+``) so a grouped balance like ``999,996044`` is removed
# whole — a bare ``\d+`` would stop at the comma and leak the tail.
_CREDITS = re.compile(r"(?i)Credits\s*:\s*[\d.,]+")
_APPROVEDS = re.compile(r"(?i)Approveds!?\s*✅?\s*:\s*\d+")
_DEADS = re.compile(r"(?i)Deads!?\s*❌?\s*:\s*\d+")
# Just the Approveds count — the special-mode validity decision reads this.
_APPROVEDS_COUNT = re.compile(r"(?i)Approveds!?\s*✅?\s*:\s*(\d+)")


def _tidy_separators(text: str) -> str:
    """Collapse the ``ヾ⌿`` artifacts a removed stats segment leaves behind.

    Only touches lines that still contain a separator, so non-stats content is
    preserved verbatim. Collapses separator runs to a single ` ヾ⌿ `, drops a
    separator dangling at line start (or right after the bot's leading ``↳``
    arrow) and at line end.
    """
    out: list[str] = []
    for line in text.split("\n"):
        if _SEP in line:
            line = re.sub(rf"(?:\s*{_SEP}\s*)+", f" {_SEP} ", line)
            line = re.sub(rf"^(\s*↳?\s*){_SEP}\s*", r"\1", line)
            line = re.sub(rf"\s*{_SEP}\s*$", "", line)
            line = line.rstrip()
        out.append(line)
    return "\n".join(out)


def redact_reply_text(text: str) -> str:
    """Return ``text`` with the "Checked By" line and any ``Credits:`` segment
    removed (the GLOBAL scrub applied at capture AND on read).

    Drops "Checked By" lines entirely; removes the ``Credits: <n>`` segment in
    place and tidies the separator it leaves. Preserves the rest verbatim and
    is idempotent.
    """
    if not text:
        return text
    kept = [line for line in text.splitlines() if not _CHECKED_BY.search(line)]
    redacted = _CREDITS.sub("", "\n".join(kept))
    return _tidy_separators(redacted)


def parse_approveds(text: str) -> int | None:
    """The ``N`` in ``Approveds! ✅: N``, or ``None`` if absent.

    Special-mode validity (``core/capture.py``): a reply with no Approveds line
    yet is still "processing" (the legacy ``⏳`` intermediate state).
    """
    match = _APPROVEDS_COUNT.search(text)
    return int(match.group(1)) if match else None


def strip_special_stats(text: str) -> str:
    """Return ``text`` with the ``Approveds!`` and ``Deads!`` stats segments
    removed (special-mode capture only). ``Credits:`` is handled globally by
    ``redact_reply_text``; ``Time:`` is intentionally kept.

    Idempotent: tidies the ``ヾ⌿`` separators the removals leave behind.
    """
    if not text:
        return text
    stripped = _APPROVEDS.sub("", text)
    stripped = _DEADS.sub("", stripped)
    return _tidy_separators(stripped)


# --- Amazon cookie-mode classification (Phase 2) ----------------------------
#
# Cookie-mode replies (the Amazon checker bot) are classified by the
# ``⌿ Status: <token>`` verdict, NOT the legacy ✅/❌ glyph — an ``Approved``/
# ``Declined`` reply carries no glyph and would mis-derive to the previous
# state. These helpers are scoped to the cookie-mode capture branch and are
# NEVER applied to special_mode/non-cookie-mode replies (which stay byte-for-
# byte unchanged). ``strip_special_stats`` is likewise NOT applied here.

# Verdict-kind constants (the worker reads the signal's kind).
VERDICT_APPROVED = "approved"
VERDICT_DECLINED = "declined"
VERDICT_COOKIE_DEAD = "cookie_dead"
VERDICT_FORMAT_ERROR = "format_error"
VERDICT_CONFIRMATION = "confirmation"
VERDICT_NONE = "none"

# The ``⌿ Status: <token>`` verdict anchor — separator/whitespace-tolerant
# (mirror of ``_APPROVEDS_COUNT``). The token is the first non-space run after
# the colon: ``Approved ✅`` → ``Approved``, ``❌ Cookies Inválidas`` → ``❌``.
_STATUS_TOKEN = re.compile(r"(?i)Status\s*:\s*(\S+)")
# The bot's ``⌿ Format :`` help message — only consulted when NO Status line is
# present (Status is checked FIRST).
_FORMAT_LINE = re.compile(r"(?i)\bFormat\s*:")
# The side-band ``.cookie`` confirmation marker (``…almacenó tu cookie
# correctamente. ✅``). Accent-tolerant (``almaceno``/``almacenó``); the real
# drop happens via the content-sniff at the top of ``process_incoming`` — this
# is the parser's fallback classification only.
_COOKIE_CONFIRMATION = re.compile(r"(?i)almacen[oó]\s+tu\s+cookie\s+correctamente")

# The literal content-sniff marker (substring, accent-exact to the bot copy).
COOKIE_CONFIRMATION_MARKER = "almacenó tu cookie correctamente"

# Cookie-mode inline separators glued onto the ``CC:`` line in an Approved
# reply: ``☇ CC: <card>⌿ Status: …`` keeps the card and the verdict on ONE
# line. ``extract_cc`` splits at ``Status`` but leaves the ``⌿`` glued to the
# card, so the bare ``⌿`` (U+233F) and a leading ``☇`` (U+2607) are rewritten
# to newlines BEFORE ``extract_cc`` so the ``CC:`` line terminates before
# ``Status:`` and yields the bare card with no trailing separator.
_INLINE_SEP = "⌿"  # ⌿
_LEADING_BOLT = "☇"  # ☇


def parse_amazon_verdict(text: str) -> tuple[str, str | None]:
    """Classify a cookie-mode bot reply by its ``⌿ Status:`` verdict token.

    Runs on the REDACTED text (``redact_reply_text`` already scrubbed Checked
    By / Credits). Returns ``(verdict_kind, status_token_or_none)`` where
    ``verdict_kind`` is one of ``approved|declined|cookie_dead|format_error|
    confirmation|none``:

    - ``Status:`` present → the first ``\\S+`` token after the colon decides:
      ``Approved`` (case-insensitive) ⇒ ``approved``; ``Declined`` ⇒
      ``declined``; ANYTHING else ⇒ ``cookie_dead`` (the confirmed catch-all:
      ``Error ⚠️``, ``❌ Cookies Inválidas`` → ``❌``, any unknown dead variant).
    - No ``Status:`` but a ``Format :`` help line ⇒ ``format_error`` (Status is
      checked BEFORE Format).
    - The ``almacenó tu cookie correctamente`` confirmation ⇒ ``confirmation``
      (normally already dropped by the content-sniff; this is the fallback).
    - Otherwise ⇒ ``none`` (a pure ⏳/no-verdict edit — persists nothing).

    Owner-locked (2026-06-19): the token catch-all and the Approved/Declined
    exact-match are BINDING.
    """
    if not text:
        return (VERDICT_NONE, None)
    match = _STATUS_TOKEN.search(text)
    if match is not None:
        token = match.group(1)
        lowered = token.lower()
        if lowered == "approved":
            return (VERDICT_APPROVED, token)
        if lowered == "declined":
            return (VERDICT_DECLINED, token)
        return (VERDICT_COOKIE_DEAD, token)
    if _FORMAT_LINE.search(text):
        return (VERDICT_FORMAT_ERROR, None)
    if _COOKIE_CONFIRMATION.search(text):
        return (VERDICT_CONFIRMATION, None)
    return (VERDICT_NONE, None)


def normalize_cookie_cc(text: str) -> str:
    """Rewrite the cookie-mode inline ``⌿``/leading ``☇`` separators to newlines
    BEFORE ``extract_cc`` (cookie-mode scope ONLY — never touches special_mode
    or non-cookie-mode replies).

    An Approved reply glues ``☇ CC: <card>⌿ Status: …`` onto ONE line;
    ``extract_cc`` truncates at ``Status`` but the bare ``⌿`` (U+233F) stays
    welded to the card. Converting it to a newline terminates the ``CC:`` line
    before ``Status:`` so ``extract_cc`` yields exactly the bare card
    (``377481016137504|05|2033|3845``, no trailing ``⌿``). Idempotent.
    """
    if not text:
        return text
    return text.replace(_LEADING_BOLT, "\n").replace(_INLINE_SEP, "\n")
