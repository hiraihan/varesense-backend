"""maintenance records

Revision ID: a5c8e2f31b04
Revises: 87f1b29472d4
Create Date: 2026-10-31 23:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a5c8e2f31b04'
down_revision: Union[str, Sequence[str], None] = '87f1b29472d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'maintenance_records',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('asset_id', sa.UUID(), nullable=False),
        sa.Column('alert_id', sa.UUID(), nullable=True),
        sa.Column('kind', sa.String(length=32), nullable=False),
        sa.Column('findings', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('outcome', sa.String(length=32), nullable=False),
        sa.Column('author_user_id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['alert_id'], ['alerts.id']),
        sa.ForeignKeyConstraint(['asset_id'], ['assets.id']),
        sa.ForeignKeyConstraint(['author_user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_maintenance_records_organization_id', 'maintenance_records', ['organization_id'], unique=False)
    op.create_index('ix_maintenance_records_asset_id', 'maintenance_records', ['asset_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_maintenance_records_asset_id', table_name='maintenance_records')
    op.drop_index('ix_maintenance_records_organization_id', table_name='maintenance_records')
    op.drop_table('maintenance_records')
