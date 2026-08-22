"""Initial workflow schema.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flow_definition",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("draft_content", sa.JSON(), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "flow_version",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_id"], ["flow_definition.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("flow_id", "version_number"),
    )
    op.create_table(
        "flow_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("flow_version_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=False),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["flow_id"], ["flow_definition.id"]),
        sa.ForeignKeyConstraint(["flow_version_id"], ["flow_version.id"]),
    )
    op.create_index("ix_flow_run_status_created", "flow_run", ["status", "created_at"])
    op.create_table(
        "node_run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_run_id", sa.String(36), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("node_type", sa.String(100), nullable=False),
        sa.Column("node_version", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("input_data", sa.JSON(), nullable=True),
        sa.Column("output_data", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["flow_run_id"], ["flow_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("flow_run_id", "node_id"),
    )
    op.create_index("ix_node_run_status_available", "node_run", ["status", "available_at"])
    op.create_table(
        "node_run_attempt",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("node_run_id", sa.String(36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("node_run_id", "attempt_number"),
    )
    op.create_table(
        "flow_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flow_run_id", sa.String(36), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_run_id"], ["flow_run.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_flow_event_run_id_id", "flow_event", ["flow_run_id", "id"])


def downgrade() -> None:
    op.drop_table("flow_event")
    op.drop_table("node_run_attempt")
    op.drop_table("node_run")
    op.drop_table("flow_run")
    op.drop_table("flow_version")
    op.drop_table("flow_definition")
