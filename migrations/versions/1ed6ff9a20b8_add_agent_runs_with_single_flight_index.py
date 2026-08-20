"""add_agent_runs_with_single_flight_index

Revision ID: 1ed6ff9a20b8
Revises: 
Create Date: 2026-08-20 23:23:35.084812

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ed6ff9a20b8'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'agent_runs',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), nullable=False, index=True),
        sa.Column('profile_hash', sa.String(64), nullable=False),
        sa.Column('pending_profile_hash', sa.String(64), nullable=True),
        sa.Column('refresh_requested', sa.Boolean, server_default='0'),
        sa.Column('follow_up_count', sa.Integer, server_default='0'),
        sa.Column('status', sa.String(20), nullable=False, server_default='queued'),
        sa.Column('recommendation_id', sa.Integer, sa.ForeignKey('recommendations.id'), nullable=True),
        sa.Column('trigger_reason', sa.String(100), nullable=True),
        sa.Column('skip_reasons_json', sa.Text, nullable=True),
        sa.Column('candidate_scores_json', sa.Text, nullable=True),
        sa.Column('model_used', sa.String(100), nullable=True),
        sa.Column('tokens', sa.Integer, nullable=True),
        sa.Column('cost_usd', sa.Float, nullable=True),
        sa.Column('latency_ms', sa.Integer, nullable=True),
        sa.Column('retry_count', sa.Integer, server_default='0'),
        sa.Column('degraded', sa.Boolean, server_default='0'),
        sa.Column('lease_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP')),
    )
    # Partial unique index: only queued/running rows enforce single-flight per user
    op.create_index(
        'uq_single_flight',
        'agent_runs',
        ['user_id'],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index('uq_single_flight', table_name='agent_runs')
    op.drop_table('agent_runs')