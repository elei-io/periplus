"""Bounded depth traversal expressed through ordinary nodes and SQL edges."""
from uuid import UUID, uuid5

from periplus.crawl.runtime.graph_queue import FrozenGraphSnapshot, FrozenGraphNode, FrozenGraphEdge

_NAMESPACE = UUID("fb6992b7-1908-4672-93af-207007c83fe2")


def depth_plan_snapshot(depth: int, link_predicate: str) -> FrozenGraphSnapshot:
    graph_id = uuid5(_NAMESPACE, f"depth={depth}:predicate={link_predicate}")
    nodes = [FrozenGraphNode(id=uuid5(graph_id, f"depth-{i}"), name=f"depth-{i}") for i in range(depth + 1)]
    edges = [FrozenGraphEdge(
        id=uuid5(graph_id, f"edge-{i}"), name=f"depth-{i + 1}",
        source_node_id=nodes[i].id, target_node_id=nodes[i + 1].id,
        sql=f"SELECT target_url AS url FROM nav.links WHERE {link_predicate}",
    ) for i in range(depth)]
    return FrozenGraphSnapshot(graph_id=graph_id, root_node_id=nodes[0].id, nodes=nodes, edges=edges)
