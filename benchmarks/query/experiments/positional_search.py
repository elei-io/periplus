"""Disposable real-HTML positional layout and QueryService acceptance bench."""

import argparse
import json
import resource
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from unittest.mock import patch

import pyarrow as pa

from periplus.materialization.document_projection import VisitBatchContext
from periplus.materialization.dom.nodes import parse_document
from periplus.materialization.registry import BY_NAME
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.platform.catalogue.connection import DuckLakeConnectionFactory
from periplus.query.benchmarking import QueryCase, _measure
from periplus.query.service import QueryRequest, QueryService


class ApiBench:
    """Use actual request execution for search; delegate measurement metadata."""

    def __init__(self, service):
        self.service = service

    def interrupt(self):
        self.service.connection.interrupt()

    def execute(self, sql, parameters=None):
        if "FROM duckdb_extensions()" in sql:
            # Metadata collection must not relax the query service IO sandbox.
            connection = DuckLakeConnectionFactory(self.service._config).connect(
                read_only=True
            )
            try:
                rows = connection.execute(sql, parameters).fetchall()
            finally:
                connection.close()

            class MetadataCursor:
                def fetchall(self):
                    return rows

            return MetadataCursor()
        if "FROM public_v1.search(" not in sql:
            return self.service.connection.execute(sql, parameters)
        if parameters:
            raise ValueError("this fixed benchmark uses literal query strings")
        try:
            result = self.service.execute(QueryRequest(sql=sql))
        except Exception as error:
            print(
                json.dumps({"query_error": type(error).__name__, "detail": str(error)}),
                flush=True,
            )
            raise
        if result.truncated:
            raise ValueError("incomplete search benchmark")
        rows = iter(result.rows)

        class Cursor:
            description = tuple(zip(result.columns, result.types))

            def fetchone(self):
                return next(rows, None)

        return Cursor()


def run(input_dir, count, fixture=None):
    sources = sorted(input_dir.glob("*.html"))[:count]
    if len(sources) != count:
        raise ValueError("insufficient retained HTML")
    with (
        nullcontext(str(fixture))
        if fixture
        else TemporaryDirectory(prefix="periplus-positional-")
    ) as temporary:
        root = Path(temporary).resolve()
        if fixture and root.exists() and any(root.iterdir()):
            raise ValueError("fixture directory must be empty")
        root.mkdir(parents=True, exist_ok=True)
        config = CatalogueConfig(
            "periplus", str(root / "metadata.duckdb"), str(root / "data"), "ducklake"
        )
        started = perf_counter()
        parsed = {p.stem: parse_document(p.read_text()) for p in sources}
        context = VisitBatchContext(
            (),
            (),
            (),
            {k: v[1] for k, v in parsed.items()},
            {k: v[0] for k, v in parsed.items()},
            {},
            frozenset(parsed),
        )
        terms = BY_NAME["term"].rows(context).to_pylist()
        context.dictionary_ids["term"] = {r["text"]: i for i, r in enumerate(terms, 1)}
        build_seconds = perf_counter() - started
        print(
            json.dumps(
                {"stage": "tokenized", "seconds": build_seconds, "terms": len(terms)}
            ),
            flush=True,
        )
        with Catalogue(
            config, duckdb_config={"threads": "2", "memory_limit": "4GiB"}
        ) as catalogue:
            catalogue.bootstrap()
            db = catalogue.trusted_connection
            db.register(
                "dictionary",
                pa.table(
                    {
                        "text": list(context.dictionary_ids["term"]),
                        "term_id": list(context.dictionary_ids["term"].values()),
                    }
                ),
            )
            db.execute("INSERT INTO material.term SELECT * FROM dictionary")
            db.unregister("dictionary")
            counts = {}
            for name in ["html_nodes", "posting"]:
                table = BY_NAME[name].rows(context)
                counts[name] = table.num_rows
                print(json.dumps({"stage": name, "rows": table.num_rows}), flush=True)
                db.register("batch", table)
                db.execute(f"INSERT INTO material.{name} SELECT * FROM batch")
                db.unregister("batch")
            db.execute(
                "INSERT INTO ingest.visits(visit_id,document_id,requested_url,outcome,admitted_at,finished_at) SELECT uuid(),uuid(),'https://fixture.invalid/'||content_sha256,'succeeded',now(),now() FROM material.html_nodes WHERE node_index=0"
            )
            db.execute(
                "INSERT INTO ingest.documents(document_id,visit_id,detected_media_type,content_sha256,observed_at,representation,content_bytes,object_key,storage_encoding,stored_bytes) SELECT document_id,visit_id,'text/html',split_part(requested_url,'/',4),now(),'html',0,'fixture', 'identity',0 FROM ingest.visits"
            )
            validation_start = perf_counter()
            for sql in BY_NAME["posting"].validation_queries:
                assert db.execute(sql).fetchone()[0] == 0, sql
            validation_seconds = perf_counter() - validation_start
            stats = db.execute(
                "SELECT sum(frequency),max(frequency) FROM material.posting"
            ).fetchone()
        summary = {
            "documents": count,
            "tokens": stats[0],
            "max_term_frequency": stats[1],
            "terms": len(terms),
            "rows": counts,
            "tokenization_seconds": build_seconds,
            "validation_seconds": validation_seconds,
            "preparation_seconds": perf_counter() - started,
            "max_rss_platform_units": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss,
            "parquet_bytes": sum(
                p.stat().st_size for p in (root / "data").rglob("*.parquet")
            ),
        }
        print(json.dumps(summary), flush=True)
        del parsed, context, terms, table
        service = QueryService(config)
        try:
            service.connection.execute("SELECT 1")
            cases = []
            with patch(
                "periplus.query.benchmarking.catalogue_config_from_env",
                return_value=config,
            ):
                for text in ["robot", "the", "robot the", '"artificial intelligence"']:
                    sql = (
                        "SELECT * FROM public_v1.search('"
                        + text
                        + "') ORDER BY score DESC,content_id"
                    )
                    case = QueryCase(
                        "positional-search",
                        text,
                        "Search API",
                        "schema",
                        True,
                        (None,),
                        "4GiB",
                        60000,
                        sql,
                        root,
                        seconds=120,
                    )
                    result = asdict(
                        _measure(
                            ApiBench(service), case, None, 1, profile_warm_runs=False
                        )
                    )
                    cases.append({"query": text, "measurement": result})
                    print(
                        json.dumps(
                            {
                                "query": text,
                                "normal_ms": result["normal_ms"],
                                "warm_ms": result["warm_ms"],
                                "contents": result["result_rows"],
                            }
                        ),
                        flush=True,
                    )
        finally:
            service.close()
        return {"summary": summary, "cases": cases}


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--input-dir", type=Path, required=True)
    p.add_argument("--documents", type=int, default=500)
    p.add_argument("--report", type=Path, required=True)
    p.add_argument("--fixture", type=Path)
    args = p.parse_args()
    if not 1 <= args.documents <= 1000:
        p.error("documents must be 1..1000")
    result = run(args.input_dir, args.documents, args.fixture)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2))
