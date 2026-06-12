"""audit log

Revision ID: 2d9609ffa4d4
Revises: 2faec0509cb8
Create Date: 2026-06-12 07:33:49.989440

Story 3.6: ``audit_log`` — one row per cross-tenant support read (the single
place tenant isolation is intentionally crossed, architecture :248). FK
decisions (recorded in the model docstring): ``actor_user_id`` SET NULL (the
trail survives the admin's removal), ``tenant_id`` CASCADE (the support trail
dies with the TARGET tenant), ``capture_session_id`` deliberately WITHOUT FK
(historical reference — deleting the session never touches the record).
Hand-written, mirrored in models.py so later autogenerates diff empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d9609ffa4d4'
down_revision: Union[str, Sequence[str], None] = '2faec0509cb8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), nullable=True),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(length=40), nullable=False),
        sa.Column('capture_session_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_audit_log_actor_user_id_users'), ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_audit_log_tenant_id_tenants'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log')),
    )
    op.create_index(op.f('ix_audit_log_actor_user_id'), 'audit_log', ['actor_user_id'], unique=False)
    op.create_index(op.f('ix_audit_log_tenant_id'), 'audit_log', ['tenant_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_audit_log_tenant_id'), table_name='audit_log')
    op.drop_index(op.f('ix_audit_log_actor_user_id'), table_name='audit_log')
    op.drop_table('audit_log')
