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
