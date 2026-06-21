"""send_log.reply_purged_at — stop the reconciler resurrecting purged history

A Historial delete (``api/history``) removes ``responses`` rows but, by
invariant, leaves ``send_log`` intact. The reply reconciler keys "awaiting a
reply" on the ABSENCE of a ``kind='full'`` response for a line, so a deleted
message looked awaiting again and was re-fetched from Telegram and re-inserted
within one ~45s pass (the user saw deleted history reappear on refresh). This
nullable timestamp tombstones the line's ``send_log`` row on delete;
``awaiting_sent_keys`` / ``count_awaiting_beyond_window`` skip tombstoned rows.

Nullable, no backfill (NULL = not purged, every pre-existing row). Ships before
the service restart.

Revision ID: c4e2f7a1b903
Revises: 9b1e4c7a2f08
Create Date: 2026-06-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4e2f7a1b903'
down_revision: Union[str, Sequence[str], None] = '9b1e4c7a2f08'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'send_log',
        sa.Column('reply_purged_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('send_log', 'reply_purged_at')
