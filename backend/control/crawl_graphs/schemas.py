from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CrawlGraphCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)


class CrawlGraphUpdate(CrawlGraphCreate):
    root_node_id: UUID | None = None


class CrawlGraphNodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)


class CrawlGraphNodeUpdate(CrawlGraphNodeCreate):
    pass


class CrawlGraphNodePositionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: float = Field(ge=-1_000_000, le=1_000_000)
    y: float = Field(ge=-1_000_000, le=1_000_000)


class CrawlGraphEdgeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_node_id: UUID
    target_node_id: UUID
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)


class CrawlGraphEdgeUpdate(CrawlGraphEdgeCreate):
    pass


class CrawlGraphNodeRecord(CrawlGraphNodeCreate):
    id: UUID
    graph_id: UUID
    position_x: float | None = None
    position_y: float | None = None
    used_at: datetime | None
    created_at: datetime


class CrawlGraphEdgeRecord(CrawlGraphEdgeCreate):
    id: UUID
    graph_id: UUID
    used_at: datetime | None
    created_at: datetime


class CrawlGraphRecord(CrawlGraphCreate):
    id: UUID
    root_node_id: UUID | None
    created_at: datetime


class CrawlGraphDetail(CrawlGraphRecord):
    nodes: list[CrawlGraphNodeRecord]
    edges: list[CrawlGraphEdgeRecord]


class CrawlGraphListResponse(BaseModel):
    items: list[CrawlGraphRecord]
    total: int


class FrozenGraphNode(BaseModel):
    id: UUID
    name: str


class FrozenGraphEdge(BaseModel):
    id: UUID
    name: str
    source_node_id: UUID
    target_node_id: UUID
    sql: str


class FrozenGraphSnapshot(BaseModel):
    graph_id: UUID
    root_node_id: UUID
    nodes: list[FrozenGraphNode]
    edges: list[FrozenGraphEdge]
