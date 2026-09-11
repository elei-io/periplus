"""Controlled fixed-key extraction growth test; writes only a disposable DuckLake."""
from dataclasses import replace
import argparse
import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pyarrow.parquet as pq

from term_surface import Experiment, add_dom, install_surface, prepare
from vocabulary_materialization import tokenizer_metadata
from vocabulary_lookup import measure_stage
from periplus.query.benchmarking import deadline, load_case
from periplus.query.content_scope import prose_heading_scope


ROOT = Path(__file__).resolve().parent


def source(index):
    phrase = "monkeys uniquematch" if index == 0 else "monkeys" if index == 1 else "garden animals"
    return (f"<h1>Title <em>{index}</em></h1><h2></h2><p>{phrase}</p>"
            + "".join(f"<section><h3>Section {j}</h3><p>ordinary <b>body</b> text {index} {j}</p></section>"
                      for j in range(12)))


def quote(value):
    return "'" + value.replace("'", "''") + "'"


def scoped_source(index, match_counts):
    if tuple(match_counts) == (1, 2):
        return source(index)
    terms = ' '.join(f'matchscope{count}end' for count in match_counts if index < count)
    return source(index) + f'<p>{terms}</p>'


def variants(relation, keys, term):
    columns = "d.content_id,d.text" if relation == "prose" else "d.content_id,d.node_index,d.level,d.text"
    order = " ORDER BY ALL"
    base = f"SELECT {columns} FROM public_v1.{relation} d"
    key_sql = "SELECT content_id FROM (VALUES " + ",".join(f"({quote(k)})" for k in keys) + ") k(content_id)"
    discovery = f"SELECT content_id FROM public_v1.term WHERE text={quote(term)}"
    selected = ",".join(map(quote, keys))
    queries = {
        "literal": base + f" WHERE d.content_id IN ({selected})" + order,
        # Diagnostic exact-equality access, not a proposed public API requirement.
        "literal_union": " UNION ALL ".join(base + f" WHERE d.content_id={quote(k)}" for k in keys) + order,
        "key_relation": base + f" WHERE d.content_id IN ({key_sql})" + order,
        "term_exists": base + f" WHERE d.content_id IN ({discovery})" + order,
        "term_join": base + f" JOIN ({discovery}) k USING(content_id)" + order,
    }
    if relation == "html_heading":
        # Restrict whole content partitions before heading reconstruction. Reuse the
        # actual catalogue SQL rather than maintaining a second heading definition.
        resources = files("periplus.platform.catalogue").joinpath("sql/public_v1/views")
        heading = resources.joinpath("html_heading.sql").read_text().split(" AS\n", 1)[1].rstrip().rstrip(";")
        heading = heading.replace("public_v1.html_element", "selected_elements").replace("public_v1.html_node", "selected_nodes")
        queries["scoped_inputs"] = f"""WITH keys AS MATERIALIZED ({discovery}),
          selected_elements AS MATERIALIZED (
            SELECT * FROM public_v1.html_element WHERE content_id IN (SELECT content_id FROM keys)),
          selected_nodes AS MATERIALIZED (
            SELECT * FROM public_v1.html_node WHERE content_id IN (SELECT content_id FROM keys))
          SELECT * FROM ({heading}) d ORDER BY ALL"""
        queries["scoped_barrier"] = queries["scoped_inputs"].replace(
            "FROM keys))", "FROM keys) OFFSET 0)")
        # Equivalent only for this controlled fixture; exercise the actual API
        # compiler on the already-public prose driver as well as experimental term.
        public_sql = (f"SELECT {columns} FROM public_v1.html_heading d "
                      "JOIN public_v1.prose p USING(content_id) "
                      f"WHERE p.text LIKE {quote('%' + term + '%')} ORDER BY d.content_id,d.node_index")
        candidate = prose_heading_scope(public_sql)
        assert candidate is not None
        queries["api_prose_native"] = public_sql
        queries["api_prose_barrier"] = candidate.sql
    return queries


