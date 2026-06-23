"""gift keys: admin-chosen credits grant (days may be 0)

Revision ID: f4b9c2e7a1d3
Revises: e7d2c9a4b1f8
Create Date: 2026-06-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f4b9c2e7a1d3'
down_revision: Union[str, Sequence[str], None] = 'e7d2c9a4b1f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Credits a claim adds to the tenant's balance (gift-key-credits feature).
    # Default 0 ⇒ existing keys stay days-only; no backfill needed.
    op.add_column(
        'gift_keys',
        sa.Column(
            'credits', sa.Integer(), server_default=sa.text('0'), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column('gift_keys', 'credits')
