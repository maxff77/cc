"""capture sessions and responses

Revision ID: 2faec0509cb8
Revises: ede0d6d7b7c6
Create Date: 2026-06-12 03:01:03.620601

Story 3.1: ``capture_sessions`` (the legacy active ``Sesion`` generalized per
tenant — one ACTIVE session per tenant, enforced by the partial unique index
``uq_capture_sessions_one_active_per_tenant``, same idiom as
``uq_batches_one_live_per_tenant``) + ``responses`` (full revisions AND
filtered CC rows in one table, discriminated by ``kind``; per-session CC dedup
DB-enforced by the partial unique index ``uq_responses_session_cc``) +
``batches.capture_session_id`` (the AC 3 binding at batch start; SET NULL —
the session outlives batch cleanup). Gate strings are snapshots, no FK to
``gates`` (2.1 design). Hand-written, mirrored in models.py so later
autogenerates diff empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2faec0509cb8'
down_revision: Union[str, Sequence[str], None] = 'ede0d6d7b7c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'capture_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('gate_value', sa.String(length=20), nullable=False),
        sa.Column('gate_name', sa.String(length=80), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_capture_sessions_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_capture_sessions')),
    )
    op.create_index(op.f('ix_capture_sessions_tenant_id'), 'capture_sessions', ['tenant_id'], unique=False)
    op.create_index(
        'uq_capture_sessions_one_active_per_tenant',
        'capture_sessions',
        ['tenant_id'],
        unique=True,
        postgresql_where=sa.text('is_active'),
    )
    op.create_table(
        'responses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('capture_session_id', sa.Integer(), nullable=False),
        sa.Column('batch_id', sa.Integer(), nullable=True),
        sa.Column('line_id', sa.Integer(), nullable=True),
        sa.Column('message_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('status', sa.String(length=10), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_responses_tenant_id_tenants'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['capture_session_id'], ['capture_sessions.id'], name=op.f('fk_responses_capture_session_id_capture_sessions'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['batch_id'], ['batches.id'], name=op.f('fk_responses_batch_id_batches'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['line_id'], ['batch_lines.id'], name=op.f('fk_responses_line_id_batch_lines'), ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_responses')),
    )
    op.create_index(op.f('ix_responses_tenant_id'), 'responses', ['tenant_id'], unique=False)
    op.create_index(op.f('ix_responses_capture_session_id'), 'responses', ['capture_session_id'], unique=False)
    op.create_index('ix_responses_message_id', 'responses', ['message_id'], unique=False)
    op.create_index(
        'uq_responses_session_cc',
        'responses',
        ['capture_session_id', 'text'],
        unique=True,
        postgresql_where=sa.text("kind = 'cc'"),
    )
    op.add_column('batches', sa.Column('capture_session_id', sa.Integer(), nullable=True))
    op.create_foreign_key(
        op.f('fk_batches_capture_session_id_capture_sessions'),
        'batches',
        'capture_sessions',
        ['capture_session_id'],
        ['id'],
        ondelete='SET NULL',
    )
    op.create_index(op.f('ix_batches_capture_session_id'), 'batches', ['capture_session_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_batches_capture_session_id'), table_name='batches')
    op.drop_constraint(op.f('fk_batches_capture_session_id_capture_sessions'), 'batches', type_='foreignkey')
    op.drop_column('batches', 'capture_session_id')
    op.drop_index('uq_responses_session_cc', table_name='responses')
    op.drop_index('ix_responses_message_id', table_name='responses')
    op.drop_index(op.f('ix_responses_capture_session_id'), table_name='responses')
    op.drop_index(op.f('ix_responses_tenant_id'), table_name='responses')
    op.drop_table('responses')
    op.drop_index('uq_capture_sessions_one_active_per_tenant', table_name='capture_sessions')
    op.drop_index(op.f('ix_capture_sessions_tenant_id'), table_name='capture_sessions')
    op.drop_table('capture_sessions')
