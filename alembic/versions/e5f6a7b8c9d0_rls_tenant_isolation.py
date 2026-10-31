"""row-level security: tenant isolation policies

Revision ID: e5f6a7b8c9d0
Revises: d0a9b1c2e3f4
Create Date: 2026-10-31 23:59:30.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd0a9b1c2e3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (
    "assets",
    "asset_history",
    "edge_nodes",
    "device_credentials",
    "device_endpoints",
    "telemetry",
    "dead_letter",
    "alerts",
    "alert_rules",
    "baselines",
    "anomalies",
    "carbon_factors",
    "maintenance_records",
)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
        )
        """
    )


def upgrade() -> None:
    """Upgrade schema."""
    # Superuser connection role bypasses RLS, so current app behaviour is
    # unchanged; these policies bind once the app runs under a least-privilege
    # role (defense-in-depth per ADR-0002).
    for t in TABLES:
        _rls(t)


def downgrade() -> None:
    """Downgrade schema."""
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
