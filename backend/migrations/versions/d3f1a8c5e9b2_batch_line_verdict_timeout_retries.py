"""amazon gate Phase 2 hardening — durable verdict-timeout retry budget

The cookie-mode verdict-timeout retry-once budget used to live in a process-
memory set (``send_worker._timeout_retried``), reset on restart — so a crash
loop around the 90s timeout granted a fresh retry (and a fresh ``.cookie``+
``.amz`` resend on the shared account) per restart instead of pausing after the
single mandated retry. Persist it as ``batch_lines.verdict_timeout_retries``
(0 = fresh, >=1 = the one retry already burned) so the sweep + boot recovery
read durable state.

NOT NULL with ``server_default='0'`` is deploy-safe in the pre-restart window:
the OLD code's INSERTs omit the column and the DB fills the default, so no 500
(same stance as the existing ``batches`` SmallInteger counter).

Revision ID: d3f1a8c5e9b2
Revises: a7c3e9f1b204
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f1a8c5e9b2'
down_revision: Union[str, Sequence[str], None] = 'a7c3e9f1b204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'batch_lines',
        sa.Column(
            'verdict_timeout_retries',
            sa.SmallInteger(),
            nullable=False,
            server_default='0',
        ),
    )


def downgrade() -> None:
    op.drop_column('batch_lines', 'verdict_timeout_retries')
