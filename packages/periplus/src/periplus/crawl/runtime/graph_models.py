"""Transactional Postgres state for current crawl-graph execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from periplus.platform.postgres import Base
from periplus.platform.postgres.types import json_type, utc_now


class ClaimedWorkColumns:
    """Columns shared by claimable graph-runtime work records."""

    claim_token: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class GraphRunRecord(Base):
    __tablename__ = "graph_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('queued', 'running', 'paused', 'completed', "
            "'completed_with_errors', 'failed', 'cancelled')",
            name="ck_graph_runs_status",
        ),
        CheckConstraint(
            "trigger_kind IN ('manual', 'schedule')",
            name="ck_graph_runs_trigger_kind",
        ),
        CheckConstraint("generation >= 1", name="ck_graph_runs_generation"),
        CheckConstraint("max_crawls >= 1", name="ck_graph_runs_max_crawls"),
        CheckConstraint(
            "request_count >= 0 AND pending_request_count >= 0 "
            "AND acquisition_pending_count >= 0 "
            "AND failed_request_count >= 0 AND error_count >= 0",
            name="ck_graph_runs_nonnegative_counts",
        ),
        Index(
            "ix_graph_runs_active",
            "status",
            "not_before",
            postgresql_where=text(
                "status IN ('queued', 'running', 'paused')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    graph_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    trigger_kind: Mapped[str] = mapped_column(Text)
    trigger_schedule_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(Text, default="queued", index=True)
    snapshot: Mapped[dict[str, Any]] = mapped_column(json_type)
    trigger_urls: Mapped[list[str]] = mapped_column(json_type)
    max_crawls: Mapped[int] = mapped_column(Integer)
    crawl_limit_reached: Mapped[bool] = mapped_column(Boolean, default=False)
    root_admission_cursor: Mapped[int] = mapped_column(Integer, default=0)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    pending_request_count: Mapped[int] = mapped_column(Integer, default=0)
    acquisition_pending_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_request_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_groups: Mapped[list[dict[str, Any]]] = mapped_column(
        json_type, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class CrawlRequestRecord(ClaimedWorkColumns, Base):
    __tablename__ = "crawl_requests"
    __table_args__ = (
        CheckConstraint(
            "status IN "
            "('queued', 'crawling', 'awaiting_navigation', "
            "'evaluating_edges', 'completed', 'failed', 'cancelled')",
            name="ck_crawl_requests_status",
        ),
        CheckConstraint(
            "generation >= 1", name="ck_crawl_requests_generation"
        ),
        CheckConstraint(
            "processing_failure_count >= 0",
            name="ck_crawl_requests_processing_failures",
        ),
        UniqueConstraint(
            "graph_run_id", "identity", name="uq_crawl_requests_identity"
        ),
        Index(
            "ix_crawl_requests_runnable",
            "status",
            "not_before",
            "priority",
        ),
        Index(
            "ix_crawl_requests_run_node_status",
            "graph_run_id",
            "node_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    graph_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("graph_runs.id", ondelete="CASCADE"),
        index=True,
    )
    identity: Mapped[str] = mapped_column(Text)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    url: Mapped[str] = mapped_column(Text)
    content_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_policy_snapshot: Mapped[dict[str, Any]] = mapped_column(json_type)
    source_crawl_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    source_edge_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    parent_request_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("crawl_requests.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(Text, default="queued", index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    failure_stage: Mapped[str | None] = mapped_column(Text, nullable=True)
    processing_failure_count: Mapped[int] = mapped_column(Integer, default=0)
    acquisition_attempts: Mapped[list[dict[str, Any]]] = mapped_column(
        json_type, default=list
    )


class GraphAdmissionRecord(Base):
    __tablename__ = "graph_admissions"

    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    graph_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("graph_runs.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("crawl_requests.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )


class EdgeEvaluationRecord(ClaimedWorkColumns, Base):
    __tablename__ = "edge_evaluations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_edge_evaluations_status",
        ),
        CheckConstraint(
            "generation >= 1", name="ck_edge_evaluations_generation"
        ),
        Index(
            "ix_edge_evaluations_run_edge_status",
            "graph_run_id",
            "edge_id",
            "status",
        ),
    )

    identity: Mapped[str] = mapped_column(Text, primary_key=True)
    graph_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("graph_runs.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_request_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("crawl_requests.id", ondelete="CASCADE"),
        index=True,
    )
    crawl_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True))
    edge_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(Text, default="pending")
    output_count: Mapped[int] = mapped_column(Integer, default=0)
    selection: Mapped[dict[str, Any] | None] = mapped_column(
        json_type, nullable=True
    )


class GraphOutboxRecord(Base):
    __tablename__ = "graph_outbox"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_graph_outbox_message_id"),
        CheckConstraint(
            "publish_attempts >= 0", name="ck_graph_outbox_publish_attempts"
        ),
        Index(
            "ix_graph_outbox_ready",
            "published_at",
            "not_before",
            "claim_expires_at",
            postgresql_where=text("published_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=uuid4
    )
    graph_run_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("graph_runs.id", ondelete="CASCADE"),
        index=True,
    )
    message_id: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(json_type)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now
    )
    not_before: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    claim_token: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    claim_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publish_attempts: Mapped[int] = mapped_column(Integer, default=0)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
