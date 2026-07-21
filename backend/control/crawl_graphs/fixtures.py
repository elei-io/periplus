"""Typed source-controlled crawl-graph fixture definitions."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from .schemas import EdgeDedupeMode


SEEDED_CRAWL_GRAPH_SLUGS = frozenset(
    {
        "single-page",
        "same-origin-depth-1",
        "same-origin-depth-2",
        "same-origin-depth-3",
        "same-site-depth-1",
        "same-site-depth-2",
        "same-site-depth-3",
        "cross-site-depth-1",
        "cross-site-depth-2",
        "cross-site-depth-3",
        "all-links-depth-1",
        "all-links-depth-2",
        "all-links-depth-3",
        "cross-site-then-same-site",
        "same-site-then-cross-site",
        "same-site-query-recursive",
    }
)
SYSTEM_CRAWL_GRAPH_SLUGS = SEEDED_CRAWL_GRAPH_SLUGS


class SeedCrawlGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)


class SeedCrawlGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    target: str
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2_000)
    sql: str = Field(min_length=1, max_length=100_000)
    dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph


class SeedCrawlGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    description: str = Field(min_length=1, max_length=2_000)
    root: str
    nodes: tuple[SeedCrawlGraphNode, ...] = Field(min_length=1)
    edges: tuple[SeedCrawlGraphEdge, ...] = ()

    @model_validator(mode="after")
    def validate_graph(self) -> SeedCrawlGraph:
        node_names = [node.name for node in self.nodes]
        edge_names = [edge.name for edge in self.edges]
        if len(node_names) != len(set(node_names)):
            raise ValueError(f"Graph {self.slug!r} has duplicate node names.")
        if len(edge_names) != len(set(edge_names)):
            raise ValueError(f"Graph {self.slug!r} has duplicate edge names.")
        if self.root not in node_names:
            raise ValueError(f"Graph {self.slug!r} root does not name a node.")
        for edge in self.edges:
            if edge.source not in node_names or edge.target not in node_names:
                raise ValueError(
                    f"Graph {self.slug!r} edge {edge.name!r} has an unknown endpoint."
                )
        return self


_fixture_adapter = TypeAdapter(tuple[SeedCrawlGraph, ...])


def load_seeded_crawl_graphs(fixtures_root: Path) -> tuple[SeedCrawlGraph, ...]:
    path = fixtures_root / "crawl_graphs" / "library.json"
    fixtures = _fixture_adapter.validate_json(path.read_bytes())
    slugs = [fixture.slug for fixture in fixtures]
    if len(slugs) != len(set(slugs)):
        raise ValueError("Crawl-graph fixtures contain duplicate slugs.")
    if set(slugs) != SEEDED_CRAWL_GRAPH_SLUGS:
        missing = sorted(SEEDED_CRAWL_GRAPH_SLUGS - set(slugs))
        unexpected = sorted(set(slugs) - SEEDED_CRAWL_GRAPH_SLUGS)
        raise ValueError(
            "Crawl-graph fixture library does not match its declared slugs: "
            f"missing={missing}, unexpected={unexpected}."
        )
    return fixtures