def file_evidence(c, table, keys):
    column = "content_sha256" if table.startswith("html_") else "content_id"
    paths = [r[0] for r in c.execute(
        "SELECT data_file FROM ducklake_list_files('periplus',?,schema=>'material')", [table]).fetchall()]
    actual, candidates, row_groups, candidate_rows, candidate_bytes = 0, 0, 0, 0, 0
    for path in paths:
        parquet = pq.ParquetFile(path)
        ci = parquet.schema_arrow.names.index(column)
        hit = bool(set(parquet.read(columns=[column]).column(0).to_pylist()) & set(keys))
        actual += hit
        groups = [parquet.metadata.row_group(i) for i in range(parquet.metadata.num_row_groups)]
        matching = [g for g in groups if any(g.column(ci).statistics.min <= key <= g.column(ci).statistics.max for key in keys)]
        candidates += bool(matching)
        row_groups += len(matching)
        candidate_rows += sum(g.num_rows for g in matching)
        candidate_bytes += sum(sum(g.column(i).total_compressed_size for i in range(g.num_columns)) for g in matching)
    return {"files": len(paths), "bytes": sum(Path(p).stat().st_size for p in paths),
            "actual_matching_files": actual, "range_candidate_files_without_bucket_pruning": candidates,
            "range_candidate_row_groups": row_groups, "range_candidate_rows": candidate_rows,
            "range_candidate_all_column_bytes": candidate_bytes}


