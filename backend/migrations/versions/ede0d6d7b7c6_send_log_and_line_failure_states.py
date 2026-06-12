"""send log and line failure states

Revision ID: ede0d6d7b7c6
Revises: 1b606109cc99
Create Date: 2026-06-12 01:32:22.177050

Story 2.5: write-ahead ``send_log`` (one row per line, written by the send
worker, read by Story 3.1's attribution) + ``batch_lines.fail_code`` (the
machine-readable code the frontend maps to Spanish copy). The new line/batch
states ``'failed'``/``'cancelled'`` are plain Strings (2.2 decision — no DB
enum, so ZERO state-column ALTERs here) and ``'cancelled'`` is NOT live, so
the partial unique index ``uq_batches_one_live_per_tenant`` is untouched.
Hand-written, mirrored in models.py so later autogenerates diff empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ede0d6d7b7c6'
down_revision: Union[str, Sequence[str], None] = '1b606109cc99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'send_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('line_id', sa.Integer(), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_send_log_tenant_id_tenants'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_send_log_batch_id_batches'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['line_id'], ['batch_lines.id'], name=op.f('fk_send_log_line_id_batch_lines'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_send_log')),
        sa.UniqueConstraint('line_id', name=op.f('uq_send_log_line_id')),
    )
    op.create_index(op.f('ix_send_log_tenant_id'), 'send_log', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_send_log_batch_id'), 'send_log', ['batch_id'], unique=False)
    op.create_index('ix_send_log_message_id', 'send_log', ['message_id'], unique=False)
    op.add_column('batch_lines', sa.Column('fail_code', sa.String(length=40), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('batch_lines', 'fail_code')
    op.drop_index('ix_send_log_message_id', table_name='send_log')
    op.drop_index(op.f('ix_send_log_batch_id'), table_name='send_log')
    op.drop_index(op.f('ix_send_log_tenant_id'), table_name='send_log')
    op.drop_table('send_log')
