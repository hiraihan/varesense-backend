"""energy analytics: carbon factors

Revision ID: 87f1b29472d4
Revises: 2c41b0e7a905
Create Date: 2026-10-31 23:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '87f1b29472d4'
down_revision: Union[str, Sequence[str], None] = '2c41b0e7a905'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'carbon_factors',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('organization_id', sa.UUID(), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('unit', sa.String(length=32), nullable=False),
        sa.Column('factor', sa.Float(), nullable=False),
        sa.Column('source', sa.String(length=255), nullable=False),
        sa.Column('version', sa.String(length=32), nullable=False),
        sa.Column('basis', sa.String(length=255), nullable=False),
        sa.Column('assumptions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_carbon_factors_organization_id', 'carbon_factors', ['organization_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_carbon_factors_organization_id', table_name='carbon_factors')
    op.drop_table('carbon_factors')
