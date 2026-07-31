"""Compare flat, document-packed, and block-packed DOM material layouts.

The benchmark reads the configured Atlas DuckLake but writes only disposable local
Parquet datasets. It exercises the same logical query suite against all layouts and
uses the current flat layout as the differential correctness oracle.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter
from typing import Any

import duckdb

from atlas.platform.catalogue.config import catalogue_config_from_env
from atlas.platform.catalogue.connection import DuckLakeConnectionFactory


BLOCK_ELEMENTS = 2_048
LAYOUTS = ("flat", "document", "block")


@dataclass(frozen=True, slots=True)
class QueryCase:
    name: str
    use_case: str
    sql: str


@dataclass(frozen=True, slots=True)
class Measurement:
    query: str
    layout: str
    normal_ms: float
    warm_ms: tuple[float, ...]
    median_warm_ms: float
    cumulative_rows_scanned: tuple[int, ...]
    peak_buffer_bytes: tuple[int, ...]
    peak_temp_bytes: tuple[int, ...]
    result_rows: int
    result_digest: str
    blocking_operators: tuple[dict[str, Any], ...]


QUERY_CASES = (
    QueryCase(
        "global_tag_frequency",
        "Compare the dominant structural vocabulary across the retained corpus.",
        """
        SELECT tag_name,
               count(*)::BIGINT AS element_count,
               count(DISTINCT content_id)::BIGINT AS content_count
        FROM {elements}
        GROUP BY tag_name
        ORDER BY element_count DESC, tag_name
        LIMIT 30
        """,
    ),
    QueryCase(
        "cross_site_structure_trends",
        "Compare semantic and documentation-oriented structures across major sites.",
        """
        SELECT scope.hostname,
               element.tag_name,
               count(*)::BIGINT AS element_count,
               count(DISTINCT element.content_id)::BIGINT AS content_count
        FROM {elements} AS element
        JOIN bench_scope AS scope USING (content_id)
        WHERE scope.hostname IN (
            'developer.mozilla.org', 'docs.python.org',
            'www.postgresql.org', 'numpy.org', 'pandas.pydata.org',
            'commoncrawl.org'
        )
          AND element.tag_name IN (
            'main', 'article', 'section', 'nav', 'pre', 'code',
            'table', 'form', 'aside'
        )
        GROUP BY scope.hostname, element.tag_name
        ORDER BY scope.hostname, element_count DESC, element.tag_name
        """,
    ),
    QueryCase(
        "bounded_dom_distribution",
        "Measure DOM size, depth, and attributed-element distributions over 1,000 pages.",
        """
        WITH per_content AS (
            SELECT element.content_id,
                   count(*)::BIGINT AS element_count,
                   max(element.depth)::INTEGER AS max_depth,
                   count_if(len(map_keys(element.attributes)) > 0)::BIGINT
                       AS attributed_elements
            FROM {elements} AS element
            JOIN bench_scope AS scope USING (content_id)
            WHERE scope.global_rank <= 1000
            GROUP BY element.content_id
        )
        SELECT count(*)::BIGINT AS content_count,
               sum(element_count)::HUGEINT AS element_count,
               min(element_count)::BIGINT AS smallest_dom,
               max(element_count)::BIGINT AS largest_dom,
               round(avg(element_count), 2) AS average_dom,
               quantile_cont(element_count, [0.5, 0.9, 0.99]) AS dom_quantiles,
               max(max_depth)::INTEGER AS deepest_dom,
               sum(attributed_elements)::HUGEINT AS attributed_elements
        FROM per_content
        """,
    ),
    QueryCase(
        "record_shape_discovery",
        "Identify repeated record-like structural shapes without knowing a site's schema.",
        """
        WITH selected AS MATERIALIZED (
            SELECT element.*
            FROM {elements} AS element
            JOIN bench_scope AS scope USING (content_id)
            WHERE scope.global_rank <= 500
        ),
        child_counts AS (
            SELECT content_id, parent_element_index AS element_index,
                   count(*)::INTEGER AS child_count
            FROM selected
            WHERE parent_element_index IS NOT NULL
            GROUP BY content_id, parent_element_index
        )
        SELECT element.tag_name,
               array_to_string(list_sort(map_keys(element.attributes)), ',')
                   AS attribute_shape,
               least(coalesce(child.child_count, 0), 10)::INTEGER
                   AS bounded_child_count,
               count(*)::BIGINT AS occurrences,
               count(DISTINCT element.content_id)::BIGINT AS contents
        FROM selected AS element
        LEFT JOIN child_counts AS child
          USING (content_id, element_index)
        WHERE element.tag_name IN ('article', 'section', 'li', 'tr', 'div')
        GROUP BY element.tag_name, attribute_shape, bounded_child_count
        HAVING count(*) >= 5
        ORDER BY occurrences DESC, element.tag_name,
                 attribute_shape, bounded_child_count
        LIMIT 50
        """,
    ),
    QueryCase(
        "image_accessibility_by_site",
        "Audit image alternative-text coverage across current pages from major sites.",
        """
        SELECT scope.hostname,
               count(*)::BIGINT AS images,
               count_if(
                   dom.get_attribute(element.attributes, 'alt') IS NULL
               )::BIGINT AS missing_alt,
               count_if(
                   dom.get_attribute(element.attributes, 'alt') = ''
               )::BIGINT AS empty_alt
        FROM {elements} AS element
        JOIN bench_scope AS scope USING (content_id)
        WHERE scope.global_rank <= 1500
          AND element.tag_name = 'img'
        GROUP BY scope.hostname
        ORDER BY images DESC, scope.hostname
        """,
    ),
    QueryCase(
        "documentation_term_trends",
        "Compare deprecation, experimental, and compatibility language across documentation sites.",
        """
        SELECT scope.hostname,
               count_if(contains(lower(element.direct_text), 'deprecated'))::BIGINT
                   AS deprecated_mentions,
               count_if(contains(lower(element.direct_text), 'experimental'))::BIGINT
                   AS experimental_mentions,
               count_if(contains(lower(element.direct_text), 'compatib'))::BIGINT
                   AS compatibility_mentions
        FROM {elements} AS element
        JOIN bench_scope AS scope USING (content_id)
        WHERE scope.global_rank <= 1500
          AND scope.hostname IN (
              'developer.mozilla.org', 'docs.python.org',
              'www.postgresql.org', 'numpy.org', 'pandas.pydata.org'
          )
        GROUP BY scope.hostname
        ORDER BY scope.hostname
        """,
    ),
    QueryCase(
        "extract_main_links",
        "Extract navigable links from the main content of 100 MDN pages.",
        """
        SELECT scope.content_id, match.element_index,
               dom.get_attribute(match.attributes, 'href') AS href
        FROM bench_scope AS scope
        JOIN LATERAL {selector}(
            scope.content_id, 'main a[href]'
        ) AS match ON true
        WHERE scope.hostname = 'developer.mozilla.org'
          AND scope.host_rank <= 100
        ORDER BY scope.content_id, match.element_index
        LIMIT 5000
        """,
    ),
    QueryCase(
        "extract_form_fields",
        "Extract named form controls from 500 representative current pages.",
        """
        SELECT scope.content_id, match.element_index, match.tag_name,
               dom.get_attribute(match.attributes, 'name') AS field_name,
               dom.get_attribute(match.attributes, 'type') AS field_type
        FROM bench_scope AS scope
        JOIN LATERAL {selector}(
            scope.content_id,
            'form input[name], form select[name], form textarea[name]'
        ) AS match ON true
        WHERE scope.global_rank <= 500
        ORDER BY scope.content_id, match.element_index
        LIMIT 5000
        """,
    ),
    QueryCase(
        "extract_headings",
        "Extract primary article and main-content headings from 500 current pages.",
        """
        SELECT scope.content_id, match.element_index, match.tag_name,
               match.direct_text
        FROM bench_scope AS scope
        JOIN LATERAL {selector}(
            scope.content_id,
            'main h1, main h2, article h1, article h2'
        ) AS match ON true
        WHERE scope.global_rank <= 500
        ORDER BY scope.content_id, match.element_index
        LIMIT 5000
        """,
    ),
)


FLAT_SELECT_MACRO = """
CREATE OR REPLACE TEMP MACRO bench_flat_select_all(
    selected_content_id, css_selector
) AS TABLE
SELECT
    content_id,
    element_index,
    parent_index AS parent_element_index,
    subtree_end_index,
    depth,
    child_index AS sibling_index,
    tag AS tag_name,
    namespace,
    attributes,
    text_direct AS direct_text,
    text_tail AS tail_text
