"""Bounded detection of a known shared-input barrier, not a plan cost model."""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, JsonValue, TypeAdapter, ValidationError


class _PlanNode(BaseModel):
    name: str
    extra_info: dict[str, JsonValue] = Field(default_factory=dict)
    children: list[_PlanNode] = Field(default_factory=list)


_PLAN = TypeAdapter(list[_PlanNode])
_HTML_TABLES = {'html_nodes', 'html_elements'}


def shared_html_inputs(plan_json: str, *, key_cte: str) -> tuple[str, ...] | None:
    """Find generated shared HTML producers without the selected key domain.

    Return None for unavailable/unrecognized evidence. An empty result means only
    that this particular barrier was not found; it does not certify bounded I/O.
    CTE identities come from the native plan, not estimates or user column names.
    """
    if len(plan_json.encode()) > 1_000_000:
        return None
    try:
        roots = _PLAN.validate_json(plan_json)
    except ValidationError:
        return None
    if not roots:
        return None
    nodes = []
    pending = [(node, 0) for node in roots]
    while pending:
        node, depth = pending.pop()
        if depth > 64 or len(nodes) >= 4096:
            return None
        nodes.append(node)
        pending.extend((child, depth + 1) for child in node.children)
    definitions = {
        str(node.extra_info['Table Index']): node
        for node in nodes if node.name == 'CTE' and 'Table Index' in node.extra_info
    }
    key_indices = {index for index, node in definitions.items()
                   if node.extra_info.get('CTE Name') == key_cte}
    if not any(str(node.extra_info.get('CTE Name', '')).startswith('__common_subplan_')
               for node in definitions.values()):
        return ()
    if not key_indices:
        return None
    tables = set()
    for definition in definitions.values():
        if not str(definition.extra_info.get('CTE Name', '')).startswith('__common_subplan_'):
            continue
        if len(definition.children) != 2:
            return None
        pending = [definition.children[0]]  # Producer only, never its consumers.
        seen = set()
        input_tables = set()
        restricted = False
        while pending:
            node = pending.pop()
            if id(node) in seen:
                continue
            seen.add(id(node))
            if node.name == 'CTE_SCAN':
                index = str(node.extra_info.get('CTE Index', ''))
                if index in key_indices:
                    restricted = True
                    break
                referenced = definitions.get(index)
                if referenced is None or len(referenced.children) != 2:
                    return None
                pending.append(referenced.children[0])
            if node.name.strip() in {'DUCKLAKE_SCAN', 'SEQ_SCAN'}:
                table = str(node.extra_info.get('Table', '')).split('.')[-1]
                if table in _HTML_TABLES:
                    input_tables.add(table)
                    filters = str(node.extra_info.get('Filters', '')) + str(node.extra_info.get('Dynamic Filters', ''))
                    # A static/dynamic key restriction is outside this narrow
                    # detector's proof. Do not label that scan unrestricted.
                    if re.search(r'\b(content_id|content_sha256)\b', filters):
                        restricted = True
            pending.extend(node.children)
        if not restricted:
            tables.update(input_tables)
    return tuple(sorted(tables))
