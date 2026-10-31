"""telemetry ingestion: partitioned samples, dead letter

Revision ID: 6e135edc54a5
Revises: a5f4c63512b9
Create Date: 2026-10-31 21:48:25.077351

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6e135edc54a5'
down_revision: Union[str, Sequence[str], None] = 'a5f4c63512b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Time-range partitioned parent. `measured_at` is the partition key;
    # a DEFAULT partition catches any range not yet explicitly created.
    op.execute(
        """
        CREATE TABLE telemetry (
            event_id VARCHAR(64) NOT NULL,
            device_id UUID NOT NULL,
            organization_id UUID NOT NULL,
            measured_at TIMESTAMPTZ NOT NULL,
            received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            sequence_id BIGINT NOT NULL,
            measurement_type VARCHAR(64) NOT NULL,
            value DOUBLE PRECISION NOT NULL,
            unit VARCHAR(32) NOT NULL,
            quality_status VARCHAR(32) NOT NULL,
            operating_context JSONB,
            schema_version VARCHAR(32) NOT NULL,
            configuration_version VARCHAR(64),
            firmware_version VARCHAR(64)
        ) PARTITION BY RANGE (measured_at)
        """
    )
    op.execute("CREATE TABLE telemetry_default PARTITION OF telemetry DEFAULT")
    # Idempotency: duplicate (device_id, event_id) must not double-insert.
    # Postgres requires a unique index on a partitioned table to include every
    # partition key column, so measured_at is part of the dedupe key.
    op.execute(
        "CREATE UNIQUE INDEX ix_telemetry_dedupe "
        "ON telemetry (device_id, event_id, measured_at)"
    )
    op.execute(
        "CREATE INDEX ix_telemetry_org_time "
        "ON telemetry (organization_id, measured_at)"
    )

    op.execute(
        """
        CREATE TABLE dead_letter (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id UUID NOT NULL,
            device_id UUID,
            event_id VARCHAR(64),
            reason VARCHAR(255) NOT NULL,
            payload JSONB NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_dead_letter_org ON dead_letter (organization_id)")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TABLE dead_letter")
    op.execute("DROP TABLE telemetry CASCADE")
