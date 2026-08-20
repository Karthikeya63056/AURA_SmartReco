"""add_user_token_version_and_product_hashes

Revision ID: d73730cf1020
Revises: f302aef27b78
Create Date: 2026-08-20 23:25:28.491610

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd73730cf1020'
down_revision: Union[str, Sequence[str], None] = 'f302aef27b78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('token_version', sa.Integer, server_default='0', nullable=False))
    with op.batch_alter_table('products') as batch_op:
        batch_op.add_column(sa.Column('embedding_hash', sa.String(64), nullable=True))
        batch_op.add_column(sa.Column('payload_hash', sa.String(64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('products') as batch_op:
        batch_op.drop_column('payload_hash')
        batch_op.drop_column('embedding_hash')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('token_version')