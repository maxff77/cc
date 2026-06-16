"""send_log + responses chat_id — per-chat message-id namespacing

Revision ID: b7d3f9a2c1e8
Revises: e2f5a7c9d1b4
Create Date: 2026-06-16 00:00:00.000000

Attribution keyed on message_id alone broke once multi-target sending added
supergroup destinations: a supergroup's message_id sequence is per-chat (starts
at 1), so the SAME id is reused across every CC group and `reply_to_msg_id`
collides — replies mis-attribute or never clear their originating send. This
adds `chat_id` (the marked peer id of the destination) to both `send_log` and
`responses` so the key becomes the (chat_id, message_id) PAIR.

Additive only: `chat_id` is NULLable. Pre-existing rows stay NULL — their
destination was never recorded and cannot be reconstructed, so their replies
remain unattributable (the fix is forward-looking). No backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d3f9a2c1e8'
down_revision: Union[str, Sequence[str], None] = 'e2f5a7c9d1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # BigInteger: marked supergroup/channel peer ids (-100…) overflow int4.
    op.add_column('send_log', sa.Column('chat_id', sa.BigInteger(), nullable=True))
    op.add_column('responses', sa.Column('chat_id', sa.BigInteger(), nullable=True))
    # The hot attribution lookup: (chat_id, reply_to_msg_id) → send_log row, and
    # the per-message state lookup on responses — both namespaced per chat.
    op.create_index(
        'ix_send_log_chat_message', 'send_log', ['chat_id', 'message_id']
    )
    op.create_index(
        'ix_responses_chat_message', 'responses', ['chat_id', 'message_id']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_responses_chat_message', table_name='responses')
    op.drop_index('ix_send_log_chat_message', table_name='send_log')
    op.drop_column('responses', 'chat_id')
    op.drop_column('send_log', 'chat_id')
