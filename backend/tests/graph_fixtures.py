from uuid import uuid4

from atlas.crawl.control.crawl_graphs.schemas import (
    EdgeDedupeMode,
    FrozenGraphEdge,
    FrozenGraphNode,
    FrozenGraphSnapshot,
)
from atlas.crawl.runtime.navigation_contract import NavigationPackage


def navigation_package() -> NavigationPackage:
    return NavigationPackage(
        object_name="runtime/navigation/test.arrow",
        sha256="0" * 64,
        schema_version=1,
        recipe="recipe",
        row_count=1,
        byte_size=10,
    )


def policy_snapshot(_variant: str = "default") -> dict:
    return {
        "crawl": {
            "id": str(uuid4()),
            "slug": "content-policy-test",
            "scheme": "*",
            "host": "*",
            "path_prefix": "/",
            "path_mode": "prefix",
            "content": {},
        },
        "domain": {
            "id": str(uuid4()),
            "slug": "domain-policy-test",
            "host_match": "*",
            "maximum_concurrency": 4,
            "minimum_request_interval_seconds": 0,
        },
    }


def snapshot(
    *,
    entry: bool = True,
    self_edge: bool = False,
    dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph,
) -> FrozenGraphSnapshot:
    graph_id = uuid4()
    source = FrozenGraphNode(id=uuid4(), name="source")
    target = source if self_edge else FrozenGraphNode(id=uuid4(), name="target")
    edge = FrozenGraphEdge(
        id=uuid4(),
        name="links",
        source_node_id=source.id,
        target_node_id=target.id,
        sql=(
            "SELECT target_url AS url FROM edge.page_links "
            "WHERE crawl_id = $crawl_id LIMIT 10"
        ),
        dedupe_mode=dedupe_mode,
    )
    return FrozenGraphSnapshot(
        graph_id=graph_id,
        root_node_id=source.id if entry else uuid4(),
        nodes=[source, target] if source != target else [source],
        edges=[edge],
    )
