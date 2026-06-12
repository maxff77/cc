"""watchdog state global pause latch

Revision ID: d7f4b2a91c63
Revises: 2faec0509cb8
Create Date: 2026-06-12 10:00:00.000000

Story 4.1: ``watchdog_state`` — single-row (id=1, app-enforced) durable latch
of the watchdog's GLOBAL send pause. No ``tenant_id`` on purpose (global
system state, same documented exception class as ``gates``). The in-process
singleton in ``core/watchdog.py`` is the operating authority; this row is
what ``load_persisted()`` restores at boot so a deploy/restart never resumes
sending on its own (AC 3: resuming is an explicit owner action — never
automatic). Hand-written, mirrored in models.py so later autogenerates diff
empty.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7f4b2a91c63'
down_revision: Union[str, Sequence[str], None] = '2d9609ffa4d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'watchdog_state',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('paused', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('reason', sa.String(length=40), nullable=True),
        sa.Column('detail', sa.Text(), nullable=True),
        sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_watchdog_state')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('watchdog_state')
