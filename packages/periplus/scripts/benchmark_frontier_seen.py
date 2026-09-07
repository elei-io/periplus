"""Synthetic local DuckLake benchmark; never attaches configured development data."""
import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time

from periplus.crawl.runtime.background_seen import SeenCandidates
from periplus.platform.catalogue.client import Catalogue
from periplus.platform.catalogue.config import CatalogueConfig
from periplus.query.service import QueryRequest, QueryService


def run(rows: int) -> dict:
    with TemporaryDirectory(prefix="periplus-seen-benchmark-") as directory:
        root = Path(directory)
        config = CatalogueConfig("periplus", str(root / "metadata.duckdb"), str(root / "data"), "ducklake", "")
        catalogue = Catalogue(config)
        try:
            catalogue.bootstrap()
            connection = catalogue.trusted_connection
            started = time.monotonic()
            connection.execute("""
                INSERT INTO ingest.visits (
                    visit_id, visibility, requested_url, effective_url, admitted_at, started_at,
                    observed_at, finished_at, outcome, document_id, provenance
                )
                SELECT md5('visit-' || i)::UUID,
                    CASE WHEN i % 17 = 0 THEN 'private' ELSE 'public' END,
                    'https://domain-' || (i % 1000) || '.example/page/' || i AS requested_url,
                    CASE WHEN i % 19 = 0 THEN 'https://redirect.example/page/' || i
                         ELSE 'https://domain-' || (i % 1000) || '.example/page/' || i END,
                    d, d, CASE WHEN i % 13 <> 0 THEN d END, d,
                    CASE WHEN i % 13 = 0 THEN 'failed' ELSE 'succeeded' END,
                    CASE WHEN i % 13 <> 0 THEN md5('document-' || i)::UUID END,
                    {'kind': 'periplus'}
                FROM (SELECT i, TIMESTAMPTZ '2026-01-01' + (i % 30) * INTERVAL '1 day' AS d
                      FROM range(?) AS t(i))
                ORDER BY requested_url
            """, [rows])
            connection.execute("""
                INSERT INTO ingest.documents (
                    document_id, visit_id, observed_at, representation, detected_media_type, charset,
                    content_sha256, content_bytes, object_key, storage_encoding, stored_bytes
                )
                SELECT document_id, visit_id, observed_at, 'rendered_html', 'text/html', 'utf-8',
                       repeat('a', 64), 1, 'objects/synthetic', 'identity', 1
                FROM ingest.visits WHERE document_id IS NOT NULL
            """)
            build_seconds = time.monotonic() - started
            version = connection.execute("SELECT version()").fetchone()[0]
            documents = connection.execute("SELECT count(*) FROM ingest.documents").fetchone()[0]
        finally:
            catalogue.close()
        requested = [f"https://domain-{i % 1000}.example/page/{i}" for i in range(101, 133)]
        redirected = [f"https://redirect.example/page/{19 * i}" for i in range(1, 17)]
        absent = [f"https://absent.example/{i}" for i in range(16)]
        candidates = SeenCandidates(urls=tuple(requested + redirected + absent))
        expected = {url for i, url in zip(range(101, 133), requested) if i % 17}
        expected.update(url for i, url in zip(range(1, 17), redirected) if (19 * i) % 17 and (19 * i) % 13)
        service = QueryService(config)
        try:
            request = candidates.query()
            runs = [service.execute(request) for _ in range(3)]
            assert all(not result.truncated and {row[0] for row in result.rows} == expected for result in runs)
            analyzed = service.execute(QueryRequest(sql="EXPLAIN ANALYZE " + request.sql, parameters=request.parameters))
            values = ", ".join("(?)" for _ in candidates.urls)
            baseline = QueryRequest(sql=f"""
                WITH candidates(url) AS (VALUES {values})
                SELECT c.url FROM candidates c
                WHERE EXISTS (SELECT 1 FROM web.observation o WHERE o.requested_url = c.url)
                   OR EXISTS (SELECT 1 FROM web.observation o
                              WHERE o.outcome = 'succeeded' AND o.effective_url = c.url)
                ORDER BY c.url
            """, parameters=list(candidates.urls))
            baseline_result = service.execute(baseline)
            assert baseline_result.rows == runs[0].rows
            assert baseline_result.types == runs[0].types
            assert baseline_result.source_snapshot == runs[0].source_snapshot
            return dict(rows=rows, document_rows=documents, candidates=64, result_rows=len(expected),
                        duckdb_version=version, build_seconds=build_seconds,
                        query_ms=[result.elapsed_ms for result in runs], snapshot=runs[0].source_snapshot,
                        sql=request.sql, parameters=request.parameters, plan=runs[0].plan,
                        analyzed_plan=analyzed.rows, baseline_ms=baseline_result.elapsed_ms,
                        baseline_plan=baseline_result.plan)
        finally:
            service.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=1000000)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not 1000 <= args.rows <= 10000000:
        parser.error("--rows must be between 1,000 and 10,000,000")
    result = run(args.rows)
    args.report.write_text(json.dumps(result, indent=2))
    print(json.dumps({key: result[key] for key in ("rows", "document_rows", "candidates", "result_rows", "query_ms")}))