FROM atlas_dom_select_all(
    (
        SELECT element.*, false AS _atlas_document_end
        FROM bench_flat_rows AS element
        JOIN (SELECT selected_content_id AS content_id)
             AS selected USING (content_id)
        UNION ALL
        SELECT
            selected_content_id,
            NULL::INTEGER, NULL::INTEGER, NULL::INTEGER, NULL::INTEGER,
            NULL::INTEGER, NULL::VARCHAR, NULL::VARCHAR,
            NULL::MAP(VARCHAR, VARCHAR), NULL::VARCHAR, NULL::VARCHAR,
            true
        ORDER BY content_id, _atlas_document_end, element_index
    ),
    css_selector
)
"""


DOCUMENT_SELECT_MACRO = """
CREATE OR REPLACE TEMP MACRO bench_document_select_all(
    selected_content_id, css_selector
) AS TABLE
WITH document AS MATERIALIZED (
    SELECT content_id, nodes
    FROM bench_document_rows
    WHERE content_id = selected_content_id
),
matched AS MATERIALIZED (
    SELECT content_id, nodes,
           atlas_dom_query_selector_all(nodes, css_selector) AS indexes
    FROM document
)
SELECT
    content_id,
    node.element_index,
    node.parent_index AS parent_element_index,
    node.subtree_end_index,
    node.depth,
    node.child_index AS sibling_index,
    node.tag AS tag_name,
    node.namespace,
    node.attributes,
    node.text_direct AS direct_text,
    node.text_tail AS tail_text
