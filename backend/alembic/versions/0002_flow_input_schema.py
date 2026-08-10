"""Add versioned flow input schemas.

Revision ID: 0002
Revises: 0001
"""

import json

import sqlalchemy as sa

from alembic import op
from app.input_schema import DEMO_INPUT_SCHEMA, EMPTY_INPUT_SCHEMA

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    empty_schema = json.dumps(EMPTY_INPUT_SCHEMA).replace("'", "''")
    demo_schema = json.dumps(DEMO_INPUT_SCHEMA).replace("'", "''")
    op.add_column(
        "flow_definition",
        sa.Column(
            "input_schema",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{empty_schema}'::json"),
        ),
    )
    op.add_column(
        "flow_version",
        sa.Column(
            "input_schema",
            sa.JSON(),
            nullable=False,
            server_default=sa.text(f"'{empty_schema}'::json"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE flow_definition SET input_schema = "
            f"'{demo_schema}'::json WHERE name = 'Customer Score Automation'"
        )
    )
    op.execute(
        sa.text(
            "UPDATE flow_version AS version SET input_schema = definition.input_schema "
            "FROM flow_definition AS definition "
            "WHERE version.flow_id = definition.id"
        )
    )
    op.alter_column("flow_definition", "input_schema", server_default=None)
    op.alter_column("flow_version", "input_schema", server_default=None)


def downgrade() -> None:
    op.drop_column("flow_version", "input_schema")
    op.drop_column("flow_definition", "input_schema")
