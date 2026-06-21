"""Display transform: simplify Amazon cookie-mode replies for client surfaces.

Pure module (no I/O, no DB, no Telethon/FastAPI imports). Called after
redact_reply_text, never instead of it. Admin cross-tenant view is excluded
by design — admins see raw redacted data for debugging.

Keyed off the session ``cookie_mode`` flag (the SAME signal the capture engine
uses to classify Amazon replies) — NOT the gate name — so it fires for the
cookie-mode gate regardless of what the owner named it. For Approved/Declined
replies we drop ONLY the bot's ``⌿ Response: …`` field (its prose copy +
``Removed:`` suffix carry no info the client needs) and keep everything else
verbatim — CC, Status, Gate, Total Time, etc. Anything else (cookie errors,
format help, plain edits) passes through unchanged. Verdict classification is
delegated to the owner-locked ``parse_amazon_verdict`` so we only ever touch a
reply the engine actually classified as a verdict.
"""

import re

from app.core.redact import (
    VERDICT_APPROVED,
    VERDICT_DECLINED,
    parse_amazon_verdict,
)

# The bot's ``⌿ Response: …`` field. Its value runs to the next ⌿/☇ separator or
# newline; an optional leading newline is eaten too so dropping the field leaves
# no blank line (works for both the multiline shape and the inline
# ``… ✅ ⌿ Response: ok`` shape).
_RESPONSE_RE = re.compile(r"\n?[ \t]*⌿\s*Response\s*:[^⌿☇\n]*")


def display_transform(text: str, cookie_mode: bool) -> str:
    if not text or not cookie_mode:
        return text
    kind, _token = parse_amazon_verdict(text)
    if kind not in (VERDICT_APPROVED, VERDICT_DECLINED):
        return text  # cookie errors, format help, plain edits → raw
    return _RESPONSE_RE.sub("", text)
