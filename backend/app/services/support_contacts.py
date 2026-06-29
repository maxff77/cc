"""Owner-managed Telegram support contacts (editable-support-contacts feature).

The handles clients see on ``/login``, the ``/expired`` lockout, and the in-app
"Soporte" link. Stored as ONE ``system_settings`` row (key below) holding a JSON
array of canonical handles — same owner-tunable, hot-from-the-UI, survives-restart
rationale as the configurable interval / admission cap / antispam default.

GLOBAL — no tenant. ``DEFAULT_HANDLES`` is the pre-feature behavior: until the
owner edits, the system returns exactly what ``frontend/config/site.ts`` shipped,
so there is nothing to migrate or boot-seed. The PUT route owns handle
normalization (the single ``_normalize_contact`` source of truth); this service
only stores/reads the already-canonical list and parses defensively (a
missing/garbage/empty row all mean "use the defaults").
"""

import json

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repos import system_settings as system_settings_repo

SUPPORT_CONTACTS_KEY = "support_contacts"
# Pre-feature defaults — identical to the handles previously hardcoded in
# ``frontend/config/site.ts``. Returned whenever the row is unset/unparseable so
# the support channel can never resolve to nothing.
DEFAULT_HANDLES: tuple[str, ...] = ("AionRanger", "AionRangerOwner")
# Sane upper bound so the owner can't bloat the login footer; enforced in the
# PUT route (kept here as the single source of the number).
MAX_SUPPORT_CONTACTS = 8


async def get_handles(session: AsyncSession) -> list[str]:
    """Resolve the active support handles, falling back to the defaults.

    Defensive: a missing row, malformed JSON, a non-list, a list with a
    non-string element, or an empty list all collapse to ``DEFAULT_HANDLES``.
    """
    raw = await system_settings_repo.get_value(session, SUPPORT_CONTACTS_KEY)
    if raw is None:
        return list(DEFAULT_HANDLES)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return list(DEFAULT_HANDLES)
    if (
        not isinstance(data, list)
        or not data
        or not all(isinstance(handle, str) and handle for handle in data)
    ):
        return list(DEFAULT_HANDLES)
    return data


async def set_handles(session: AsyncSession, handles: list[str]) -> None:
    """Persist the canonical handle list (caller normalizes; flush, no commit)."""
    await system_settings_repo.set_value(
        session, SUPPORT_CONTACTS_KEY, json.dumps(handles)
    )
