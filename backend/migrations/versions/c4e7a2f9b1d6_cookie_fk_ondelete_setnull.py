"""amazon gate — cookie removal: failed_cookie_id FK ON DELETE SET NULL

The Phase-2 rotation migration ``a7c3e9f1b204`` created
``batch_lines.failed_cookie_id`` as a plain FK to ``gate_cookies.id`` with NO
``ondelete`` (Postgres default RESTRICT). The send worker stamps that column on
EVERY cookie-mode send (the cookie actually sent for the attempt), so any
already-sent cookie is referenced — and a hard DELETE of it (manual delete from
the vault, OR the rotation purging a dead cookie) raises ForeignKeyViolation.
That unmapped 500 is the "Ocurrió un error inesperado." behind the broken
delete button.

This migration drops and recreates the constraint with ``ON DELETE SET NULL``
so deleting a cookie auto-nulls the diagnostic reference on any line that points
at it (the id is audit-only — no behavior depends on its value surviving the
cookie). Constraint-only change; no column/data change. Ships before restart.

Revision ID: c4e7a2f9b1d6
Revises: d3f1a8c5e9b2
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c4e7a2f9b1d6'
down_revision: Union[str, Sequence[str], None] = 'd3f1a8c5e9b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = 'fk_batch_lines_failed_cookie_id_gate_cookies'


def upgrade() -> None:
    op.drop_constraint(op.f(_FK), 'batch_lines', type_='foreignkey')
    op.create_foreign_key(
        op.f(_FK),
        'batch_lines',
        'gate_cookies',
        ['failed_cookie_id'],
        ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint(op.f(_FK), 'batch_lines', type_='foreignkey')
    op.create_foreign_key(
        op.f(_FK),
        'batch_lines',
        'gate_cookies',
        ['failed_cookie_id'],
        ['id'],
    )
