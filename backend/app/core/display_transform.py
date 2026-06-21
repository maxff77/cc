"""Display transform: rewrite Amazon cookie-mode verdicts for client surfaces.

Pure module (no I/O, no DB, no Telethon/FastAPI imports). Called after
redact_reply_text, never instead of it. Admin cross-tenant view is excluded
by design — admins see raw redacted data for debugging.

Keyed off the session ``cookie_mode`` flag (the SAME signal the capture engine
uses to classify Amazon replies) — NOT the gate name — so it fires for the
cookie-mode gate regardless of what the owner named it. An Approved verdict is
rewritten to a LIVE card and a Declined to a DEAD card: a fixed branded
template (CC value kept, the bot's prose/Status/Gate/Time fields replaced).
Anything else (cookie errors, format help, plain edits) passes through
unchanged. Verdict classification is delegated to the owner-locked
``parse_amazon_verdict`` so we only ever rewrite a reply the engine actually
classified as an Approved/Declined verdict.
"""

from app.core.cc_extract import extract_cc
from app.core.redact import (
    VERDICT_APPROVED,
    VERDICT_DECLINED,
    parse_amazon_verdict,
)

# Fixed branded templates, in the legacy field order. The CC line is prepended
# per-reply with the card extracted from the bot reply.
_LIVE_LINES = (
    "- Status: LIVE 100% ✅",
    "- Details: Tarjeta apta / Card successfully linked",
    "- Response: Process completed successfully ✅",
    "- System: Ranger Validation Engine",
)
_DEAD_LINES = (
    "- Status: DEAD ❌",
    "- Details: No apta / Not eligible",
    "- Response: Delete complete / Eliminación completada ✅",
    "- System: Ranger Validation Engine",
)


def _render(text: str, lines: tuple[str, ...]) -> str:
    # The card from the bot reply's ``CC:`` line. extract_cc truncates at
    # ``Status`` but an inline Approved shape (``…|456 ⌿ Status: …``) leaves the
    # bare ``⌿``/``☇`` separator welded on — strip it so the card reads clean.
    cards = extract_cc(text)
    card = cards[0].strip(" \t⌿☇") if cards else ""
    cc_line = f"CC: {card}".rstrip()
    return "\n".join((cc_line, *lines))


def display_transform(text: str, cookie_mode: bool) -> str:
    if not text or not cookie_mode:
        return text
    kind, _token = parse_amazon_verdict(text)
    if kind == VERDICT_APPROVED:
        return _render(text, _LIVE_LINES)
    if kind == VERDICT_DECLINED:
        return _render(text, _DEAD_LINES)
    return text  # cookie errors, format help, plain edits → raw
