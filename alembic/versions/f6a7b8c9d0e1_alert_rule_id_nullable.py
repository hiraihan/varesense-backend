"""alert rule_id nullable (statistical anomalies)

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-11-01 00:15:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Alerts from statistical anomalies (z-score) have no threshold rule.
    op.alter_column('alerts', 'rule_id', nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column('alerts', 'rule_id', nullable=False)
