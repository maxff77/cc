"""send_targets — multi-target sending

Revision ID: e2f5a7c9d1b4
Revises: c4e1a2b3d5f6
Create Date: 2026-06-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e2f5a7c9d1b4'
down_revision: Union[str, Sequence[str], None] = 'c4e1a2b3d5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'send_targets',
        sa.Column('id', sa.Integer(), nullable=False),
        # BigInteger: marked peer ids of supergroups/channels (-100…) overflow int4.
        sa.Column('chat_id', sa.BigInteger(), nullable=False),
        sa.Column('label', sa.String(length=80), nullable=False),
        sa.Column('enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_send_targets')),
        sa.UniqueConstraint('chat_id', name=op.f('uq_send_targets_chat_id')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('send_targets')
