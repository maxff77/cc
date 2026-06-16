"""Reply attribution (Story 3.1): ``reply_to_msg_id`` → (tenant, batch, line,
capture session).

Architecture component boundary: pure DB resolution — no telethon, no
requests. ``tenant_id`` is NEVER taken from a request here: the capture
pipeline runs outside requests and derives it from ``send_log`` (or from a
previous ``responses`` row).

ASSUMES one Telegram account for the lifetime of the data: both lookups key on
``(chat_id, message_id)`` — the message-id sequence is per-CHAT (supergroups
reuse ids across destinations), so ``chat_id`` (the marked peer id) namespaces
it. Re-authenticating ``anon.session`` as a DIFFERENT account restarts those
sequences — stale rows would then attribute new replies to other tenants. The
re-auth runbook MUST wipe that state first (``scripts/telegram_auth.py`` prints
the step).
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Batch
from app.db.repos import capture_sessions as capture_sessions_repo
from app.db.repos import responses as responses_repo
from app.db.repos import send_log as send_log_repo


@dataclass(frozen=True)
class Attribution:
    """Where a captured response belongs. ``batch_id``/``line_id`` may be
    ``None`` (batch cleanup SET-NULLed them on a previous revision) — the
    capture session is the real owner."""

    tenant_id: int
    batch_id: int | None
    line_id: int | None
    capture_session_id: int


async def resolve(
    session: AsyncSession,
    *,
    chat_id: int,
    message_id: int,
    reply_to_msg_id: int | None,
) -> Attribution | None:
    """Resolve a bot message to its owner. Resolution order (AC 4/5/7):

    1. A previous ``responses`` row for this ``(chat_id, message_id)`` → reuse
       its full attribution. EDITS keep their attribution this way even if
       something deletes the ``send_log`` row (AC 5: "message_id is preserved
       so attribution holds").
    2. ``(chat_id, reply_to_msg_id)`` → ``send_log`` → the batch's bound capture
       session. A pre-3.1 batch (``capture_session_id`` NULL) resolves via
       ``resolve_for_backfill`` with the batch's own gate snapshots, and the
       binding is BACKFILLED (recorded decision: late replies to old batches
       are not lost). The backfill NEVER changes which session is active
       (review 3-1): exact gate match reuses the active session, anything
       else gets an INACTIVE fallback — activation stays an API-only act.
    3. Nothing matched → ``None`` (the caller logs it to the
       unmatched-replies bucket, AC 7).

    🔒 Both lookups key on ``chat_id`` because Telegram message ids are
    per-chat, not account-global: with multi-destination sending the same id
    is reused across supergroups, so a bare-id match would attribute a reply to
    the wrong chat's line — and across tenants is a data leak.
    """
    previous = await responses_repo.last_full_revision(
        session, chat_id=chat_id, message_id=message_id
    )
    if previous is not None:
        return Attribution(
            tenant_id=previous.tenant_id,
            batch_id=previous.batch_id,
            line_id=previous.line_id,
            capture_session_id=previous.capture_session_id,
        )

    if reply_to_msg_id is None:
        return None
    record = await send_log_repo.get_by_chat_and_message_id(
        session, chat_id, reply_to_msg_id
    )
    if record is None:
        return None
    batch = await session.get(Batch, record.batch_id)
    if batch is None:  # deleted between the send and this reply (tenant gone)
        return None
    capture_session_id = batch.capture_session_id
    if capture_session_id is None:
        capture_session = await capture_sessions_repo.resolve_for_backfill(
            session, record.tenant_id, batch.gate_value, batch.gate_name
        )
        batch.capture_session_id = capture_session.id
        await session.flush()
        capture_session_id = capture_session.id
    return Attribution(
        tenant_id=record.tenant_id,
        batch_id=record.batch_id,
        line_id=record.line_id,
        capture_session_id=capture_session_id,
    )
