"""add_vector_outbox

Revision ID: f302aef27b78
Revises: 1ed6ff9a20b8
Create Date: 2026-08-20 23:24:48.120705

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f302aef27b78'
down_revision: Union[str, Sequence[str], None] = '1ed6ff9a20b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'vector_outbox',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('product_id', sa.Integer, sa.ForeignKey('products.id'), nullable=False),
        sa.Column('op', sa.String(20), nullable=False),
        sa.Column('embedding_hash', sa.String(64), nullable=True),
        sa.Column('payload_hash', sa.String(64), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('attempts', sa.Integer, server_default='0'),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    op.create_index('ix_vector_outbox_claim', 'vector_outbox', ['status', 'next_attempt_at'])


def downgrade() -> None:
    op.drop_index('ix_vector_outbox_claim', table_name='vector_outbox')
    op.drop_table('vector_outbox')