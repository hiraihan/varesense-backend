"""machine-health analytics: baselines + anomalies

Revision ID: 07dd9c349662
Revises: 062f2d9a9eba
Create Date: 2026-10-31 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '07dd9c349662'
down_revision: Union[str, Sequence[str], None] = '062f2d9a9eba'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'baselines',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('asset_id', sa.UUID(), nullable=False),
        sa.Column('measurement_type', sa.String(length=64), nullable=False),
        sa.Column('placement', sa.String(length=255), nullable=True),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('operating_context', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('n', sa.Integer(), nullable=False),
        sa.Column('mean', sa.Float(), nullable=False),
        sa.Column('std', sa.Float(), nullable=False),
        sa.Column('min_value', sa.Float(), nullable=False),
        sa.Column('max_value', sa.Float(), nullable=False),
        sa.Column('method_version', sa.String(length=32), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_baselines_asset_id', 'baselines', ['asset_id'], unique=False)
    op.create_index('ix_baselines_organization_id', 'baselines', ['organization_id'], unique=False)

    op.create_table(
        'anomalies',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('baseline_id', sa.UUID(), nullable=False),
        sa.Column('asset_id', sa.UUID(), nullable=False),
        sa.Column('device_id', sa.UUID(), nullable=True),
        sa.Column('measurement_type', sa.String(length=64), nullable=False),
        sa.Column('measured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('z_score', sa.Float(), nullable=False),
        sa.Column('gradual', sa.Boolean(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('source', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['baseline_id'], ['baselines.id']),
        sa.ForeignKeyConstraint(['device_id'], ['edge_nodes.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_anomalies_asset_id', 'anomalies', ['asset_id'], unique=False)
    op.create_index('ix_anomalies_baseline_id', 'anomalies', ['baseline_id'], unique=False)
    op.create_index('ix_anomalies_organization_id', 'anomalies', ['organization_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_anomalies_organization_id', table_name='anomalies')
    op.drop_index('ix_anomalies_baseline_id', table_name='anomalies')
    op.drop_index('ix_anomalies_asset_id', table_name='anomalies')
    op.drop_table('anomalies')
    op.drop_index('ix_baselines_organization_id', table_name='baselines')
    op.drop_index('ix_baselines_asset_id', table_name='baselines')
    op.drop_table('baselines')
