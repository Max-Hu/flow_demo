"""Add group scoped Temporal runtime tables.

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_user",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("is_super_admin", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "app_group",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "group_member",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["app_group.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("group_id", "user_id", "role", name="uq_group_member_role"),
    )
    op.create_index("ix_group_member_user_group", "group_member", ["user_id", "group_id"])

    for table in ("flow_definition", "flow_version", "flow_run", "flow_schedule", "flow_credential"):
        op.add_column(table, sa.Column("group_id", sa.String(36), nullable=False, server_default="default"))
    op.add_column("flow_version", sa.Column("node_registry_fingerprint", sa.String(64), nullable=False, server_default=""))
    op.add_column("flow_run", sa.Column("temporal_workflow_id", sa.String(240), nullable=True))
    op.add_column("flow_schedule", sa.Column("temporal_schedule_id", sa.String(240), nullable=True))
    op.add_column("admin_session", sa.Column("user_id", sa.String(36), nullable=True))
    op.add_column("security_audit_event", sa.Column("group_id", sa.String(36), nullable=True))
    op.add_column("callback_wait", sa.Column("group_id", sa.String(36), nullable=False, server_default="default"))
    op.add_column("flow_event", sa.Column("group_id", sa.String(36), nullable=False, server_default="default"))
    op.drop_constraint("flow_definition_name_key", "flow_definition", type_="unique")
    op.create_unique_constraint(
        "uq_flow_definition_group_name",
        "flow_definition",
        ["group_id", "name"],
    )

    op.create_table(
        "approval_group",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["group_id"], ["app_group.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("group_id", "alias", name="uq_approval_group_alias"),
    )
    op.create_table(
        "approval_group_member",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("approval_group_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.ForeignKeyConstraint(["approval_group_id"], ["approval_group.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["app_user.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("approval_group_id", "user_id", name="uq_approval_group_member"),
    )
    op.create_table(
        "approval_task",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("group_id", sa.String(36), nullable=False),
        sa.Column("approval_group_id", sa.String(36), nullable=True),
        sa.Column("flow_run_id", sa.String(36), nullable=False),
        sa.Column("node_run_id", sa.String(36), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("decision", sa.String(100), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("decided_by_user_id", sa.String(36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_run_id"], ["flow_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("flow_run_id", "node_id", name="uq_approval_task_node"),
    )
    op.create_index("ix_approval_task_group_status", "approval_task", ["group_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_approval_task_group_status", table_name="approval_task")
    op.drop_table("approval_task")
    op.drop_table("approval_group_member")
    op.drop_table("approval_group")
    op.drop_column("flow_event", "group_id")
    op.drop_column("callback_wait", "group_id")
    op.drop_column("security_audit_event", "group_id")
    op.drop_column("admin_session", "user_id")
    op.drop_column("flow_schedule", "temporal_schedule_id")
    op.drop_column("flow_run", "temporal_workflow_id")
    op.drop_column("flow_version", "node_registry_fingerprint")
    op.drop_constraint("uq_flow_definition_group_name", "flow_definition", type_="unique")
    op.create_unique_constraint("flow_definition_name_key", "flow_definition", ["name"])
    for table in ("flow_credential", "flow_schedule", "flow_run", "flow_version", "flow_definition"):
        op.drop_column(table, "group_id")
    op.drop_index("ix_group_member_user_group", table_name="group_member")
    op.drop_table("group_member")
    op.drop_table("app_group")
    op.drop_table("app_user")
