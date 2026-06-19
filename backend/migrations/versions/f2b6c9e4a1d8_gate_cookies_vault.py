"""gate-cookie vault (Phase 1) + category/session cookie_mode snapshot

Cookie-vault feature, Phase 1 (the vault only — no send/rotation/capture
changes). Three schema additions:

- ``gate_categories.cookie_mode`` — owner toggle: gates in this category run in
  "cookie mode" (the client stores per-account cookies for them).
- ``capture_sessions.cookie_mode`` — snapshot of the gate category's flag at
  session creation (the Phase-2 rotation reader will read THIS, never the live
  category). Both columns are NOT NULL DEFAULT false so existing rows backfill
  in one step (a server_default fills the populated table) and stay
  behaviorally unchanged.
- ``gate_cookies`` — the tenant-scoped vault: one PLAINTEXT credential per row
  (the CC precedent; encryption is Phase 2), deduped per ``(tenant, gate)`` by a
  unique index on the sha256 ``value_hash`` (the hash, not the value, sits in
  the btree — a cookie can exceed the ~2704-byte row limit). ``status`` is
  reserved for Phase-2 rotation (no reader yet).

Revision ID: f2b6c9e4a1d8
Revises: e1c7a4b9d2f0
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2b6c9e4a1d8'
down_revision: Union[str, Sequence[str], None] = 'e1c7a4b9d2f0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'gate_categories',
        sa.Column('cookie_mode', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.add_column(
        'capture_sessions',
        sa.Column('cookie_mode', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    )
    op.create_table(
        'gate_cookies',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('gate_id', sa.Integer(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('value_hash', sa.String(length=64), nullable=False),
        sa.Column('label', sa.String(length=80), nullable=True),
        sa.Column(
            'status', sa.String(length=10),
            server_default=sa.text("'active'"), nullable=False,
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False,
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_gate_cookies')),
        sa.ForeignKeyConstraint(
            ['tenant_id'], ['tenants.id'],
            name=op.f('fk_gate_cookies_tenant_id_tenants'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['gate_id'], ['gates.id'],
            name=op.f('fk_gate_cookies_gate_id_gates'),
        ),
    )
    op.create_index(
        op.f('ix_gate_cookies_tenant_id'), 'gate_cookies', ['tenant_id'], unique=False
    )
    op.create_index(
        'uq_gate_cookies_tenant_gate_hash',
        'gate_cookies',
        ['tenant_id', 'gate_id', 'value_hash'],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index('uq_gate_cookies_tenant_gate_hash', table_name='gate_cookies')
    op.drop_index(op.f('ix_gate_cookies_tenant_id'), table_name='gate_cookies')
    op.drop_table('gate_cookies')
    op.drop_column('capture_sessions', 'cookie_mode')
    op.drop_column('gate_categories', 'cookie_mode')
