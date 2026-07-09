"""query pagination schema

Revision ID: 20260709_0009
Revises: 20260709_0008
Create Date: 2026-07-09 00:09:00.000000

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260709_0009"
down_revision: str | None = "20260709_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_pagination_schemas_kind", table_name="pagination_schemas")
    op.add_column("pagination_schemas", sa.Column("next_button_selector", sa.Text(), nullable=True))
    op.add_column("pagination_schemas", sa.Column("expected_max_item_count", sa.Integer(), nullable=True))
    op.add_column("pagination_schemas", sa.Column("query_param_key", sa.Text(), nullable=True))
    op.add_column("pagination_schemas", sa.Column("query_param_value_template", sa.Text(), nullable=True))
    op.add_column("pagination_schemas", sa.Column("start_value", sa.Integer(), nullable=True))
    op.add_column("pagination_schemas", sa.Column("value_step", sa.Integer(), nullable=True))

    op.execute("UPDATE pagination_schemas SET query_param_key = 'page' WHERE query_param_key IS NULL")
    op.execute("UPDATE pagination_schemas SET query_param_value_template = '{{value}}' WHERE query_param_value_template IS NULL")
    op.execute("UPDATE pagination_schemas SET start_value = 1 WHERE start_value IS NULL")
    op.execute("UPDATE pagination_schemas SET value_step = 1 WHERE value_step IS NULL")
    op.execute("UPDATE pagination_schemas SET expected_max_item_count = expected_max_page_item_count")

    op.alter_column("pagination_schemas", "query_param_key", nullable=False)
    op.alter_column("pagination_schemas", "query_param_value_template", nullable=False)
    op.alter_column("pagination_schemas", "start_value", nullable=False)
    op.alter_column("pagination_schemas", "value_step", nullable=False)

    op.drop_column("pagination_schemas", "config_json")
    op.drop_column("pagination_schemas", "kind")
    op.drop_column("pagination_schemas", "expected_max_page_item_count")


def downgrade() -> None:
    op.add_column("pagination_schemas", sa.Column("expected_max_page_item_count", sa.Integer(), nullable=True))
    op.add_column("pagination_schemas", sa.Column("kind", sa.Text(), nullable=True))
    op.add_column("pagination_schemas", sa.Column("config_json", sa.JSON(), nullable=True))

    op.execute("UPDATE pagination_schemas SET kind = 'query' WHERE kind IS NULL")
    op.execute(
        """
        UPDATE pagination_schemas
        SET config_json = json_build_object(
            'query_param_name', query_param_key,
            'value_format', query_param_value_template,
            'start', start_value + value_step,
            'step', value_step
        )
        WHERE config_json IS NULL
        """
    )
    op.execute("UPDATE pagination_schemas SET expected_max_page_item_count = expected_max_item_count")

    op.alter_column("pagination_schemas", "kind", nullable=False)
    op.alter_column("pagination_schemas", "config_json", nullable=False)
    op.create_index("ix_pagination_schemas_kind", "pagination_schemas", ["kind"])

    op.drop_column("pagination_schemas", "value_step")
    op.drop_column("pagination_schemas", "start_value")
    op.drop_column("pagination_schemas", "query_param_value_template")
    op.drop_column("pagination_schemas", "query_param_key")
    op.drop_column("pagination_schemas", "expected_max_item_count")
    op.drop_column("pagination_schemas", "next_button_selector")
