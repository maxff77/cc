"""Display transform: reformat Amazon gate replies for client-facing surfaces.

Pure module (no I/O, no DB, no Telethon/FastAPI imports). Called after
redact_reply_text, never instead of it. Admin cross-tenant view is excluded
by design — admins see raw redacted data for debugging.
"""

import re

_NORMALIZE_RE = re.compile(r"[☇⌿]")
_CC_RE = re.compile(r"(?i)\bCC\s*:\s*(\S+)")
_STATUS_RE = re.compile(r"(?i)Status\s*:\s*(.+)")
_TIME_RE = re.compile(r"(?i)Time\s*:\s*([\d.]+\s*s?)")


def display_transform(text: str, gate_name: str | None) -> str:
    if not text:
        return text
    if gate_name is None or "amz" not in gate_name.lower():
        return text
    return _amazon_transform(text)


def _amazon_transform(text: str) -> str:
    normalized = _NORMALIZE_RE.sub("\n", text)
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]

    cc_token: str | None = None
    status_text: str | None = None
    time_str: str | None = None

    for line in lines:
        if cc_token is None:
            m = _CC_RE.search(line)
            if m:
                cc_token = m.group(1)
        if status_text is None:
            m = _STATUS_RE.search(line)
            if m:
                status_text = m.group(1).strip()
        if time_str is None:
            m = _TIME_RE.search(line)
            if m:
                time_str = m.group(1).strip()

    if status_text is None:
        return "\n".join(lines)

    status_lower = status_text.lower()
    if status_lower.startswith("approved"):
        label = "Aprobada"
        glyph = "✅"
        body = "TARJETA VINCULADA LIVE 🌟"
    elif status_lower.startswith("declined"):
        label = "Rechazada"
        glyph = "❌"
        body = "TARJETA INVALIDA DEAD ➕"
    else:
        label = "No procesada"
        glyph = "❌"
        body = "COOKIE MUERTA ❌"

    if cc_token is not None:
        header = f"◈ {label} {glyph} — {cc_token}"
    else:
        header = f"◈ {label} {glyph}"

    if time_str is not None:
        header += f" · {time_str}"

    return f"{header}\n▸ {body}"
