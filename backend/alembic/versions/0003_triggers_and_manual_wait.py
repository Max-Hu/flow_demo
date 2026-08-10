"""Add schedules, run provenance, and manual waiting states.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE flow_definition SET status = 'ACTIVE' WHERE status = 'PUBLISHED'")
    op.add_column(
        "flow_run",
        sa.Column("trigger_type", sa.String(30), nullable=False, server_default="MANUAL"),
    )
    op.add_column("flow_run", sa.Column("trigger_id", sa.String(36), nullable=True))
    op.add_column("flow_run", sa.Column("parent_run_id", sa.String(36), nullable=True))
    op.add_column("flow_run", sa.Column("idempotency_key", sa.String(200), nullable=True))
    op.add_column(
        "flow_run",
        sa.Column(
            "source_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
    )
    op.add_column(
        "flow_run",
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_foreign_key("fk_flow_run_parent", "flow_run", "flow_run", ["parent_run_id"], ["id"])
    op.create_unique_constraint("uq_flow_run_idempotency_key", "flow_run", ["idempotency_key"])
    op.create_table(
        "flow_schedule",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("flow_version_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(100), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_id"], ["flow_definition.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["flow_version_id"], ["flow_version.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_flow_schedule_due", "flow_schedule", ["enabled", "next_run_at"])
    op.alter_column("flow_run", "trigger_type", server_default=None)
    op.alter_column("flow_run", "source_metadata", server_default=None)
    op.alter_column("flow_run", "requested_at", server_default=None)


def downgrade() -> None:
    op.drop_table("flow_schedule")
    op.drop_constraint("uq_flow_run_idempotency_key", "flow_run", type_="unique")
    op.drop_constraint("fk_flow_run_parent", "flow_run", type_="foreignkey")
    columns = [
        "requested_at",
        "source_metadata",
        "idempotency_key",
        "parent_run_id",
        "trigger_id",
        "trigger_type",
    ]
    for column in columns:
        op.drop_column("flow_run", column)
    op.execute("UPDATE flow_definition SET status = 'PUBLISHED' WHERE status = 'ACTIVE'")
