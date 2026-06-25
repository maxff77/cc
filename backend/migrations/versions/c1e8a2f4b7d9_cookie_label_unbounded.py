"""gate_cookies.label — drop the 80-char cap (VARCHAR(80) -> TEXT)

The cookie label is display-only (shown next to the masked value) and is never
indexed — the dedup btree keys on ``value_hash``, not the label — so the 80-char
ceiling bought nothing and only surfaced as a Postgres truncation 500 when a
client pasted a longer note. Widen it to unbounded TEXT.

VARCHAR(80) -> TEXT is a metadata-only change in Postgres (no table rewrite, no
data loss), so it ships cleanly before the service restart. The downgrade casts
back to VARCHAR(80) and will fail if any row already exceeds 80 chars — expected
(you cannot un-widen lossily).

Revision ID: c1e8a2f4b7d9
Revises: b3f1a7c9d2e4
Create Date: 2026-06-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c1e8a2f4b7d9'
down_revision: Union[str, Sequence[str], None] = 'b3f1a7c9d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        'gate_cookies',
        'label',
        existing_type=sa.String(length=80),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        'gate_cookies',
        'label',
        existing_type=sa.Text(),
        type_=sa.String(length=80),
        existing_nullable=True,
    )
