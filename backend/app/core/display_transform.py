"""Display transform: simplify Amazon cookie-mode replies for client surfaces.

Pure module (no I/O, no DB, no Telethon/FastAPI imports). Called after
redact_reply_text, never instead of it. Admin cross-tenant view is excluded
by design — admins see raw redacted data for debugging.

Keyed off the session ``cookie_mode`` flag (the SAME signal the capture engine
uses to classify Amazon replies) — NOT the gate name — so it fires for the
cookie-mode gate regardless of what the owner named it. Approved/Declined
replies collapse to just the canonical status line; anything else (cookie
errors, format help, plain edits) passes through unchanged. Verdict
classification is delegated to the owner-locked ``parse_amazon_verdict`` so the
displayed status can never disagree with the verdict the engine stored.
"""

from app.core.redact import (
    VERDICT_APPROVED,
    VERDICT_DECLINED,
    parse_amazon_verdict,
)


def display_transform(text: str, cookie_mode: bool) -> str:
    if not text or not cookie_mode:
        return text
    kind, _token = parse_amazon_verdict(text)
    if kind == VERDICT_APPROVED:
        return "⌿ Status: Approved ✅"
    if kind == VERDICT_DECLINED:
        return "⌿ Status: Declined ❌"
    return text
