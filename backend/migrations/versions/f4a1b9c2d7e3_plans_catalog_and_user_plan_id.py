"""plans catalog + users.plan_id

Revision ID: f4a1b9c2d7e3
Revises: b7d3f9a2c1e8
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4a1b9c2d7e3'
down_revision: Union[str, Sequence[str], None] = 'b7d3f9a2c1e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create the (empty) ``plans`` catalog and link clients via ``users.plan_id``.

    No seed data — the table ships empty; the owner creates plans from
    ``/admin/plans``. ``users.plan_id`` is nullable (owner/admin and pre-plan
    clients carry no plan) with an ``ondelete=RESTRICT`` FK so a plan referenced
    by any user can never be deleted out from under a historical assignment.
    """
    op.create_table(
        'plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('price_usd', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('duration_days', sa.Integer(), nullable=False),
        sa.Column('antispam_seconds', sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column('max_lines_per_batch', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_plans')),
        sa.UniqueConstraint('name', name=op.f('uq_plans_name')),
    )
    op.add_column('users', sa.Column('plan_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_users_plan_id'), 'users', ['plan_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_users_plan_id_plans'),
        'users',
        'plans',
        ['plan_id'],
        ['id'],
        ondelete='RESTRICT',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_users_plan_id_plans'), 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_plan_id'), table_name='users')
    op.drop_column('users', 'plan_id')
    op.drop_table('plans')
