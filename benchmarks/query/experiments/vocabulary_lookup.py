"""Diagnose term lookup on a disposable lake using the shared query bench.

Run from packages/periplus: uv run python ../../benchmarks/query/experiments/
vocabulary_lookup.py --report ../../.artifacts/query-benchmarks/vocabulary-lookup.json
"""
from dataclasses import asdict, replace
import argparse
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import pyarrow.parquet as pq

from vocabulary_materialization import Experiment, prepare, tokenizer_metadata
from periplus.query.benchmarking import _measure, deadline, load_case


def source(index):
    phrase = "monkeys in the zoo" if index < 10 else "ordinary garden animals"
    return f"<p>{phrase} {' '.join(f'word{j}' for j in range(100))} document{index}</p>"


def file_evidence(connection, ids):
    """Inspect actual Parquet term ranges and membership, outside query timing."""
    files = connection.execute(
        "SELECT data_file FROM ducklake_list_files('periplus', 'term_stat', schema=>'material')"
    ).fetchall()
    entries = []
    groups = []
    for (filename,) in files:
        parquet = pq.ParquetFile(filename)
        column = parquet.schema_arrow.names.index("term_id")
        stats = [parquet.metadata.row_group(i).column(column).statistics
                 for i in range(parquet.metadata.num_row_groups)]
        terms = set(parquet.read(columns=["term_id"]).column(0).to_pylist())
        entries.append({"bucket": Path(filename).parent.name,
                        "min": min(s.min for s in stats), "max": max(s.max for s in stats),
                        "hits": set(ids.values()) & terms})
        for index, stat in enumerate(stats):
            group = parquet.metadata.row_group(index)
            groups.append({"bucket": Path(filename).parent.name, "min": stat.min, "max": stat.max,
                           "rows": group.num_rows,
                           "column_bytes": sum(group.column(parquet.schema_arrow.names.index(name)).total_compressed_size
                                               for name in ("term_id", "content_id"))})
    terms = {}
    for term, term_id in ids.items():
        actual = [e for e in entries if term_id in e["hits"]]
        buckets = {e["bucket"] for e in actual}
        possible = [e for e in entries if e["bucket"] in buckets and e["min"] <= term_id <= e["max"]]
        terms[term] = {"term_id": term_id, "actual_files": len(actual),
                       "bucket_and_range_candidates": len(possible),
                       "range_examples": [{k: e[k] for k in ("bucket", "min", "max")}
                                          for e in possible[:3]]}
    selected_groups = [g for g in groups if any(
        g["min"] <= term_id <= g["max"] and any(
            term_id in e["hits"] and e["bucket"] == g["bucket"] for e in entries
        ) for term_id in ids.values()
    )]
    return {"total_files": len(entries), "terms": terms,
            "total_file_bytes": sum(Path(f).stat().st_size for (f,) in files),
            "row_groups": len(groups),
            "exact_id_candidate_row_groups": len(selected_groups),
            "exact_id_candidate_rows": sum(g["rows"] for g in selected_groups),
            "exact_id_candidate_column_bytes": sum(g["column_bytes"] for g in selected_groups)}


def dynamic_filters(profile):
    found = []
    def visit(node):
        extra = node.get("extra_info", {})
        if "SCAN" in node.get("operator_name", ""):
            found.append({"table": extra.get("Table"),
                          "filters": extra.get("Filters"),
                          "dynamic_filters": extra.get("Dynamic Filters")})
        for child in node.get("children", []):
            visit(child)
    visit(profile)
    return found


def measure_stage(connection, case, variants):
    results = []
    connection.execute("BEGIN")
    try:
        for order in (list(variants), list(reversed(variants))):
            pair = {}
            for name in order:
                variant = replace(case, sql=variants[name])
                result = asdict(_measure(connection, variant, None, 1))
                with deadline(connection, 60):
                    raw = json.loads(connection.execute(
                        "EXPLAIN (ANALYZE, FORMAT JSON) " + variant.sql
                    ).fetchone()[1])
                result["scan_filters"] = dynamic_filters(raw)
                pair[name] = result
            assert len({(v["result_digest"], v["columns"], v["types"]) for v in pair.values()}) == 1
            results.append(pair)
    finally:
        connection.execute("ROLLBACK")
    return results


