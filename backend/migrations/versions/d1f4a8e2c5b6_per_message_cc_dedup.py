"""per-message CC dedup (Datos CC mirrors Aprobadas)

Revision ID: d1f4a8e2c5b6
Revises: c4e2f7a1b903
Create Date: 2026-06-22 00:00:00.000000

Swap the CC dedup scope from tenant-lifetime to PER-MESSAGE so "Datos CC"
mirrors "Aprobadas" one-row-per-approved-card. The old partial unique index
``uq_responses_session_cc(capture_session_id, text)`` collapsed the same CC
value across every message of the perpetual session; the new
``uq_responses_session_msg_cc(capture_session_id, chat_id, message_id, text)``
only dedups the SAME message's revisions/retries, so an identical CC seen on
two distinct approved messages now lands twice (matching the approved count).

Safe on existing data: the old index already guaranteed (session, text) unique
among 'cc' rows, so no (session, chat, message, text) tuple can be duplicated.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1f4a8e2c5b6"
down_revision: Union[str, Sequence[str], None] = "c4e2f7a1b903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_index("uq_responses_session_cc", table_name="responses")
    op.create_index(
        "uq_responses_session_msg_cc",
        "responses",
        ["capture_session_id", "chat_id", "message_id", "text"],
        unique=True,
        postgresql_where=sa.text("kind = 'cc'"),
    )


def downgrade() -> None:
    """Downgrade schema.

    NOTE: re-creating the old tenant-lifetime index can FAIL if per-message
    data accumulated duplicate (capture_session_id, text) 'cc' rows while this
    revision was live. De-duplicate before downgrading if that happens.
    """
    op.drop_index("uq_responses_session_msg_cc", table_name="responses")
    op.create_index(
        "uq_responses_session_cc",
        "responses",
        ["capture_session_id", "text"],
        unique=True,
        postgresql_where=sa.text("kind = 'cc'"),
    )