FROM matched,
UNNEST(
    list_select(
        nodes,
        list_transform(indexes, element_index -> element_index + 1)
    )
) AS selected(node)
"""


BLOCK_SELECT_MACRO = """
CREATE OR REPLACE TEMP MACRO bench_block_select_all(
    selected_content_id, css_selector
) AS TABLE
WITH document_lists AS MATERIALIZED (
    SELECT
        content_id,
        flatten(list(element_indexes ORDER BY block_index)) AS element_indexes,
        flatten(list(parent_indexes ORDER BY block_index)) AS parent_indexes,
        flatten(list(subtree_end_indexes ORDER BY block_index)) AS subtree_end_indexes,
        flatten(list(depths ORDER BY block_index)) AS depths,
        flatten(list(child_indexes ORDER BY block_index)) AS child_indexes,
        flatten(list(tags ORDER BY block_index)) AS tags,
        flatten(list(namespaces ORDER BY block_index)) AS namespaces,
        flatten(list(attribute_maps ORDER BY block_index)) AS attribute_maps,
        flatten(list(direct_texts ORDER BY block_index)) AS direct_texts,
        flatten(list(tail_texts ORDER BY block_index)) AS tail_texts
    FROM bench_block_rows
    WHERE content_id = selected_content_id
    GROUP BY content_id
),
document AS MATERIALIZED (
    SELECT content_id,
           list_transform(
               range(1, len(element_indexes) + 1),
               position -> struct_pack(
                   element_index := element_indexes[position],
                   parent_index := parent_indexes[position],
                   subtree_end_index := subtree_end_indexes[position],
                   depth := depths[position],
                   child_index := child_indexes[position],
                   tag := tags[position],
                   namespace := namespaces[position],
                   attributes := attribute_maps[position],
                   text_direct := direct_texts[position],
                   text_tail := tail_texts[position]
               )
           ) AS nodes
    FROM document_lists
),
matched AS MATERIALIZED (
    SELECT content_id, nodes,
           atlas_dom_query_selector_all(nodes, css_selector) AS indexes
    FROM document
)
SELECT
    content_id,
    node.element_index,
    node.parent_index AS parent_element_index,
    node.subtree_end_index,
    node.depth,
    node.child_index AS sibling_index,
    node.tag AS tag_name,
    node.namespace,
    node.attributes,
    node.text_direct AS direct_text,
    node.text_tail AS tail_text
