from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, Float, ForeignKey, ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base
from db.types import utc_now
from .schemas import EdgeDedupeMode


class CrawlGraph(Base):
    __tablename__ = "crawl_graphs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "root_node_id"],
            ["crawl_graph_nodes.graph_id", "crawl_graph_nodes.id"],
            name="fk_crawl_graphs_root_node",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    slug: Mapped[str] = mapped_column(Text, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_node_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    nodes: Mapped[list[CrawlGraphNode]] = relationship(
        back_populates="graph", cascade="all, delete-orphan", passive_deletes=True,
        foreign_keys="CrawlGraphNode.graph_id",
    )
    edges: Mapped[list[CrawlGraphEdge]] = relationship(
        back_populates="graph", cascade="all, delete-orphan", passive_deletes=True
    )


class CrawlGraphNode(Base):
    __tablename__ = "crawl_graph_nodes"
    __table_args__ = (
        UniqueConstraint("graph_id", "name", name="uq_crawl_graph_nodes_graph_name"),
        UniqueConstraint("graph_id", "id", name="uq_crawl_graph_nodes_graph_identity"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    graph_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crawl_graphs.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    position_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    graph: Mapped[CrawlGraph] = relationship(back_populates="nodes", foreign_keys=[graph_id])


class CrawlGraphEdge(Base):
    __tablename__ = "crawl_graph_edges"
    __table_args__ = (
        UniqueConstraint("graph_id", "name", name="uq_crawl_graph_edges_graph_name"),
        ForeignKeyConstraint(
            ["graph_id", "source_node_id"],
            ["crawl_graph_nodes.graph_id", "crawl_graph_nodes.id"],
            name="fk_crawl_graph_edges_source_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["graph_id", "target_node_id"],
            ["crawl_graph_nodes.graph_id", "crawl_graph_nodes.id"],
            name="fk_crawl_graph_edges_target_node",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    graph_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("crawl_graphs.id", ondelete="CASCADE"), index=True
    )
    source_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    target_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sql: Mapped[str] = mapped_column(Text)
    dedupe_mode: Mapped[EdgeDedupeMode] = mapped_column(
        Enum(EdgeDedupeMode, name="crawl_graph_edge_dedupe_mode"),
        default=EdgeDedupeMode.graph,
        server_default=EdgeDedupeMode.graph.value,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    graph: Mapped[CrawlGraph] = relationship(back_populates="edges")