def run(contents=20000, batch_size=500, *, postings_buckets=8, row_group_size=122880, append_batches=1):
    with TemporaryDirectory(prefix="periplus-term-lookup-") as directory:
        root = Path(directory)
        lake = Experiment(root, postings_buckets=postings_buckets, row_group_size=row_group_size)
        c = lake.connection
        try:
            # Serial batch order isolates layout from the earlier admission-order race.
            build_started = perf_counter()
            commits = []
            for start in range(0, contents, batch_size):
                commits.append(lake.commit(prepare(root / f"{start}.parquet", [
                    source(i) for i in range(start, min(start + batch_size, contents))
                ])))
            build_seconds = perf_counter() - build_started
            ids = dict(c.execute("SELECT term, term_id FROM material.vocabulary WHERE term IN ('monkeys','zoo')").fetchall())
            case_dir = Path(__file__).resolve().parents[1] / "cases/exp-term-discovery"
            case = load_case(case_dir)
            variants = {
                "vocabulary_join": (case_dir / "candidate.sql").read_text(),
                "literal_in": f"SELECT content_id FROM material.term_stat WHERE term_id IN ({ids['monkeys']},{ids['zoo']}) GROUP BY content_id HAVING count(DISTINCT term_id)=2 ORDER BY content_id",
                "equality_intersection": f"SELECT content_id FROM material.term_stat WHERE term_id={ids['monkeys']} INTERSECT SELECT content_id FROM material.term_stat WHERE term_id={ids['zoo']} ORDER BY content_id",
            }
            stages = {}
            with patch.dict(os.environ, {
                "PERIPLUS_DUCKLAKE_ALIAS": "periplus",
                "PERIPLUS_DUCKLAKE_METADATA_PATH": str(root / "metadata.duckdb"),
                "PERIPLUS_DUCKLAKE_DATA_PATH": str(root / "data"),
                "PERIPLUS_DUCKLAKE_METADATA_SCHEMA": "ducklake",
            }):
                names = ["uncompacted", "compacted", "after_append"]
                if append_batches > 1:
                    names.append("after_more_appends")
                for stage in names:
                    mutation = None
                    if stage == "compacted":
                        started = perf_counter()
                        # Disposable experiment only; production maintenance belongs to LakeDucktor.
                        with deadline(c, 60):
                            merged = c.execute("CALL ducklake_merge_adjacent_files('periplus','term_stat',schema=>'material')").fetchall()
                        mutation = {"merge_seconds": perf_counter() - started, "merges": merged}
                    elif stage == "after_append":
                        mutation = lake.commit(prepare(root / "append.parquet", [
                            source(i) for i in range(contents, contents + batch_size)
                        ]))
                    elif stage == "after_more_appends":
                        mutation = []
                        for index in range(1, append_batches):
                            mutation.append(lake.commit(prepare(root / f"append-{index}.parquet", [
                                source(i) for i in range(contents + index * batch_size, contents + (index + 1) * batch_size)
                            ])))
                    measurements = measure_stage(c, case, variants)
                    stages[stage] = {"measurements": measurements,
                                     "files": file_evidence(c, ids), "mutation": mutation}
            assert len({stage["measurements"][0]["vocabulary_join"]["result_digest"]
                        for stage in stages.values()}) == 1
            return {"complete": True, "contents": contents, "batch_size": batch_size,
                    "tokenizer": tokenizer_metadata(),
                    "postings_buckets": postings_buckets, "row_group_size": row_group_size,
                    "append_batches": append_batches, "build_seconds": build_seconds,
                    "commits": commits,
                    "allocation_order": "serial, no replay", "stages": stages}
        finally:
            lake.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contents", type=int, default=20000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--postings-buckets", type=int, default=8, help="0 means unpartitioned")
    parser.add_argument("--row-group-size", type=int, default=122880)
    parser.add_argument("--append-batches", type=int, default=1)
    args = parser.parse_args()
    if args.contents < 10 or args.batch_size < 10 or args.append_batches < 1:
        parser.error("contents and batch size must be at least ten; append batches positive")
    report = run(args.contents, args.batch_size, postings_buckets=args.postings_buckets,
                 row_group_size=args.row_group_size, append_batches=args.append_batches)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({name: stage["files"] for name, stage in report["stages"].items()}, indent=2))
