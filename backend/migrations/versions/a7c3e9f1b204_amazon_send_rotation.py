"""amazon gate — serialized send + cookie rotation (Phase 2) schema

Cookie-vault feature, Phase 2 (the SEND FLOW + ROTATION serialize gate). The
Phase-1 vault (``gate_cookies`` + ``cookie_mode`` snapshots) already shipped in
``f2b6c9e4a1d8``; this migration only adds the columns the serialize gate +
attempt-fence + rotation DB layer need. Every column is nullable (backfill is
NULL, no behavior change on existing rows) so it ships before the service
restart.

``batches`` gains the cookie-mode serialize gate / attempt-fence:

- ``awaiting_verdict_until`` (timestamptz) — while set and in the future,
  ``active_senders`` excludes the tenant (the serialize hold), resolved against
  DB ``now()``.
- ``awaiting_message_id`` / ``awaiting_chat_id`` (bigint) — the ``.amz``
  ``(chat_id, message_id)`` the worker is awaiting (the attempt-fence: a
  rotation/timeout resend is a NEW message_id for the SAME line).
- ``pause_reason`` (varchar(40)) — ``'cookies_exhausted'`` /
  ``'verdict_timeout'`` discriminator on an ordinary paused batch.
- ``gate_id`` (int) — SNAPSHOT of the gate id at cookie-mode batch creation,
  keys the active-cookie pick ``(tenant_id, gate_id)``. NO FK, by design (same
  stance as ``gate_value``/``gate_name``).

``batch_lines`` gains ``failed_cookie_id`` (FK ``gate_cookies.id``, nullable,
no relationship) — the cookie that produced a line's last dead verdict.

``gate_cookies`` gains a composite index ``(tenant_id, gate_id, status, id)``
for the FIFO active-cookie pick (``get_active_for_rotation``: oldest
``status='active'`` by ``id ASC``) — keeps it off a full partition scan.

Revision ID: a7c3e9f1b204
Revises: f2b6c9e4a1d8
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1b204'
down_revision: Union[str, Sequence[str], None] = 'f2b6c9e4a1d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'batches',
        sa.Column('awaiting_verdict_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'batches',
        sa.Column('awaiting_message_id', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'batches',
        sa.Column('awaiting_chat_id', sa.BigInteger(), nullable=True),
    )
    op.add_column(
        'batches',
        sa.Column('pause_reason', sa.String(length=40), nullable=True),
    )
    op.add_column(
        'batches',
        sa.Column('gate_id', sa.Integer(), nullable=True),
    )
    op.add_column(
        'batch_lines',
        sa.Column('failed_cookie_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        op.f('fk_batch_lines_failed_cookie_id_gate_cookies'),
        'batch_lines',
        'gate_cookies',
        ['failed_cookie_id'],
        ['id'],
    )
    # FIFO active-cookie pick: oldest status='active' by id ASC for
    # (tenant_id, gate_id). The composite index keeps get_active_for_rotation
    # off a full partition scan.
    op.create_index(
        'ix_gate_cookies_tenant_gate_status_id',
        'gate_cookies',
        ['tenant_id', 'gate_id', 'status', 'id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_gate_cookies_tenant_gate_status_id', table_name='gate_cookies')
    op.drop_constraint(
        op.f('fk_batch_lines_failed_cookie_id_gate_cookies'),
        'batch_lines',
        type_='foreignkey',
    )
    op.drop_column('batch_lines', 'failed_cookie_id')
    op.drop_column('batches', 'gate_id')
    op.drop_column('batches', 'pause_reason')
    op.drop_column('batches', 'awaiting_chat_id')
    op.drop_column('batches', 'awaiting_message_id')
    op.drop_column('batches', 'awaiting_verdict_until')
