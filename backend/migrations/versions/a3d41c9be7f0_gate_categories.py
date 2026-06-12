"""gate categories

Revision ID: a3d41c9be7f0
Revises: 62f6cc07f7b0
Create Date: 2026-06-11 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3d41c9be7f0'
down_revision: Union[str, Sequence[str], None] = '62f6cc07f7b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create ``gate_categories`` and give every gate a required category.

    Same backfill choreography as 62f6cc07f7b0 (name label): production has
    live gate rows, so ``category_id`` is added nullable, every existing gate
    is backfilled into a seed "General" category (the owner can rename it from
    the UI), then the column is made NOT NULL.
    """
    op.create_table(
        'gate_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_gate_categories')),
        sa.UniqueConstraint('name', name=op.f('uq_gate_categories_name')),
    )
    op.execute("INSERT INTO gate_categories (name) VALUES ('General')")
    op.add_column('gates', sa.Column('category_id', sa.Integer(), nullable=True))
    op.execute(
        "UPDATE gates SET category_id = "
        "(SELECT id FROM gate_categories WHERE name = 'General') "
        "WHERE category_id IS NULL"
    )
    op.alter_column('gates', 'category_id', nullable=False)
    op.create_index(op.f('ix_gates_category_id'), 'gates', ['category_id'], unique=False)
    op.create_foreign_key(
        op.f('fk_gates_category_id_gate_categories'),
        'gates',
        'gate_categories',
        ['category_id'],
        ['id'],
        ondelete='RESTRICT',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_gates_category_id_gate_categories'), 'gates', type_='foreignkey')
    op.drop_index(op.f('ix_gates_category_id'), table_name='gates')
    op.drop_column('gates', 'category_id')
    op.drop_table('gate_categories')
