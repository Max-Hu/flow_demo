"""Add durable HTTP callback waits.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "callback_wait",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_run_id", sa.String(36), nullable=False),
        sa.Column("node_run_id", sa.String(36), nullable=False),
        sa.Column("node_id", sa.String(100), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("auth_mode", sa.String(30), nullable=False),
        sa.Column("credential_alias", sa.String(100), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("request_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_run_id"], ["flow_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_run.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "node_run_id", "attempt_number", name="uq_callback_wait_attempt"
        ),
    )
    op.create_index(
        "ix_callback_wait_due", "callback_wait", ["status", "expires_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_callback_wait_due", table_name="callback_wait")
    op.drop_table("callback_wait")
