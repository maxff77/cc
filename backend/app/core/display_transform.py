"""Display transform: simplify Amazon gate replies for client-facing surfaces.

Pure module (no I/O, no DB, no Telethon/FastAPI imports). Called after
redact_reply_text, never instead of it. Admin cross-tenant view is excluded
by design — admins see raw redacted data for debugging.

Approved/Declined cookie-mode replies collapse to just the canonical status
line; anything else (cookie errors, format help, plain edits) passes through
unchanged. Verdict classification is delegated to the owner-locked
``parse_amazon_verdict`` so the displayed status can never disagree with the
verdict the engine stored.
"""

from app.core.redact import (
    VERDICT_APPROVED,
    VERDICT_DECLINED,
    parse_amazon_verdict,
)


def display_transform(text: str, gate_name: str | None) -> str:
    if not text:
        return text
    # ponytail: gate-name substring match is pre-existing; the authoritative
    # signal is the session cookie_mode flag, but that isn't threaded to the
    # display surfaces. Deferred — see deferred-work.md.
    if gate_name is None or "amz" not in gate_name.lower():
        return text

    kind, _token = parse_amazon_verdict(text)
    if kind == VERDICT_APPROVED:
        return "⌿ Status: Approved ✅"
    if kind == VERDICT_DECLINED:
        return "⌿ Status: Declined ❌"
    return text
