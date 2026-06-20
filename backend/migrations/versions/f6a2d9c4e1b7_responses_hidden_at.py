"""clear-declined — responses.hidden_at soft-hide marker

Adds a nullable ``hidden_at`` timestamp to ``responses``. The cockpit "Limpiar"
button soft-hides a session's declined (❌) 'full' revisions: the marked rows
are dropped from the Completa display/export reads (``list_full``/``full_count``)
but STILL counted by every integrity query (``responded_line_count``, the reply
reconciler's ``_answered_full_exists``, ``last_full_revision``,
``has_ok_revision``). A physical DELETE was rejected — the reconciler (45s / 72h
window) would see the line as unanswered and re-fetch the ❌ reply from Telegram,
resurrecting it, and the awaiting counter would spike. Soft-hide retains the row
for attribution while making it invisible to the operator.

Nullable, no backfill (NULL = visible, every pre-existing row). Ships before the
service restart.

Revision ID: f6a2d9c4e1b7
Revises: c4e7a2f9b1d6
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f6a2d9c4e1b7'
down_revision: Union[str, Sequence[str], None] = 'c4e7a2f9b1d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'responses',
        sa.Column('hidden_at', sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('responses', 'hidden_at')