FROM matched,
UNNEST(
    list_select(
        nodes,
        list_transform(indexes, element_index -> element_index + 1)
    )
) AS selected(node)
"""


def _literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _paths(workspace: Path) -> dict[str, Path]:
    return {
        "flat": workspace / "flat_elements.parquet",
        "document": workspace / "document_elements",
        "block": workspace / "block_elements",
        "scope": workspace / "content_scope.parquet",
        "report": workspace / "report.json",
    }


def _dataset_glob(path: Path) -> Path:
    return path / "*.parquet" if path.is_dir() else path


def _dataset_bytes(path: Path) -> int:
    if path.is_dir():
        return sum(item.stat().st_size for item in path.glob("*.parquet"))
    return path.stat().st_size


def _layout_ready(path: Path, *, partitioned: bool) -> bool:
    if partitioned:
        return path.is_dir() and len(tuple(path.glob("*.parquet"))) == 16
    return path.is_file() and path.stat().st_size > 0


def _copy_sql(query: str, path: Path, *, row_group_size: int) -> str:
    return (
        f"COPY ({query}) TO {_literal(path)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD, "
        f"ROW_GROUP_SIZE {row_group_size})"
    )


def build_layouts(workspace: Path, *, rebuild: bool) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    paths = _paths(workspace)
    ready = (
        _layout_ready(paths["flat"], partitioned=False)
        and _layout_ready(paths["document"], partitioned=True)
        and _layout_ready(paths["block"], partitioned=True)
        and _layout_ready(paths["scope"], partitioned=False)
    )
    if not rebuild and ready:
        return {
            "reused": True,
            "build_ms": 0.0,
            "bytes": {name: _dataset_bytes(paths[name]) for name in LAYOUTS},
        }
    paths["flat"].unlink(missing_ok=True)
    paths["scope"].unlink(missing_ok=True)
    for name in ("document", "block"):
        paths[name].mkdir(parents=True, exist_ok=True)
        for item in paths[name].glob("*.parquet"):
            item.unlink()

    connection = DuckLakeConnectionFactory(
        catalogue_config_from_env()
    ).connect(read_only=True, override_data_path=True)
    connection.execute("SET threads = 2")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute(
        "SET temp_directory = " + _literal(workspace / "duckdb-temp")
    )
    started = perf_counter()
    try:
        connection.execute(
            _copy_sql(
                """
                SELECT content_sha256 AS content_id,
                       element_index, parent_index, subtree_end_index,
                       depth, child_index, tag, namespace, attributes,
                       text_direct, text_tail
                FROM atlas.material.html_elements
                ORDER BY content_sha256, element_index
                """,
                paths["flat"],
                row_group_size=122_880,
            )
        )
        flat_source = "read_parquet(" + _literal(paths["flat"]) + ")"
        for prefix in "0123456789abcdef":
            document_path = paths["document"] / f"part-{prefix}.parquet"
            block_path = paths["block"] / f"part-{prefix}.parquet"
            predicate = "content_id LIKE " + _literal(prefix + "%")
            connection.execute(
                _copy_sql(
                    f"""
                    SELECT content_id,
                           list(
                               struct_pack(
                                   element_index := element_index,
                                   parent_index := parent_index,
                                   subtree_end_index := subtree_end_index,
                                   depth := depth,
                                   child_index := child_index,
                                   tag := tag,
                                   namespace := namespace,
                                   attributes := attributes,
                                   text_direct := text_direct,
                                   text_tail := text_tail
                               )
                               ORDER BY element_index
                           ) AS nodes
                    FROM {flat_source}
                    WHERE {predicate}
                    GROUP BY content_id
                    ORDER BY content_id
                    """,
                    document_path,
                    row_group_size=2_048,
                )
            )
            connection.execute(
                _copy_sql(
                    f"""
                    SELECT content_id,
                           (element_index // {BLOCK_ELEMENTS})::INTEGER AS block_index,
                           list(element_index ORDER BY element_index) AS element_indexes,
                           list(parent_index ORDER BY element_index) AS parent_indexes,
                           list(subtree_end_index ORDER BY element_index) AS subtree_end_indexes,
                           list(depth ORDER BY element_index) AS depths,
                           list(child_index ORDER BY element_index) AS child_indexes,
                           list(tag ORDER BY element_index) AS tags,
                           list(namespace ORDER BY element_index) AS namespaces,
                           list(attributes ORDER BY element_index) AS attribute_maps,
                           list(text_direct ORDER BY element_index) AS direct_texts,
                           list(text_tail ORDER BY element_index) AS tail_texts
                    FROM {flat_source}
                    WHERE {predicate}
                    GROUP BY content_id, block_index
                    ORDER BY content_id, block_index
                    """,
                    block_path,
                    row_group_size=8_192,
                )
            )
        connection.execute(
            _copy_sql(
                """
                WITH candidates AS (
                    SELECT visit.content_id, page.hostname, page.url,
                           row_number() OVER (
                               PARTITION BY visit.content_id
                               ORDER BY page.url, page.latest_page_visit_id
                           ) AS content_row
                    FROM atlas.web.page AS page
                    JOIN atlas.web.page_visit AS visit
                      ON visit.page_visit_id = page.latest_page_visit_id
                    WHERE visit.content_id IS NOT NULL
                ),
                selected AS (
                    SELECT content_id, hostname, url
                    FROM candidates
                    WHERE content_row = 1
                )
                SELECT content_id, hostname, url,
                       row_number() OVER (
                           ORDER BY hash(content_id), content_id
                       ) AS global_rank,
                       row_number() OVER (
                           PARTITION BY hostname
                           ORDER BY hash(content_id), content_id
                       ) AS host_rank
                FROM selected
                ORDER BY content_id
                """,
                paths["scope"],
                row_group_size=2_048,
            )
        )
    finally:
        connection.close()
    return {
        "reused": False,
        "build_ms": (perf_counter() - started) * 1000,
        "bytes": {name: _dataset_bytes(paths[name]) for name in LAYOUTS},
    }


def _benchmark_connection(paths: dict[str, Path]) -> duckdb.DuckDBPyConnection:
    config = catalogue_config_from_env()
    connection = duckdb.connect(
        ":memory:", config={"allow_unsigned_extensions": "true"}
    )
    connection.load_extension(str(config.resolved_extension_path()))
    connection.execute("SET threads = 4")
    connection.execute("SET preserve_insertion_order = false")
    connection.execute(
        "CREATE TEMP VIEW bench_scope AS SELECT * FROM read_parquet("
        + _literal(paths["scope"])
        + ")"
    )
    connection.execute(
        "CREATE TEMP VIEW bench_flat_rows AS "
        "SELECT * FROM read_parquet(" + _literal(paths["flat"]) + ")"
    )
    connection.execute(
        """
        CREATE TEMP VIEW bench_flat_element AS
        SELECT content_id, element_index,
               parent_index AS parent_element_index,
               subtree_end_index, depth,
               child_index AS sibling_index,
               tag AS tag_name, namespace, attributes,
               text_direct AS direct_text, text_tail AS tail_text
        FROM bench_flat_rows
        """
    )
    connection.execute(
        "CREATE TEMP VIEW bench_document_rows AS "
        "SELECT * FROM read_parquet("
        + _literal(_dataset_glob(paths["document"]))
        + ")"
    )
    connection.execute(
        """
        CREATE TEMP VIEW bench_document_element AS
        SELECT document.content_id,
               node.element_index,
               node.parent_index AS parent_element_index,
               node.subtree_end_index,
               node.depth,
               node.child_index AS sibling_index,
               node.tag AS tag_name,
               node.namespace,
               node.attributes,
               node.text_direct AS direct_text,
               node.text_tail AS tail_text
        FROM bench_document_rows AS document,
             UNNEST(document.nodes) AS expanded(node)
        """
    )
    connection.execute(
        "CREATE TEMP VIEW bench_block_rows AS "
        "SELECT * FROM read_parquet("
        + _literal(_dataset_glob(paths["block"]))
        + ")"
    )
    connection.execute(
        """
        CREATE TEMP VIEW bench_block_element AS
        SELECT block.content_id,
               block.element_indexes[position] AS element_index,
               block.parent_indexes[position] AS parent_element_index,
               block.subtree_end_indexes[position] AS subtree_end_index,
               block.depths[position] AS depth,
               block.child_indexes[position] AS sibling_index,
               block.tags[position] AS tag_name,
               block.namespaces[position] AS namespace,
               block.attribute_maps[position] AS attributes,
               block.direct_texts[position] AS direct_text,
               block.tail_texts[position] AS tail_text
        FROM bench_block_rows AS block,
             LATERAL range(
                 1, len(block.element_indexes) + 1
             ) AS positions(position)
        """
    )
    connection.execute("CREATE SCHEMA dom")
    connection.execute(
        "CREATE MACRO dom.get_attribute(element_attributes, attribute_name) "
        "AS map_extract_value(element_attributes, attribute_name)"
    )
    connection.execute(FLAT_SELECT_MACRO)
    connection.execute(DOCUMENT_SELECT_MACRO)
    connection.execute(BLOCK_SELECT_MACRO)
    return connection


def _render_query(case: QueryCase, layout: str) -> str:
    return case.sql.format(
        elements={
            "flat": "bench_flat_element",
            "document": "bench_document_element",
            "block": "bench_block_element",
        }[layout],
        selector={
            "flat": "bench_flat_select_all",
            "document": "bench_document_select_all",
            "block": "bench_block_select_all",
        }[layout],
    )


def _canonical(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_canonical(item) for item in value]
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _result_digest(rows: list[tuple]) -> str:
    rendered = sorted(
        json.dumps(_canonical(row), sort_keys=True, separators=(",", ":"))
        for row in rows
    )
    return hashlib.sha256("\n".join(rendered).encode()).hexdigest()


def _blocking_operators(profile: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    found: list[dict[str, Any]] = []

    def visit(node: dict[str, Any]) -> None:
        name = str(node.get("operator_name", ""))
        children = node.get("children") or []
        if name in {
            "HASH_GROUP_BY", "PERFECT_HASH_GROUP_BY", "ORDER_BY", "TOP_N",
            "WINDOW", "HASH_JOIN", "RIGHT_DELIM_JOIN", "LEFT_DELIM_JOIN",
            "MATERIALIZED_CTE", "UNNEST",
        }:
            before = None
            if children:
                before = children[0].get("operator_cardinality")
            found.append(
                {
                    "operator": name,
                    "before": before,
                    "after": node.get("operator_cardinality"),
                    "rows_scanned": node.get("operator_rows_scanned"),
                }
            )
        for child in children:
            visit(child)

    visit(profile)
    return tuple(found)


def measure_case(
    paths: dict[str, Path], case: QueryCase, layout: str, *, warm_runs: int
) -> tuple[Measurement, list[tuple]]:
    connection = _benchmark_connection(paths)
    sql = _render_query(case, layout)
    try:
        started = perf_counter()
        rows = connection.execute(sql).fetchall()
        normal_ms = (perf_counter() - started) * 1000
        warm: list[float] = []
        scanned: list[int] = []
        peak_buffer: list[int] = []
        peak_temp: list[int] = []
        blocking: tuple[dict[str, Any], ...] = ()
        for _ in range(warm_runs):
            payload = connection.execute(
                "EXPLAIN (ANALYZE, FORMAT JSON) " + sql
            ).fetchone()[1]
            profile = json.loads(payload)
            warm.append(float(profile["latency"]) * 1000)
            scanned.append(int(profile.get("cumulative_rows_scanned", 0)))
            peak_buffer.append(int(profile.get("system_peak_buffer_memory", 0)))
            peak_temp.append(int(profile.get("system_peak_temp_dir_size", 0)))
            blocking = _blocking_operators(profile)
        measurement = Measurement(
            query=case.name,
            layout=layout,
            normal_ms=normal_ms,
            warm_ms=tuple(warm),
            median_warm_ms=median(warm),
            cumulative_rows_scanned=tuple(scanned),
            peak_buffer_bytes=tuple(peak_buffer),
            peak_temp_bytes=tuple(peak_temp),
            result_rows=len(rows),
            result_digest=_result_digest(rows),
            blocking_operators=blocking,
        )
        return measurement, rows
    finally:
        connection.close()


def run_benchmark(
    workspace: Path, *, rebuild: bool, warm_runs: int
) -> dict[str, Any]:
    build = build_layouts(workspace, rebuild=rebuild)
    paths = _paths(workspace)
    measurements: list[Measurement] = []
    correctness: dict[str, dict[str, bool]] = {}
    for case_index, case in enumerate(QUERY_CASES):
        order = tuple(
            LAYOUTS[(case_index + offset) % len(LAYOUTS)]
            for offset in range(len(LAYOUTS))
        )
        rows_by_layout: dict[str, list[tuple]] = {}
        measured_by_layout: dict[str, Measurement] = {}
        for layout in order:
            measured, rows = measure_case(
                paths, case, layout, warm_runs=warm_runs
            )
            rows_by_layout[layout] = rows
            measured_by_layout[layout] = measured
            print(
                f"query={case.name} layout={layout} "
                f"normal_ms={measured.normal_ms:.3f} "
                f"warm_median_ms={measured.median_warm_ms:.3f} "
                f"rows={measured.result_rows}"
            )
        baseline = _result_digest(rows_by_layout["flat"])
        correctness[case.name] = {
            layout: _result_digest(rows) == baseline
            for layout, rows in rows_by_layout.items()
        }
        measurements.extend(measured_by_layout[layout] for layout in LAYOUTS)
    report = {
        "workspace": str(workspace),
        "block_elements": BLOCK_ELEMENTS,
        "query_cases": [asdict(case) for case in QUERY_CASES],
        "build": build,
        "correctness": correctness,
        "measurements": [asdict(item) for item in measurements],
    }
    paths["report"].write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("/tmp/atlas-dom-layout-benchmark"),
    )
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--warm-runs", type=int, default=2)
    arguments = parser.parse_args()
    if arguments.warm_runs < 1:
        parser.error("--warm-runs must be positive")
    report = run_benchmark(
        arguments.workspace.resolve(),
        rebuild=arguments.rebuild,
        warm_runs=arguments.warm_runs,
    )
    failed = {
        query: layouts
        for query, layouts in report["correctness"].items()
        if not all(layouts.values())
    }
    print(f"report={_paths(arguments.workspace.resolve())['report']}")
    print(f"correctness_failures={failed!r}")


if __name__ == "__main__":
    main()
