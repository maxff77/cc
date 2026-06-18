"""gate-category special_mode flag (+ capture-session snapshot)

Special-mode gate categories feature. Two boolean columns, NOT NULL DEFAULT
false so existing rows backfill in one step (a server_default fills the
populated table):

- ``gate_categories.special_mode`` — owner toggle: gates in this category
  capture in "special mode" (status from the ``Approveds! ✅: N`` count;
  Approveds!/Deads! stats stripped from the stored reply).
- ``capture_sessions.special_mode`` — snapshot of the gate category's flag at
  session creation (the capture pipeline reads THIS, never the live category).

Default false keeps every existing category/session behaviorally unchanged.

Revision ID: e1c7a4b9d2f0
Revises: d7c1a9e3f2b8
Create Date: 2026-06-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1c7a4b9d2f0'
down_revision: Union[str, Sequence[str], None] = 'd7c1a9e3f2b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'gate_categories',
        sa.Column('special_mode', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column(
        'capture_sessions',
        sa.Column('special_mode', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('capture_sessions', 'special_mode')
    op.drop_column('gate_categories', 'special_mode')
