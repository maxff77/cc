"""batch priority tier (owner > admin > client)

Revision ID: a1b2c3d4e5f6
Revises: f3a9c1d4e8b7
Create Date: 2026-06-12 13:10:00.000000

Replaces the binary ``batches.is_owner_priority`` (bool) with a 3-tier
``batches.priority`` (smallint): 0=client, 1=admin, 2=owner — higher sends
first. The scheduler (Story 2.4, now generalized) consumes it to rank
owner > admin > client. Backfill maps the old owner flag to tier 2; everything
else (admins were indistinguishable from clients before) becomes tier 0 — old
admin batches are historical/terminal, so their tier is moot.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f3a9c1d4e8b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "batches",
        sa.Column(
            "priority",
            sa.SmallInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute("UPDATE batches SET priority = 2 WHERE is_owner_priority")
    op.drop_column("batches", "is_owner_priority")


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column(
        "batches",
        sa.Column(
            "is_owner_priority",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.execute("UPDATE batches SET is_owner_priority = (priority = 2)")
    op.drop_column("batches", "priority")
