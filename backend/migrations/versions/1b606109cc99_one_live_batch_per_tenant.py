"""one live batch per tenant

Revision ID: 1b606109cc99
Revises: b8e52d0cf1a4
Create Date: 2026-06-12 00:01:15.957532

Partial unique index enforcing the "one live batch per tenant" invariant
(Story 2.3, absorbing the 2.2 review finding). Hand-written: autogenerate
does not capture partial indexes reliably — the index is mirrored in
``Batch.__table_args__`` so later autogenerates diff empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b606109cc99'
down_revision: Union[str, Sequence[str], None] = 'b8e52d0cf1a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Pre-clean: if the 2.2 concurrency bug ever produced two live batches for
    # one tenant, the unique index below would fail to build. Keep the newest
    # live batch (highest id) per tenant; older live duplicates -> 'stopped'.
    op.execute(
        """
        UPDATE batches
        SET state = 'stopped'
        WHERE state IN ('sending', 'paused', 'stopping')
          AND id NOT IN (
              SELECT MAX(id)
              FROM batches
              WHERE state IN ('sending', 'paused', 'stopping')
              GROUP BY tenant_id
          )
        """
    )
    op.create_index(
        "uq_batches_one_live_per_tenant",
        "batches",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('sending', 'paused', 'stopping')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_batches_one_live_per_tenant", table_name="batches")
