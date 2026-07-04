"""rename task dedupe key

Revision ID: 20260704_0003
Revises: 20260704_0002
Create Date: 2026-07-04 21:00:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260704_0003"
down_revision: str | None = "20260704_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'tasks'
                  AND column_name = 'dedupe_key'
            ) THEN
                ALTER TABLE tasks RENAME COLUMN dedupe_key TO identity_key;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_tasks_dedupe_key'
            ) THEN
                ALTER TABLE tasks RENAME CONSTRAINT uq_tasks_dedupe_key TO uq_tasks_identity_key;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'tasks'
                  AND column_name = 'identity_key'
            ) THEN
                ALTER TABLE tasks RENAME COLUMN identity_key TO dedupe_key;
            END IF;

            IF EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname = 'uq_tasks_identity_key'
            ) THEN
                ALTER TABLE tasks RENAME CONSTRAINT uq_tasks_identity_key TO uq_tasks_dedupe_key;
            END IF;
        END $$;
        """
    )
