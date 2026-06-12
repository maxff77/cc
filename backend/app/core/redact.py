"""Redaction of operator-identifying lines from captured bot replies.

The checker bot echoes a ``⌿ Checked By : <name> [User]`` line that leaks the
operator's real name into every reply. It must **NEVER** surface in any
tenant-facing view or export. We strip it in two places:

- at **capture** (``core/capture.py``), so it never persists going forward;
- on **read** (detail/snapshot/export builders), so replies captured *before*
  this redaction existed are scrubbed too — no data migration required.

The whole line is dropped (not just the name): partial masking still leaks the
"Checked By" structure and any future name format. Match is case-insensitive
and tolerant of the leading glyph/spacing.
"""

import re

# Marks the bot's operator-attribution line, e.g. "⌿ Checked By : Richard [User]".
_CHECKED_BY = re.compile(r"(?i)\bchecked\s*by\b")


def redact_reply_text(text: str) -> str:
    """Return ``text`` with any "Checked By" attribution line removed.

    Drops matching lines entirely, preserving the rest verbatim (line order
    and content). Idempotent — safe to apply at capture and again on read.
    """
    if not text:
        return text
    kept = [line for line in text.splitlines() if not _CHECKED_BY.search(line)]
    return "\n".join(kept)
