"""audit log (append-only)

Revision ID: a3b4c5d6e7
Revises: b28d98db4268
Create Date: 2026-10-31
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a3b4c5d6e7"
down_revision = "b28d98db4268"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("object_type", sa.String(50), nullable=False),
        sa.Column("object_id", postgresql.UUID(as_uuid=True)),
        sa.Column("details", postgresql.JSONB()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.execute(
        """
        CREATE FUNCTION prevent_audit_mutation() RETURNS trigger AS $$
        BEGIN
          IF TG_OP IN ('UPDATE', 'DELETE') THEN
            RAISE EXCEPTION 'audit_log is append-only';
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_append_only
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_audit_append_only ON audit_log")
    op.execute("DROP FUNCTION prevent_audit_mutation()")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_table("audit_log")
