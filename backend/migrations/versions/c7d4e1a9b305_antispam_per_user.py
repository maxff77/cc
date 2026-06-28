"""antispam per-user: user override column, drop plan antispam

Revision ID: c7d4e1a9b305
Revises: e9b3d6c1f2a4
Create Date: 2026-06-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d4e1a9b305'
down_revision: Union[str, Sequence[str], None] = 'e9b3d6c1f2a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-user antispam OVERRIDE (antispam-per-user feature). Nullable: NULL ⇒
    # the client falls back to the owner's global default (system_settings key
    # ``default_antispam_seconds``). The scheduler cooldown resolves as
    # coalesce(users.antispam_seconds, default). No backfill — every existing
    # client starts on the global default.
    op.add_column(
        'users',
        sa.Column('antispam_seconds', sa.Numeric(6, 2), nullable=True),
    )
    # Antispam is no longer a pricing-plan dimension; drop the now-unused column.
    # Per-plan values are intentionally discarded (the owner sets one global
    # default + per-user overrides instead).
    op.drop_column('plans', 'antispam_seconds')


def downgrade() -> None:
    # Re-add with a server_default so existing rows satisfy NOT NULL, then drop
    # the default to match the original (app-supplied) column shape.
    op.add_column(
        'plans',
        sa.Column(
            'antispam_seconds',
            sa.Numeric(6, 2),
            nullable=False,
            server_default=sa.text('4.0'),
        ),
    )
    op.alter_column('plans', 'antispam_seconds', server_default=None)
    op.drop_column('users', 'antispam_seconds')
