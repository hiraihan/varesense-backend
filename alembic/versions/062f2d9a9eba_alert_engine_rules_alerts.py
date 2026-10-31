"""alert engine: rules + alerts

Revision ID: 062f2d9a9eba
Revises: 6e135edc54a5
Create Date: 2026-10-31 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '062f2d9a9eba'
down_revision: Union[str, Sequence[str], None] = '6e135edc54a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'alert_rules',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('rule_key', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('parameter', sa.String(length=64), nullable=False),
        sa.Column('operator', sa.String(length=8), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('asset_id', sa.UUID(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alert_rules_organization_id', 'alert_rules', ['organization_id'], unique=False)
    op.create_index('ix_alert_rules_rule_key', 'alert_rules', ['rule_key'], unique=False)

    op.create_table(
        'alerts',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('rule_id', sa.UUID(), nullable=False),
        sa.Column('asset_id', sa.UUID(), nullable=True),
        sa.Column('device_id', sa.UUID(), nullable=True),
        sa.Column('parameter', sa.String(length=64), nullable=False),
        sa.Column('value', sa.Float(), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('source', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('evidence', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['device_id'], ['edge_nodes.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['rule_id'], ['alert_rules.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_alerts_organization_id', 'alerts', ['organization_id'], unique=False)
    op.create_index('ix_alerts_rule_id', 'alerts', ['rule_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_alerts_rule_id', table_name='alerts')
    op.drop_index('ix_alerts_organization_id', table_name='alerts')
    op.drop_table('alerts')
    op.drop_index('ix_alert_rules_rule_key', table_name='alert_rules')
    op.drop_index('ix_alert_rules_organization_id', table_name='alert_rules')
    op.drop_table('alert_rules')