def run(scales, batch_size, *, diagnose_join_planning=False, check_broad=False, match_counts=(1, 2),
        dom_row_group_size=None, probe_set_filter=False):
    with TemporaryDirectory(prefix="periplus-term-extraction-") as directory:
        root = Path(directory)
        lake = Experiment(root, postings_buckets=0)
        c = lake.connection
        keys = [hashlib.sha256(scoped_source(i, match_counts).encode()).hexdigest() for i in range(max(match_counts))]
        scopes = [(count, {1: 'uniquematch', 2: 'monkeys'}[count]
                   if tuple(match_counts) == (1, 2) else f'matchscope{count}end') for count in match_counts]
        stages, fixed_results = {}, {}
        try:
            install_surface(c)
            case = load_case(ROOT.parent / "cases/exp-term-extraction")
            with patch.dict(os.environ, {
                "PERIPLUS_DUCKLAKE_ALIAS": "periplus",
                "PERIPLUS_DUCKLAKE_METADATA_PATH": str(root / "metadata.duckdb"),
                "PERIPLUS_DUCKLAKE_DATA_PATH": str(root / "data"),
                "PERIPLUS_DUCKLAKE_METADATA_SCHEMA": "ducklake",
            }):
                previous = 0
                for scale in scales:
                    for offset in range(previous, scale, batch_size):
                        sources = [scoped_source(i, match_counts) for i in range(offset, min(offset + batch_size, scale))]
                        lake.commit(prepare(root / "batch.parquet", sources))
                        add_dom(c, sources, create=offset == 0, row_group_size=dom_row_group_size)
                    previous = scale
                    for maintenance in ("appended", "compacted"):
                        if maintenance == "compacted":
                            for table in ("term_stat", "prose", "html_nodes", "html_elements"):
                                with deadline(c, 60):
                                    c.execute(f"CALL ducklake_merge_adjacent_files('periplus','{table}',schema=>'material')").fetchall()
                        name = f"{scale}_{maintenance}"
                        measured, evidence = {}, {}
                        for scope, term in scopes:
                            assert c.execute("SELECT content_id FROM public_v1.term WHERE text=? ORDER BY content_id", [term]).fetchall() == [(k,) for k in sorted(keys[:scope])]
                            evidence[str(scope)] = {t: file_evidence(c, t, keys[:scope]) for t in ("prose", "html_nodes", "html_elements")}
                            for relation in ("prose", "html_heading"):
                                label = f"{scope}_{relation}"
                                selected = variants(relation, keys[:scope], term)
                                if scope > 2:
                                    # Thousands of equality branches are not a scalable
                                    # compiler intervention; compare ordinary IN instead.
                                    selected.pop('literal_union')
                                results = measure_stage(c, replace(case, identifier=f"term-extraction-{label}"), selected)
                                digest = results[0]["literal"]["result_digest"]
                                assert fixed_results.setdefault(label, digest) == digest
                                assert results[0]['literal']['result_rows'] == scope * (14 if relation == 'html_heading' else 1)
                                measured[label] = results
                        stages[name] = {"contents": scale, "queries": measured, "files": evidence}
                        print(json.dumps({"stage": name, "complete": True}), flush=True)
                filter_probe = []
                if probe_set_filter:
                    original = int(c.execute("SELECT current_setting('dynamic_or_filter_threshold')").fetchone()[0])
                    try:
                        # Same snapshot and both setting orders; diagnostic only.
                        for threshold in (original, 1024, 1024, original):
                            c.execute('SET dynamic_or_filter_threshold = ?', [threshold])
                            measurements = {}
                            for scope, term in scopes:
                                selected = variants('html_heading', keys[:scope], term)
                                result = measure_stage(c, case, {name: selected[name] for name in ('scoped_barrier', 'api_prose_barrier')})
                                assert result[0]['scoped_barrier']['result_digest'] == fixed_results[f'{scope}_html_heading']
                                measurements[str(scope)] = result
                            filter_probe.append({'threshold': threshold, 'queries': measurements})
                    finally:
                        c.execute('SET dynamic_or_filter_threshold = ?', [original])
                broad_checks = {}
                if check_broad:
                    for name, term in (("all", "ordinary"), ("empty", "absent")):
                        selected = variants('html_heading', keys[:1], term)
                        broad_checks[name] = measure_stage(c, case, {
                            key: selected[key] for key in ('api_prose_native', 'api_prose_barrier')})
                diagnostics = {}
                if diagnose_join_planning:
                    # Causal probes only. Never disable these passes in runtime.
                    for disabled in ("build_side_probe_side", "join_order", "join_order,build_side_probe_side"):
                        c.execute("SET disabled_optimizers = ?", [disabled])
                        try:
                            measured = {}
                            for relation in ("prose", "html_heading"):
                                selected = variants(relation, keys[:1], "uniquematch")
                                measured[relation] = measure_stage(c, case, selected)
                                assert measured[relation][0]["literal"]["result_digest"] == fixed_results[f"1_{relation}"]
                            diagnostics[disabled] = measured
                        finally:
                            c.execute("SET disabled_optimizers = ''")
            return {"complete": True, "scales": scales, "batch_size": batch_size,
                    "match_counts": match_counts,
                    "dom_row_group_size": dom_row_group_size,
                    "set_filter_probe": filter_probe,
                    "join_planning_diagnostics": diagnostics,
                    "broad_checks": broad_checks,
                    "tokenizer": tokenizer_metadata(), "engine": c.execute("SELECT version()").fetchone()[0],
                    "extensions": c.execute("SELECT extension_name,extension_version FROM duckdb_extensions() WHERE loaded").fetchall(),
                    "settings": c.execute("SELECT name,value FROM duckdb_settings() WHERE name IN ('threads','memory_limit','max_temp_directory_size','dynamic_or_filter_threshold')").fetchall(),
                    "corpus": "synthetic fixed contents; 14 headings per content, including an empty heading",
                    "stages": stages}
        finally:
            lake.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", type=int, nargs="+", default=[100, 1000, 5000])
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--diagnose-join-planning", action="store_true")
    parser.add_argument("--check-broad", action="store_true")
    parser.add_argument("--match-counts", type=int, nargs='+', default=[1, 2])
    parser.add_argument("--dom-row-group-size", type=int)
    parser.add_argument("--probe-set-filter", action='store_true')
    args = parser.parse_args()
    if args.scales != sorted(set(args.scales)) or min(args.scales) < 2 or args.batch_size < 2:
        parser.error("scales must increase from at least two; batch size must be at least two")
    if (args.match_counts != sorted(set(args.match_counts)) or min(args.match_counts) < 1
            or max(args.match_counts) > min(args.scales)):
        parser.error('match counts must be positive, unique and increasing, and fit the first scale')
    if args.diagnose_join_planning and args.match_counts != [1, 2]:
        parser.error('join-planning diagnostics require the original one/two-key fixture')
    if args.dom_row_group_size is not None and args.dom_row_group_size < 2048:
        parser.error('DOM row-group size must be at least 2048')
    report = run(args.scales, args.batch_size, diagnose_join_planning=args.diagnose_join_planning,
                 check_broad=args.check_broad, match_counts=args.match_counts,
                 dom_row_group_size=args.dom_row_group_size, probe_set_filter=args.probe_set_filter)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
