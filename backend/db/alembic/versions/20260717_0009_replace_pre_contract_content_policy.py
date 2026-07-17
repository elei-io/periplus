"""replace pre-contract content policy JSON

Revision ID: 20260717_0009
Revises: 20260717_0008
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0009"
down_revision: str | None = "20260717_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    crawl_policies = sa.table(
        "crawl_policies",
        sa.column("slug", sa.Text()),
        sa.column("content", postgresql.JSONB()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    canonical_content = {
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
    }
    op.execute(
        sa.update(crawl_policies)
        .where(crawl_policies.c.slug == "default")
        .where(
            ~crawl_policies.c.content["completion"].has_key("wait_dynamic")
        )
        .values(content=canonical_content, updated_at=datetime.now(UTC))
    )


def downgrade() -> None:
    raise RuntimeError("Pre-contract content-policy JSON cannot be restored")
