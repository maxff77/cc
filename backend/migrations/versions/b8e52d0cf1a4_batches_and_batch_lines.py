"""batches and batch_lines

Revision ID: b8e52d0cf1a4
Revises: a3d41c9be7f0
Create Date: 2026-06-11 22:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e52d0cf1a4'
down_revision: Union[str, Sequence[str], None] = 'a3d41c9be7f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'batches',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('gate_value', sa.String(length=20), nullable=False),
        sa.Column('gate_name', sa.String(length=80), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('is_owner_priority', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_batches_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_batches')),
    )
    op.create_index(op.f('ix_batches_tenant_id'), 'batches', ['tenant_id'], unique=False)
    op.create_table(
        'batch_lines',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('state', sa.String(length=20), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_batch_lines_batch_id_batches'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_batch_lines_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_batch_lines')),
        sa.UniqueConstraint('batch_id', 'position', name=op.f('uq_batch_lines_batch_id_position')),
    )
    op.create_index(op.f('ix_batch_lines_batch_id'), 'batch_lines', ['batch_id'], unique=False)
    op.create_index(op.f('ix_batch_lines_tenant_id'), 'batch_lines', ['tenant_id'], unique=False)
    op.create_index('ix_batch_lines_batch_id_state', 'batch_lines', ['batch_id', 'state'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_batch_lines_batch_id_state', table_name='batch_lines')
    op.drop_index(op.f('ix_batch_lines_tenant_id'), table_name='batch_lines')
    op.drop_index(op.f('ix_batch_lines_batch_id'), table_name='batch_lines')
    op.drop_table('batch_lines')
    op.drop_index(op.f('ix_batches_tenant_id'), table_name='batches')
    op.drop_table('batches')
