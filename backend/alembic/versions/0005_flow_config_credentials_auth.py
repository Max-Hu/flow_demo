"""Add flow configuration, credentials, and admin sessions.

Revision ID: 0005
Revises: 0004
"""

import json

import sqlalchemy as sa

from alembic import op
from app.flow_config import EMPTY_CONFIG_SCHEMA

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def json_default(value: object) -> sa.TextClause:
    serialized = json.dumps(value).replace("'", "''")
    return sa.text(f"'{serialized}'::json")


def upgrade() -> None:
    for table in ("flow_definition", "flow_version"):
        op.add_column(
            table,
            sa.Column(
                "config_schema",
                sa.JSON(),
                nullable=False,
                server_default=json_default(EMPTY_CONFIG_SCHEMA),
            ),
        )
        op.add_column(
            table,
            sa.Column("default_config", sa.JSON(), nullable=False, server_default=json_default({})),
        )
    op.add_column(
        "flow_run",
        sa.Column("flow_config", sa.JSON(), nullable=False, server_default=json_default({})),
    )
    op.add_column(
        "flow_schedule",
        sa.Column("config_overrides", sa.JSON(), nullable=False, server_default=json_default({})),
    )
    op.create_table(
        "flow_credential",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("flow_id", sa.String(36), nullable=False),
        sa.Column("alias", sa.String(100), nullable=False),
        sa.Column("credential_type", sa.String(30), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["flow_id"], ["flow_definition.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("flow_id", "alias", name="uq_flow_credential_alias"),
    )
    op.create_table(
        "flow_credential_revision",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("credential_id", sa.String(36), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("key_id", sa.String(100), nullable=False),
        sa.Column("nonce", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["credential_id"], ["flow_credential.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("credential_id", "revision", name="uq_flow_credential_revision"),
    )
    op.create_table(
        "admin_session",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(100), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "security_audit_event",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("flow_id", sa.String(36), nullable=True),
        sa.Column("credential_id", sa.String(36), nullable=True),
        sa.Column("actor", sa.String(100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_security_audit_created", "security_audit_event", ["created_at"])
    for table in ("flow_definition", "flow_version"):
        op.alter_column(table, "config_schema", server_default=None)
        op.alter_column(table, "default_config", server_default=None)
    op.alter_column("flow_run", "flow_config", server_default=None)
    op.alter_column("flow_schedule", "config_overrides", server_default=None)


def downgrade() -> None:
    op.drop_table("security_audit_event")
    op.drop_table("admin_session")
    op.drop_table("flow_credential_revision")
    op.drop_table("flow_credential")
    op.drop_column("flow_schedule", "config_overrides")
    op.drop_column("flow_run", "flow_config")
    for table in ("flow_version", "flow_definition"):
        op.drop_column(table, "default_config")
        op.drop_column(table, "config_schema")
