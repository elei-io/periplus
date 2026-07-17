"""seed the default content policy

Revision ID: 20260717_0008
Revises: 20260716_0007
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0008"
down_revision: str | None = "20260716_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    crawl_policies = sa.table(
        "crawl_policies",
        sa.column("id", sa.UUID()),
        sa.column("slug", sa.Text()),
        sa.column("scheme", sa.Text()),
        sa.column("host", sa.Text()),
        sa.column("path_prefix", sa.Text()),
        sa.column("path_mode", sa.Text()),
        sa.column("content", postgresql.JSONB()),
        sa.column("enabled", sa.Boolean()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    now = datetime.now(UTC)
    statement = postgresql.insert(crawl_policies).values(
        id=uuid4(),
        slug="default",
        scheme="*",
        host="*",
        path_prefix="/",
        path_mode="prefix",
        content={
            "accepted_content_types": ["text/html", "application/xhtml+xml"],
            "response_rules": {
                "http_status": [
                    {"minimum": 408, "maximum": 408, "outcome": "retry"},
                    {"minimum": 425, "maximum": 425, "outcome": "retry"},
                    {"minimum": 429, "maximum": 429, "outcome": "retry"},
                    {"minimum": 500, "maximum": 599, "outcome": "retry"},
                    {"minimum": 400, "maximum": 499, "outcome": "fail"},
                ],
                "unsupported_content_type": "skip",
            },
            "completion": {
                "wait_dynamic": {
                    "enabled": True,
                    "maximum_wait_ms": 8000,
                    "sample_interval_ms": 250,
                    "stable_samples": 3,
                },
                "wait_fixed": {"enabled": False, "duration_ms": 0},
                "scroll": {
                    "enabled": True,
                    "maximum_iterations": 30,
                    "viewport_ratio": 0.85,
                    "wait_ms": 250,
                    "stable_bottom_samples": 3,
                },
                "expand": {
                    "enabled": True,
                    "maximum_actions": 10,
                    "wait_ms": 500,
                },
            },
        },
        enabled=True,
        created_at=now,
        updated_at=now,
    )
    op.execute(statement.on_conflict_do_nothing(index_elements=["slug"]))


def downgrade() -> None:
    raise RuntimeError("The seeded content-policy contract cannot be downgraded")
