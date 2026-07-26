"""move current graph execution state to Postgres

Revision ID: 20260724_0017
Revises: 20260721_0016
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260724_0017"
down_revision: str | None = "20260721_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "graph_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("graph_id", sa.UUID(), nullable=False),
        sa.Column("trigger_kind", sa.Text(), nullable=False),
        sa.Column("trigger_schedule_id", sa.UUID(), nullable=True),
        sa.Column("catalogue_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "catalogue_consistency",
            sa.Text(),
            nullable=False,
            server_default="run_frozen",
        ),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("snapshot", json_type, nullable=False),
        sa.Column("trigger_urls", json_type, nullable=False),
        sa.Column("max_crawls", sa.Integer(), nullable=False),
        sa.Column(
            "crawl_limit_reached",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "root_admission_cursor",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("request_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "pending_request_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "acquisition_pending_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "failed_request_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "failure_groups",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'paused', 'completed', "
            "'completed_with_errors', 'failed', 'cancelled')",
            name="ck_graph_runs_status",
        ),
        sa.CheckConstraint(
            "trigger_kind IN ('manual', 'schedule')",
            name="ck_graph_runs_trigger_kind",
        ),
        sa.CheckConstraint(
            "catalogue_consistency = 'run_frozen'",
            name="ck_graph_runs_catalogue_consistency",
        ),
        sa.CheckConstraint("generation >= 1", name="ck_graph_runs_generation"),
        sa.CheckConstraint("max_crawls >= 1", name="ck_graph_runs_max_crawls"),
        sa.CheckConstraint(
            "request_count >= 0 AND pending_request_count >= 0 "
            "AND acquisition_pending_count >= 0 "
            "AND failed_request_count >= 0 AND error_count >= 0",
            name="ck_graph_runs_nonnegative_counts",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_graph_runs_graph_id", "graph_runs", ["graph_id"])
    op.create_index(
        "ix_graph_runs_trigger_schedule_id",
        "graph_runs",
        ["trigger_schedule_id"],
    )
    op.create_index("ix_graph_runs_status", "graph_runs", ["status"])
    op.create_index("ix_graph_runs_not_before", "graph_runs", ["not_before"])
    op.create_index("ix_graph_runs_deadline_at", "graph_runs", ["deadline_at"])
    op.create_index(
        "ix_graph_runs_active",
        "graph_runs",
        ["status", "not_before"],
        postgresql_where=sa.text("status IN ('queued', 'running', 'paused')"),
    )

    op.create_table(
        "crawl_requests",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("graph_run_id", sa.UUID(), nullable=False),
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("node_id", sa.UUID(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("effective_policy_snapshot", json_type, nullable=False),
        sa.Column("source_crawl_id", sa.UUID(), nullable=True),
        sa.Column("source_edge_id", sa.UUID(), nullable=True),
        sa.Column("parent_request_id", sa.UUID(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.UUID(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("failure_stage", sa.Text(), nullable=True),
        sa.Column(
            "processing_failure_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "acquisition_attempts",
            json_type,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'crawling', 'awaiting_navigation', "
            "'evaluating_edges', 'completed', 'failed', 'cancelled')",
            name="ck_crawl_requests_status",
        ),
        sa.CheckConstraint(
            "generation >= 1", name="ck_crawl_requests_generation"
        ),
        sa.CheckConstraint(
            "processing_failure_count >= 0",
            name="ck_crawl_requests_processing_failures",
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"], ["graph_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_request_id"], ["crawl_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "graph_run_id", "identity", name="uq_crawl_requests_identity"
        ),
    )
    op.create_index(
        "ix_crawl_requests_graph_run_id", "crawl_requests", ["graph_run_id"]
    )
    op.create_index("ix_crawl_requests_node_id", "crawl_requests", ["node_id"])
    op.create_index(
        "ix_crawl_requests_source_edge_id",
        "crawl_requests",
        ["source_edge_id"],
    )
    op.create_index("ix_crawl_requests_status", "crawl_requests", ["status"])
    op.create_index(
        "ix_crawl_requests_not_before", "crawl_requests", ["not_before"]
    )
    op.create_index(
        "ix_crawl_requests_runnable",
        "crawl_requests",
        ["status", "not_before", "priority"],
    )
    op.create_index(
        "ix_crawl_requests_run_node_status",
        "crawl_requests",
        ["graph_run_id", "node_id", "status"],
    )

    op.create_table(
        "graph_admissions",
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("graph_run_id", sa.UUID(), nullable=False),
        sa.Column("crawl_request_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["graph_run_id"], ["graph_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_request_id"], ["crawl_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("identity"),
    )
    op.create_index(
        "ix_graph_admissions_graph_run_id",
        "graph_admissions",
        ["graph_run_id"],
    )
    op.create_index(
        "ix_graph_admissions_crawl_request_id",
        "graph_admissions",
        ["crawl_request_id"],
    )

    op.create_table(
        "edge_evaluations",
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("graph_run_id", sa.UUID(), nullable=False),
        sa.Column("crawl_request_id", sa.UUID(), nullable=False),
        sa.Column("crawl_id", sa.UUID(), nullable=False),
        sa.Column("edge_id", sa.UUID(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("claim_token", sa.UUID(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("output_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selection", json_type, nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_edge_evaluations_status",
        ),
        sa.CheckConstraint(
            "generation >= 1", name="ck_edge_evaluations_generation"
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"], ["graph_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["crawl_request_id"], ["crawl_requests.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("identity"),
    )
    op.create_index(
        "ix_edge_evaluations_graph_run_id",
        "edge_evaluations",
        ["graph_run_id"],
    )
    op.create_index(
        "ix_edge_evaluations_crawl_request_id",
        "edge_evaluations",
        ["crawl_request_id"],
    )
    op.create_index(
        "ix_edge_evaluations_edge_id", "edge_evaluations", ["edge_id"]
    )
    op.create_index(
        "ix_edge_evaluations_run_edge_status",
        "edge_evaluations",
        ["graph_run_id", "edge_id", "status"],
    )

    op.create_table(
        "graph_outbox",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("graph_run_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("payload", json_type, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.UUID(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "publish_attempts", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "publish_attempts >= 0", name="ck_graph_outbox_publish_attempts"
        ),
        sa.ForeignKeyConstraint(
            ["graph_run_id"], ["graph_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id", name="uq_graph_outbox_message_id"),
    )
    op.create_index(
        "ix_graph_outbox_graph_run_id",
        "graph_outbox",
        ["graph_run_id"],
    )
    op.create_index(
        "ix_graph_outbox_ready",
        "graph_outbox",
        ["published_at", "not_before", "claim_expires_at"],
        postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_graph_outbox_ready", table_name="graph_outbox")
    op.drop_index(
        "ix_graph_outbox_graph_run_id", table_name="graph_outbox"
    )
    op.drop_table("graph_outbox")
    op.drop_index(
        "ix_edge_evaluations_run_edge_status", table_name="edge_evaluations"
    )
    op.drop_index("ix_edge_evaluations_edge_id", table_name="edge_evaluations")
    op.drop_index(
        "ix_edge_evaluations_crawl_request_id",
        table_name="edge_evaluations",
    )
    op.drop_index(
        "ix_edge_evaluations_graph_run_id", table_name="edge_evaluations"
    )
    op.drop_table("edge_evaluations")
    op.drop_index(
        "ix_graph_admissions_crawl_request_id", table_name="graph_admissions"
    )
    op.drop_index(
        "ix_graph_admissions_graph_run_id", table_name="graph_admissions"
    )
    op.drop_table("graph_admissions")
    op.drop_index(
        "ix_crawl_requests_run_node_status", table_name="crawl_requests"
    )
    op.drop_index("ix_crawl_requests_runnable", table_name="crawl_requests")
    op.drop_index("ix_crawl_requests_not_before", table_name="crawl_requests")
    op.drop_index("ix_crawl_requests_status", table_name="crawl_requests")
    op.drop_index(
        "ix_crawl_requests_source_edge_id", table_name="crawl_requests"
    )
    op.drop_index("ix_crawl_requests_node_id", table_name="crawl_requests")
    op.drop_index("ix_crawl_requests_graph_run_id", table_name="crawl_requests")
    op.drop_table("crawl_requests")
    op.drop_index("ix_graph_runs_active", table_name="graph_runs")
    op.drop_index("ix_graph_runs_not_before", table_name="graph_runs")
    op.drop_index("ix_graph_runs_deadline_at", table_name="graph_runs")
    op.drop_index("ix_graph_runs_status", table_name="graph_runs")
    op.drop_index(
        "ix_graph_runs_trigger_schedule_id", table_name="graph_runs"
    )
    op.drop_index("ix_graph_runs_graph_id", table_name="graph_runs")
    op.drop_table("graph_runs")
