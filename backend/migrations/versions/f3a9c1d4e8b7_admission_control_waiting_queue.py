"""admission control waiting queue

Revision ID: f3a9c1d4e8b7
Revises: 2faec0509cb8
Create Date: 2026-06-12 00:02:31.000000

Story 4.2 (admission control): ``system_settings`` (owner-tunable runtime
key/value — first key ``max_active_senders``) and the widened partial unique
index ``uq_batches_one_live_per_tenant``: a batch queued for admission
(``state='waiting'``) IS the tenant's one live batch, so the predicate gains
'waiting'. Hand-written like ``1b606109cc99``: autogenerate does not capture
partial indexes reliably — the index is mirrored in ``Batch.__table_args__``
so later autogenerates diff empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9c1d4e8b7'
down_revision: Union[str, Sequence[str], None] = 'd7f4b2a91c63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_system_settings")),
    )
    # Widen the one-live-batch-per-tenant predicate with 'waiting'. Recreate:
    # ALTER INDEX cannot change a predicate.
    op.drop_index("uq_batches_one_live_per_tenant", table_name="batches")
    op.create_index(
        "uq_batches_one_live_per_tenant",
        "batches",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text(
            "state IN ('sending', 'paused', 'stopping', 'waiting')"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("uq_batches_one_live_per_tenant", table_name="batches")
    # Pre-clean (the 1b606109cc99 choreography): the narrow index cannot be
    # rebuilt while 'waiting' batches are live — without 4.2's code nothing
    # would ever promote them, so they are finalized as 'stopped'.
    op.execute("UPDATE batches SET state = 'stopped' WHERE state = 'waiting'")
    op.create_index(
        "uq_batches_one_live_per_tenant",
        "batches",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("state IN ('sending', 'paused', 'stopping')"),
    )
    op.drop_table("system_settings")
