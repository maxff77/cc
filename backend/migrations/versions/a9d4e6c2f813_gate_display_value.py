"""gate display_value (Comando visible) + batch/capture snapshots

Adds the owner-authored client-visible string ``display_value`` to ``gates``
and snapshots it onto ``batches``/``capture_sessions`` (same denormalize idiom
as ``gate_value``/``gate_name``). All three are NOT NULL; existing rows are
backfilled from the real command (``value`` / ``gate_value``) so nothing breaks
before the owner edits the new field.

Revision ID: a9d4e6c2f813
Revises: f4a1b9c2d7e3
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a9d4e6c2f813'
down_revision: Union[str, Sequence[str], None] = 'f4a1b9c2d7e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) Add nullable, 2) backfill from the real command, 3) enforce NOT NULL —
    # the standard three-step for a required column on a populated table.
    op.add_column('gates', sa.Column('display_value', sa.String(length=80), nullable=True))
    op.add_column('batches', sa.Column('gate_display_value', sa.String(length=80), nullable=True))
    op.add_column('capture_sessions', sa.Column('gate_display_value', sa.String(length=80), nullable=True))

    op.execute('UPDATE gates SET display_value = value WHERE display_value IS NULL')
    op.execute('UPDATE batches SET gate_display_value = gate_value WHERE gate_display_value IS NULL')
    op.execute('UPDATE capture_sessions SET gate_display_value = gate_value WHERE gate_display_value IS NULL')

    op.alter_column('gates', 'display_value', existing_type=sa.String(length=80), nullable=False)
    op.alter_column('batches', 'gate_display_value', existing_type=sa.String(length=80), nullable=False)
    op.alter_column('capture_sessions', 'gate_display_value', existing_type=sa.String(length=80), nullable=False)


def downgrade() -> None:
    op.drop_column('capture_sessions', 'gate_display_value')
    op.drop_column('batches', 'gate_display_value')
    op.drop_column('gates', 'display_value')
