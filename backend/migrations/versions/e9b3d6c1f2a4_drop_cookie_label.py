"""gate_cookies.label — drop the column (the Etiqueta field was removed)

The optional client-authored cookie label (the "Etiqueta" field in the vault
UI) was removed from the product — frontend input, API schema, and repo all
stopped reading/writing it. Drop the now-orphan column. It was display-only,
nullable, never indexed and carried no FK, so the drop is non-destructive to
every other table and ships before the service restart.

(The immediately-prior migration ``c1e8a2f4b7d9`` widened this column to TEXT;
that is now moot — kept in history because it already ran on prod.)

Revision ID: e9b3d6c1f2a4
Revises: c1e8a2f4b7d9
Create Date: 2026-06-25 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e9b3d6c1f2a4'
down_revision: Union[str, Sequence[str], None] = 'c1e8a2f4b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('gate_cookies', 'label')


def downgrade() -> None:
    # Restores the schema only — the dropped label values are unrecoverable.
    op.add_column(
        'gate_cookies',
        sa.Column('label', sa.Text(), nullable=True),
    )
