"""credentials: drop tenant scoping → one global vault

Revision ID: b3f1a7c9d2e4
Revises: f4b9c2e7a1d3
Create Date: 2026-06-23 00:00:00.000000

Owner decision (2026-06-23): the vault is single-operator, so it becomes ONE
flat global table. Dropping ``tenant_id`` also makes every pre-refactor row —
created while the feature was session-scoped, then stranded under the caller's
real tenant once the API-key refactor pointed new writes at a dedicated vault
tenant — visible again through the API. That IS the data-recovery the owner
asked for: no rows are moved, the partition is simply removed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f1a7c9d2e4'
down_revision: Union[str, Sequence[str], None] = 'f4b9c2e7a1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        'fk_credentials_tenant_id_tenants', 'credentials', type_='foreignkey'
    )
    op.drop_index('ix_credentials_tenant_id', table_name='credentials')
    op.drop_column('credentials', 'tenant_id')


def downgrade() -> None:
    # Re-add nullable, no backfill — the tenant partition is gone for good.
    op.add_column(
        'credentials', sa.Column('tenant_id', sa.Integer(), nullable=True)
    )
    op.create_index(
        'ix_credentials_tenant_id', 'credentials', ['tenant_id'], unique=False
    )
    op.create_foreign_key(
        'fk_credentials_tenant_id_tenants', 'credentials', 'tenants',
        ['tenant_id'], ['id'], ondelete='CASCADE',
    )
