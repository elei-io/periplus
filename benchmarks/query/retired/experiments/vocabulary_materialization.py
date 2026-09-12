"""Disposable DuckLake experiment; deliberately not a production projection.

Run from packages/periplus with uv run python ../../benchmarks/query/experiments/
vocabulary_materialization.py --report ../../.artifacts/query-benchmarks/vocabulary.json
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import perf_counter
from unittest.mock import patch

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from periplus.materialization.tokenization import term_counts, term_tokens, tokenizer_metadata
from periplus.materialization.dom.nodes import parse_document
# Registry discovery must finish before importing an individual projection.
from periplus.materialization import registry  # noqa: F401
from periplus.materialization.projections.prose import _body_text
from periplus.query.benchmarking import load_case, measure_pair


@dataclass(frozen=True)
class Prepared:
    documents: pa.Table
    path: Path


def prepare(path: Path, sources: list[str]) -> Prepared:
    documents = {}
    for source in sources:
        content_id = hashlib.sha256(source.encode()).hexdigest()
        documents[content_id] = _body_text(parse_document(source)[0])
    rows = [(term, content_id, frequency)
            for content_id, text in documents.items()
            for term, frequency in term_counts(text).items()]
    schema = pa.schema([("term", pa.string()), ("content_id", pa.string()),
                        ("frequency", pa.int64())])
    table = pa.Table.from_pylist([
        dict(zip(schema.names, row, strict=True)) for row in rows
    ], schema=schema).sort_by([("term", "ascending"), ("content_id", "ascending")])
    pq.write_table(table, path, compression="zstd")
    return Prepared(pa.table({"content_id": list(documents), "text": list(documents.values())},
                             schema=pa.schema([("content_id", pa.string()), ("text", pa.string())])), path)


class Experiment:
    """Parallel preparation, serialized generation commits, independent cursors.

    The local mutex models the existing generation claim. It is NOT a replacement
    for the production Postgres claim or a test of multi-process fencing.
    """

    def __init__(self, root: Path, *, postings_buckets: int = 8, row_group_size: int = 122880):
        if postings_buckets < 0 or row_group_size < 2048:
            raise ValueError("buckets must be nonnegative and row groups at least 2048 rows")
        self.root = root
        self.lock = Lock()
        self.connection = duckdb.connect(config={"threads": 2, "memory_limit": "512MB"})
        self.connection.execute("LOAD ducklake")
        meta = str(root / "metadata.duckdb").replace("'", "''")
        data = str(root / "data").replace("'", "''")
        self.connection.execute(f"ATTACH 'ducklake:{meta}' AS periplus (DATA_PATH '{data}', METADATA_SCHEMA 'ducklake')")
        self.connection.execute("USE periplus")
        self.connection.execute("CALL periplus.set_option('data_inlining_row_limit', 0)")
        self.connection.execute("CREATE SCHEMA material")
        self.connection.execute("CREATE SCHEMA public_v1")
        self.connection.execute("CREATE TABLE material.vocabulary (term VARCHAR NOT NULL, term_id BIGINT NOT NULL)")
        self.connection.execute("ALTER TABLE material.vocabulary SET SORTED BY (term)")
        self.connection.execute("CREATE TABLE material.term_stat (term_id BIGINT NOT NULL, content_id VARCHAR NOT NULL, frequency BIGINT NOT NULL)")
        if postings_buckets:
            self.connection.execute(f"ALTER TABLE material.term_stat SET PARTITIONED BY (bucket({postings_buckets}, term_id))")
        self.connection.execute("ALTER TABLE material.term_stat SET SORTED BY (term_id, content_id)")
        self.connection.execute(
            "CALL periplus.set_option('parquet_row_group_size', ?, schema=>'material', table_name=>'term_stat')",
            [row_group_size],
        )
        self.connection.execute("CREATE TABLE material.prose (content_id VARCHAR, text VARCHAR)")
        self.connection.execute("ALTER TABLE material.prose SET SORTED BY (content_id)")
        self.connection.execute("CREATE VIEW public_v1.prose AS SELECT * FROM material.prose")

    def close(self):
        self.connection.close()

    def commit(self, batch: Prepared, *, fail_after_vocabulary: bool = False) -> dict:
        started = perf_counter()
        with self.lock:
            acquired = perf_counter()
            c = self.connection.cursor()
            try:
                c.execute("USE periplus")
                c.register("batch_documents", batch.documents)
                c.execute("CREATE TEMP TABLE batch_terms AS SELECT * FROM read_parquet(?)", [str(batch.path)])
                c.execute("BEGIN")
                try:
                    c.execute("""
                        INSERT INTO material.vocabulary
                        WITH missing AS (
                            SELECT DISTINCT term FROM batch_terms b
                            WHERE NOT EXISTS (SELECT 1 FROM material.vocabulary v WHERE v.term = b.term)
                        )
                        SELECT term, (SELECT coalesce(max(term_id), 0) FROM material.vocabulary)
                                     + row_number() OVER (ORDER BY term)
                        FROM missing
                    """)
                    vocabulary_done = perf_counter()
                    if fail_after_vocabulary:
                        raise RuntimeError("injected failure after vocabulary admission")
                    # Replay after a lost receipt replaces exactly the owned content.
                    c.execute("DELETE FROM material.term_stat WHERE content_id IN (SELECT content_id FROM batch_documents)")
                    c.execute("INSERT INTO material.term_stat SELECT v.term_id, b.content_id, b.frequency FROM batch_terms b JOIN material.vocabulary v USING (term)")
                    c.execute("DELETE FROM material.prose WHERE content_id IN (SELECT content_id FROM batch_documents)")
                    c.execute("INSERT INTO material.prose SELECT * FROM batch_documents")
                    c.execute("COMMIT")
                except BaseException:
                    c.execute("ROLLBACK")
                    raise
                return {"claim_wait_ms": (acquired - started) * 1000,
                        "vocabulary_and_staging_ms": (vocabulary_done - acquired) * 1000,
                        "serialized_ms": (perf_counter() - acquired) * 1000}
            finally:
                c.close()

    def logical_rows(self):
        return self.connection.execute("""
            SELECT term, content_id, frequency FROM material.term_stat
            JOIN material.vocabulary USING (term_id) ORDER BY ALL
        """).fetchall()

    def validate(self):
        c = self.connection
        for column in ("term", "term_id"):
            assert c.execute(f"SELECT count(*) - count(DISTINCT {column}) FROM material.vocabulary").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM (SELECT term_id, content_id FROM material.term_stat GROUP BY ALL HAVING count(*) > 1)").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM material.term_stat t ANTI JOIN material.vocabulary v USING(term_id)").fetchone()[0] == 0
        expected = sorted((term, content_id, frequency)
                          for content_id, text in c.execute("SELECT * FROM material.prose").fetchall()
                          for term, frequency in term_counts(text).items())
        assert self.logical_rows() == expected
        return {"contents": c.execute("SELECT count(*) FROM material.prose").fetchone()[0],
                "vocabulary": c.execute("SELECT count(*) FROM material.vocabulary").fetchone()[0],
                "term_stats": len(expected)}


def run(contents: int, batch_size: int, workers: int) -> dict:
    with TemporaryDirectory(prefix="periplus-vocabulary-") as directory:
        root = Path(directory)
        experiment = Experiment(root)
        try:
            # Fixed rare matches; unrelated growth does not change selected identities.
            def source(i):
                phrase = "monkeys in the zoo" if i < 10 else "ordinary garden animals"
                return f"<p>{phrase} {' '.join(f'word{j}' for j in range(100))} document{i}</p>"

            batches = [list(range(start, min(start + batch_size, contents)))
                       for start in range(0, contents, batch_size)]
            def work(item):
                index, ids = item
                before = perf_counter()
                prepared = prepare(root / f"batch-{index}.parquet", [source(i) for i in ids])
                prepare_ms = (perf_counter() - before) * 1000
                return {"prepare_ms": prepare_ms, **experiment.commit(prepared)}

            started = perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                timings = list(pool.map(work, enumerate(batches)))
            elapsed = perf_counter() - started
            counts = experiment.validate()
            before = experiment.logical_rows()
            replay = prepare(root / "replay.parquet", [source(i) for i in batches[0]])
            experiment.commit(replay)
            assert experiment.logical_rows() == before
            counts_after_replay = experiment.validate()
            assert counts == counts_after_replay
            storage = {"lake_bytes_including_replay_obsolete_files": sum(p.stat().st_size for p in (root / "data").rglob("*.parquet")),
                       "prepared_parquet_bytes": sum(p.stat().st_size for p in root.glob("batch-*.parquet"))}
            experiment.close()
            experiment = None
            case_dir = Path(__file__).resolve().parents[1] / "cases" / "exp-term-discovery"
            baseline = load_case(case_dir)
            from dataclasses import replace
            candidate = replace(baseline, sql=(case_dir / "candidate.sql").read_text())
            # Explicit disposable paths: never inherit a production attachment.
            with patch.dict(os.environ, {
                "PERIPLUS_DUCKLAKE_ALIAS": "periplus",
                "PERIPLUS_DUCKLAKE_METADATA_PATH": str(root / "metadata.duckdb"),
                "PERIPLUS_DUCKLAKE_DATA_PATH": str(root / "data"),
                "PERIPLUS_DUCKLAKE_METADATA_SCHEMA": "ducklake",
            }):
                pairs = [measure_pair(baseline, candidate, None, warm_runs=1, candidate_first=order)
                         for order in (False, True)]
            for pair in pairs:
                assert pair["baseline"]["result_digest"] == pair["candidate"]["result_digest"]
                assert pair["baseline"]["columns"] == pair["candidate"]["columns"]
                assert pair["baseline"]["types"] == pair["candidate"]["types"]
            return {"complete": True, "duckdb_version": duckdb.__version__,
                    "tokenizer": tokenizer_metadata(),
                    "metadata_backend": "local DuckDB", "workers": workers,
                    "batch_size": batch_size, **counts, **storage,
                    "materialize_seconds": elapsed, "batches": timings,
                    "query_pairs": pairs, "replay_equal": True}
        finally:
            if experiment is not None:
                experiment.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contents", type=int, default=2000)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if min(args.contents, args.batch_size, args.workers) < 1:
        parser.error("counts must be positive")
    report = run(args.contents, args.batch_size, args.workers)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k not in {"query_pairs", "batches"}}, indent=2))
