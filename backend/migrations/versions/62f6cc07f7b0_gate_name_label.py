"""gate name label

Revision ID: 62f6cc07f7b0
Revises: 64cfd2bc35ff
Create Date: 2026-06-12 04:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62f6cc07f7b0'
down_revision: Union[str, Sequence[str], None] = '64cfd2bc35ff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the required ``name`` label to gates.

    Added nullable first so existing rows survive, backfilled from ``value``
    (the only sensible default for already-created gates), then made NOT NULL.
    """
    op.add_column('gates', sa.Column('name', sa.String(length=80), nullable=True))
    op.execute('UPDATE gates SET name = value WHERE name IS NULL')
    op.alter_column('gates', 'name', nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('gates', 'name')
