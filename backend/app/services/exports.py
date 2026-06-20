""".txt generation from rows (Story 3.5) — the export half of FR18.

PURE module (string building only — no DB, no FastAPI, no I/O): the router
does the SELECTs and hands the rows in, same "core.py stays pure" spirit as
the legacy. Three recorded decisions live here:

1. **Format = legacy parity, VERBATIM.** ``completa_txt`` mirrors
   ``Sesion.guardar_respuesta`` (legacy core.py): one
   ``[YYYY-MM-DD HH:MM:SS] {text}\\n\\n`` block per 'full' revision, ascending.
   ``filtrada_txt`` mirrors the ``filtrada.txt`` write: one CC datum per line
   plus a final newline — exactly what ``cargar_cc_existentes`` knew how to
   re-read. No extra markers: each revision's text already carries its ✅/❌
   glyphs (capture derives ``status`` FROM the glyphs, not the other way).
2. **The rows ARE the view.** Callers pass ``list_full(limit=None)`` /
   ``list_cc(limit=None)`` ascending — the same complete data the Historial
   detail paints. Completa exports ALL revisions (❌ and re-captured edits
   included) because that IS the Completa view of the new model.
3. **Filename is ASCII-safe and the backend is its single authority.**
   ``export_filename`` ports the legacy ``prefijo_slug`` (``lstrip(".")``,
   spaces→``_``) hardened to ASCII: the ``Content-Disposition`` header must be
   latin-1 (starlette's HTTP limit) and a gate value may carry any printable
   non-space char (``_validate_gate_value``) — an ñ/emoji gate would break the
   response without the regex. Empty after hardening ⇒ ``"gate"``.
"""

import re

from app.core.display_transform import display_transform
from app.core.redact import redact_reply_text
from app.db.models import CaptureSession, Response

# Anything outside this set becomes "_" in the filename slug (latin-1-safe,
# shell-safe, header-safe).
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def completa_txt(rows: list[Response], cookie_mode: bool = False) -> str:
    """The Completa view as legacy ``completa.txt``: one timestamped block per
    'full' revision, in the ascending ``id`` order the rows arrive in.

    Timestamps are UTC exactly as stored (``created_at`` is tz-aware,
    server-default ``now()``) with no zone suffix — recorded decision: the
    legacy wrote the server's local time and the UI paints the browser's;
    they may differ and that is accepted at MVP scale (no timezone setting
    exists and creating one would violate the no-new-settings rule).

    Zero rows ⇒ ``""`` (honest empty file, never a 404).
    """
    return "".join(
        f"[{row.created_at.strftime('%Y-%m-%d %H:%M:%S')}] "
        f"{display_transform(redact_reply_text(row.text), cookie_mode)}\n\n"
        for row in rows
    )


def filtrada_txt(rows: list[Response]) -> str:
    """The Filtrada view as legacy ``filtrada.txt``: one CC datum per line,
    final newline included; zero rows ⇒ ``""``. No timestamps (parity — same
    reason ``SessionCcRow`` carries none)."""
    if not rows:
        return ""
    return "\n".join(row.text for row in rows) + "\n"


def export_filename(capture_session: CaptureSession, view: str) -> str:
    """``{slug}-{session_id}-{view}.txt`` (e.g. display ``Comando 01``, session
    42, filtrada ⇒ ``Comando_01-42-filtrada.txt``). The session id disambiguates
    — friendly names repeat and may carry any char.

    Slugged from the client-visible ``gate_display_value`` (NOT the real
    ``gate_value``): the download filename is a client surface and must not leak
    the real command. ``lstrip(".")`` is kept (harmless on display strings)."""
    slug = _SLUG_UNSAFE.sub(
        "_", capture_session.gate_display_value.lstrip(".")
    ).strip("_")
    return f"{slug or 'gate'}-{capture_session.id}-{view}.txt"
