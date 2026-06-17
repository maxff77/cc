"""per-tenant credit balance + per-gate credit cost (+ plan grant, batch snapshot)

Credits feature. Four integer columns, all NOT NULL DEFAULT 0 so existing rows
backfill in one step (a column with a server_default fills the populated table):

- ``tenants.credit_balance`` — the tenant's spendable credits.
- ``gates.credit_cost``      — credits charged per captured ✅ on this gate.
- ``batches.gate_credit_cost`` — snapshot of the gate's cost at batch creation.
- ``plans.credits``          — credits granted to a tenant on assign/renew.

Default 0 keeps every existing tenant/gate/plan behaviorally unchanged: free
gates (cost 0) never charge, and nothing is gated until the owner sets a cost.

Revision ID: c5b8e1f9a2d4
Revises: a9d4e6c2f813
Create Date: 2026-06-17 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5b8e1f9a2d4'
down_revision: Union[str, Sequence[str], None] = 'a9d4e6c2f813'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'tenants',
        sa.Column('credit_balance', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'gates',
        sa.Column('credit_cost', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'batches',
        sa.Column('gate_credit_cost', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )
    op.add_column(
        'plans',
        sa.Column('credits', sa.Integer(), server_default=sa.text('0'), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('plans', 'credits')
    op.drop_column('batches', 'gate_credit_cost')
    op.drop_column('gates', 'credit_cost')
    op.drop_column('tenants', 'credit_balance')
