"""gift_keys table + plans.is_default

Revision ID: d7c1a9e3f2b8
Revises: c5b8e1f9a2d4
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7c1a9e3f2b8'
down_revision: Union[str, Sequence[str], None] = 'c5b8e1f9a2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the gift-key default flag to plans + the (empty) gift_keys table.

    ``plans.is_default``: at most one plan is the gift-key "basic" tier — a
    partial unique index enforces it (the service clears the prior default
    before setting a new one to dodge the index). ``gift_keys`` ships empty;
    admins mint keys from /admin/keys. ``plan_id`` is RESTRICT (a referenced
    plan can't be deleted, snapshot honoured); the actor FKs are SET NULL so the
    audit trail survives the removal of the minting/claiming user.
    """
    op.add_column(
        'plans',
        sa.Column(
            'is_default',
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_index(
        'uq_plans_one_default',
        'plans',
        ['is_default'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )
    op.create_table(
        'gift_keys',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('days', sa.Integer(), nullable=False),
        sa.Column('plan_id', sa.Integer(), nullable=False),
        sa.Column(
            'status', sa.String(length=10),
            server_default=sa.text("'active'"), nullable=False,
        ),
        sa.Column('created_by_user_id', sa.Integer(), nullable=True),
        sa.Column('claimed_by_user_id', sa.Integer(), nullable=True),
        sa.Column('revoked_by_user_id', sa.Integer(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.Column('claimed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_gift_keys')),
        sa.ForeignKeyConstraint(
            ['plan_id'], ['plans.id'],
            name=op.f('fk_gift_keys_plan_id_plans'), ondelete='RESTRICT',
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'], ['users.id'],
            name=op.f('fk_gift_keys_created_by_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['claimed_by_user_id'], ['users.id'],
            name=op.f('fk_gift_keys_claimed_by_user_id_users'),
            ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['revoked_by_user_id'], ['users.id'],
            name=op.f('fk_gift_keys_revoked_by_user_id_users'),
            ondelete='SET NULL',
        ),
    )
    op.create_index(
        op.f('ix_gift_keys_code'), 'gift_keys', ['code'], unique=True
    )
    op.create_index(
        op.f('ix_gift_keys_plan_id'), 'gift_keys', ['plan_id'], unique=False
    )
    op.create_index(
        op.f('ix_gift_keys_created_by_user_id'),
        'gift_keys', ['created_by_user_id'], unique=False,
    )
    op.create_index(
        op.f('ix_gift_keys_claimed_by_user_id'),
        'gift_keys', ['claimed_by_user_id'], unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_gift_keys_claimed_by_user_id'), table_name='gift_keys')
    op.drop_index(op.f('ix_gift_keys_created_by_user_id'), table_name='gift_keys')
    op.drop_index(op.f('ix_gift_keys_plan_id'), table_name='gift_keys')
    op.drop_index(op.f('ix_gift_keys_code'), table_name='gift_keys')
    op.drop_table('gift_keys')
    op.drop_index('uq_plans_one_default', table_name='plans')
    op.drop_column('plans', 'is_default')
