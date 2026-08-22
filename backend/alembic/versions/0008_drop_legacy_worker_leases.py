"""drop legacy db worker lease columns

Revision ID: 0008_drop_legacy_worker_leases
Revises: 0007
Create Date: 2026-08-22 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0008_drop_legacy_worker_leases"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("node_run")}
    columns = {item["name"] for item in inspector.get_columns("node_run")}
    if "ix_node_run_claim" in indexes:
        op.drop_index("ix_node_run_claim", table_name="node_run")
    if "lease_expires_at" in columns:
        op.drop_column("node_run", "lease_expires_at")
    if "lease_owner" in columns:
        op.drop_column("node_run", "lease_owner")
    if "ix_node_run_status_available" not in indexes:
        op.create_index(
            "ix_node_run_status_available",
            "node_run",
            ["status", "available_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {item["name"] for item in inspector.get_indexes("node_run")}
    columns = {item["name"] for item in inspector.get_columns("node_run")}
    if "ix_node_run_status_available" in indexes:
        op.drop_index("ix_node_run_status_available", table_name="node_run")
    if "lease_owner" not in columns:
        op.add_column("node_run", sa.Column("lease_owner", sa.String(100), nullable=True))
    if "lease_expires_at" not in columns:
        op.add_column(
            "node_run",
            sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "ix_node_run_claim" not in indexes:
        op.create_index("ix_node_run_claim", "node_run", ["status", "available_at"])
