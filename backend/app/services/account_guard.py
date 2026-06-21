"""Boot-time Telegram account-identity guard — the cross-tenant leak fence.

The whole attribution model assumes ONE Telegram account for the life of the
data: ``message_id`` is a per-chat sequence OWNED by that account, and
``core/attribution.py`` keys every reply on ``(chat_id, message_id)`` →
``send_log`` → tenant. Re-authing ``anon.session`` to a DIFFERENT account
RESTARTS those sequences, so a new reply's ``reply_to_msg_id`` can collide with
a stale ``send_log`` row and attribute the reply to ANOTHER tenant's line — a
cross-tenant data leak (CLAUDE.md critical invariant). The documented re-auth
runbook says wipe ``send_log``/``responses`` first; this guard makes forgetting
it FAIL-CLOSED instead of silently leaking.

A plain process restart is SAFE and stays silent: it reuses the same
``anon.session`` → same account → same id → ``ok``. Only a real account swap
changes the fingerprint.

The decision matrix (``_classify``) is a pure function so the security-critical
logic is tested without a DB or Telethon; ``decide_account_identity`` is the thin
IO wrapper the lifespan calls. The caller latches the watchdog on ``LOCKED``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Response, SendLog
from app.db.repos import system_settings as system_settings_repo

# system_settings key holding the last-seen account fingerprint.
ACCOUNT_ID_KEY = "telegram_account_id"

# Decisions returned to the lifespan.
SKIPPED = "skipped_unauthorized"      # no id to compare (unauthorized / lookup failed)
OK = "ok"                             # same account — proceed
FIRST_BOOT = "first_boot_recorded"    # no baseline yet — adopt this account
ADOPTED = "account_changed_adopted"   # account changed but NO historical data — safe to adopt
LOCKED = "account_changed_locked"     # account changed WITH historical data — caller must latch


def _classify(stored: str | None, current: str, has_data: bool) -> str:
    """Pure decision matrix. ``stored`` is the last-seen fingerprint (``None``
    on the very first boot), ``current`` the connected account's id, ``has_data``
    whether any ``send_log``/``responses`` rows exist (the only state a swap can
    mis-attribute). LOCKED never updates the stored id — so a careless restart
    re-latches rather than silently adopting the wrong account."""
    if stored is None:
        return FIRST_BOOT
    if stored == current:
        return OK
    return LOCKED if has_data else ADOPTED


async def _has_attribution_data(session: AsyncSession) -> bool:
    """Any ``send_log`` or ``responses`` row exists — the historical state a
    swapped account could mis-attribute. Cheap existence probe (LIMIT 1)."""
    for model in (SendLog, Response):
        row = (await session.execute(select(model.id).limit(1))).first()
        if row is not None:
            return True
    return False


async def decide_account_identity(
    session: AsyncSession, account_id: int | None
) -> str:
    """Compare the connected account against the stored fingerprint and record
    the baseline on the safe paths. Returns one of the module constants; the
    caller latches the watchdog iff it returns ``LOCKED``. Flush-not-commit —
    the caller owns the transaction."""
    if account_id is None:
        return SKIPPED
    current = str(account_id)
    stored = await system_settings_repo.get_value(session, ACCOUNT_ID_KEY)
    has_data = await _has_attribution_data(session)
    decision = _classify(stored, current, has_data)
    if decision in (FIRST_BOOT, ADOPTED):
        await system_settings_repo.set_value(session, ACCOUNT_ID_KEY, current)
    return decision
