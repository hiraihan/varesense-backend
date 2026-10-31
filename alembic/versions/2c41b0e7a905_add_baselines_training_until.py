"""add baselines.training_until

Revision ID: 2c41b0e7a905
Revises: 07dd9c349662
Create Date: 2026-10-31 23:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c41b0e7a905'
down_revision: Union[str, Sequence[str], None] = '07dd9c349662'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('baselines', sa.Column('training_until', sa.DateTime(timezone=True), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('baselines', 'training_until')
