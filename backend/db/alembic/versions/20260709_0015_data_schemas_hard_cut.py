"""data schemas hard cut

Revision ID: 20260709_0015
Revises: 20260709_0014
Create Date: 2026-07-09 09:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = "20260709_0015"
down_revision: str | None = "20260709_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.extract_schemas') IS NOT NULL
               AND to_regclass('public.data_schemas') IS NULL THEN
                ALTER TABLE extract_schemas RENAME TO data_schemas;
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'task_runs' AND column_name = 'extract_schema_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'task_runs' AND column_name = 'data_schema_id'
            ) THEN
                ALTER TABLE task_runs RENAME COLUMN extract_schema_id TO data_schema_id;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER INDEX IF EXISTS ix_extract_schemas_domain RENAME TO ix_data_schemas_domain")
    op.execute("ALTER INDEX IF EXISTS ix_extract_schemas_enabled RENAME TO ix_data_schemas_enabled")
    op.execute("ALTER INDEX IF EXISTS ix_extract_schemas_match RENAME TO ix_data_schemas_match")
    op.execute("ALTER INDEX IF EXISTS ix_extract_schemas_prompt_hash RENAME TO ix_data_schemas_prompt_hash")
    op.execute("ALTER INDEX IF EXISTS ix_extract_schemas_schema_type RENAME TO ix_data_schemas_schema_type")
    op.execute("ALTER INDEX IF EXISTS uq_extract_schemas_identity_key RENAME TO uq_data_schemas_identity_key")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_task_runs_extract_schema_id_extract_schemas'
            ) THEN
                ALTER TABLE task_runs DROP CONSTRAINT fk_task_runs_extract_schema_id_extract_schemas;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_task_runs_data_schema_id_data_schemas'
            ) THEN
                ALTER TABLE task_runs
                ADD CONSTRAINT fk_task_runs_data_schema_id_data_schemas
                FOREIGN KEY (data_schema_id) REFERENCES data_schemas(id)
                ON DELETE SET NULL;
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
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_task_runs_data_schema_id_data_schemas'
            ) THEN
                ALTER TABLE task_runs DROP CONSTRAINT fk_task_runs_data_schema_id_data_schemas;
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_task_runs_extract_schema_id_extract_schemas'
            ) THEN
                ALTER TABLE task_runs
                ADD CONSTRAINT fk_task_runs_extract_schema_id_extract_schemas
                FOREIGN KEY (data_schema_id) REFERENCES data_schemas(id)
                ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )
    op.execute("ALTER INDEX IF EXISTS uq_data_schemas_identity_key RENAME TO uq_extract_schemas_identity_key")
    op.execute("ALTER INDEX IF EXISTS ix_data_schemas_schema_type RENAME TO ix_extract_schemas_schema_type")
    op.execute("ALTER INDEX IF EXISTS ix_data_schemas_prompt_hash RENAME TO ix_extract_schemas_prompt_hash")
    op.execute("ALTER INDEX IF EXISTS ix_data_schemas_match RENAME TO ix_extract_schemas_match")
    op.execute("ALTER INDEX IF EXISTS ix_data_schemas_enabled RENAME TO ix_extract_schemas_enabled")
    op.execute("ALTER INDEX IF EXISTS ix_data_schemas_domain RENAME TO ix_extract_schemas_domain")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'task_runs' AND column_name = 'data_schema_id'
            ) AND NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'task_runs' AND column_name = 'extract_schema_id'
            ) THEN
                ALTER TABLE task_runs RENAME COLUMN data_schema_id TO extract_schema_id;
            END IF;
            IF to_regclass('public.data_schemas') IS NOT NULL
               AND to_regclass('public.extract_schemas') IS NULL THEN
                ALTER TABLE data_schemas RENAME TO extract_schemas;
            END IF;
        END $$;
        """
    )
