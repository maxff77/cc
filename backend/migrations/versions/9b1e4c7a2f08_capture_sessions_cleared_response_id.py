"""capture_sessions.cleared_response_id — cockpit Limpiar view-cutoff

Adds a nullable ``cleared_response_id`` BigInteger to ``capture_sessions``. The
sessionless cockpit's "Limpiar" stamps it to ``MAX(responses.id)`` so the
display reads (and ONLY the display reads — cockpit/snapshot panels + the
cockpit export) hide every row with ``Response.id <= cleared_response_id``. It
is an ``id`` HIGH-WATER-MARK, not a timestamp (``Response.id`` is monotonic and
tie-immune, unlike the txn-start ``created_at``), and follows the ``hidden_at``
discipline: every integrity / attribution / reconciler / dedup / credit /
``awaiting_reply`` query ignores it, so Limpiar deletes ZERO ``responses`` rows.

Nullable, no backfill (NULL = nothing cleared, every pre-existing session).
Ships before the service restart.

Revision ID: 9b1e4c7a2f08
Revises: f6a2d9c4e1b7
Create Date: 2026-06-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9b1e4c7a2f08'
down_revision: Union[str, Sequence[str], None] = 'f6a2d9c4e1b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'capture_sessions',
        sa.Column('cleared_response_id', sa.BigInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('capture_sessions', 'cleared_response_id')
