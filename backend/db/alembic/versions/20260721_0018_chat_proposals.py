"""expand chat turns to multiple approval proposals

Revision ID: 20260721_0018
Revises: 20260721_0017
"""

from collections.abc import Sequence

from alembic import op


revision: str = "20260721_0018"
down_revision: str | None = "20260721_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE chat_items
        SET content = (content - 'acquisition_plan')
            || jsonb_build_object(
                'acquisition_plans',
                CASE
                    WHEN content->'acquisition_plan' IS NULL
                      OR content->'acquisition_plan' = 'null'::jsonb
                    THEN '[]'::jsonb
                    ELSE jsonb_build_array(content->'acquisition_plan')
                END,
                'schedule_changes', '[]'::jsonb
            )
        WHERE content->>'kind' = 'assistant_turn'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE chat_items
        SET content = (content - 'acquisition_plans' - 'schedule_changes')
            || jsonb_build_object(
                'acquisition_plan',
                COALESCE(content->'acquisition_plans'->0, 'null'::jsonb)
            )
        WHERE content->>'kind' = 'assistant_turn'
        """
    )
