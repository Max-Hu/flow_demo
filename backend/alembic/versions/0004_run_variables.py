"""Add run-scoped variables.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flow_run_variable",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_run_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("value_type", sa.String(30), nullable=False),
        sa.Column("updated_by_node_id", sa.String(100), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_run_id"], ["flow_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("flow_run_id", "name", name="uq_flow_run_variable"),
    )
    op.create_index(
        "ix_flow_run_variable_run_name",
        "flow_run_variable",
        ["flow_run_id", "name"],
    )


def downgrade() -> None:
    op.drop_table("flow_run_variable")
