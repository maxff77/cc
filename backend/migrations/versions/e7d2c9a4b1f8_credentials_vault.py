"""credentials: tenant-scoped email+password vault

Revision ID: e7d2c9a4b1f8
Revises: d1f4a8e2c5b6
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e7d2c9a4b1f8'
down_revision: Union[str, Sequence[str], None] = 'd1f4a8e2c5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('credentials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tenant_id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password', sa.Text(), nullable=False),
    sa.Column('used', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], name=op.f('fk_credentials_tenant_id_tenants'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_credentials'))
    )
    op.create_index(op.f('ix_credentials_tenant_id'), 'credentials', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_credentials_tenant_id'), table_name='credentials')
    op.drop_table('credentials')
