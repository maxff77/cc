"""Display transform: simplify Amazon cookie-mode replies for client surfaces.

Pure module (no I/O, no DB, no Telethon/FastAPI imports). Called after
redact_reply_text, never instead of it. Admin cross-tenant view is excluded
by design — admins see raw redacted data for debugging.

Keyed off the session ``cookie_mode`` flag (the SAME signal the capture engine
uses to classify Amazon replies) — NOT the gate name — so it fires for the
cookie-mode gate regardless of what the owner named it. Approved/Declined
replies are reduced to the ``CC`` line + the canonical status line; the bot's
``Response``/``Time``/``Removed`` copy is dropped. A Declined reply with no CC
line collapses to just the status line. Anything else (cookie errors, format
help, plain edits) passes through unchanged. Verdict classification is
delegated to the owner-locked ``parse_amazon_verdict`` so the displayed status
can never disagree with the verdict the engine stored.
"""

import re

from app.core.redact import (
    VERDICT_APPROVED,
    VERDICT_DECLINED,
    parse_amazon_verdict,
)

_NORMALIZE_RE = re.compile(r"[☇⌿]")
_CC_RE = re.compile(r"(?i)\bCC\s*:\s*(\S+)")

_STATUS_LINE = {
    VERDICT_APPROVED: "⌿ Status: Approved ✅",
    VERDICT_DECLINED: "⌿ Status: Declined ❌",
}


def display_transform(text: str, cookie_mode: bool) -> str:
    if not text or not cookie_mode:
        return text
    kind, _token = parse_amazon_verdict(text)
    status_line = _STATUS_LINE.get(kind)
    if status_line is None:
        return text  # cookie errors, format help, plain edits → raw

    # Keep the CC card, drop the bot's Response/Time/Removed copy. Normalize the
    # inline ☇/⌿ separators to newlines first so the CC token terminates before
    # the Status field (mirrors normalize_cookie_cc on the capture side).
    cc = _CC_RE.search(_NORMALIZE_RE.sub("\n", text))
    if cc is not None:
        return f"☇ CC: {cc.group(1)}\n{status_line}"
    return status_line
