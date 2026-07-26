#!/usr/bin/env python3
"""Plan, load, verify, and run the removable analytical ground-truth pack."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import timedelta
import json
import os
from pathlib import Path
import sys
import time
from typing import Any


PACK_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = PACK_ROOT.parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from benchmarks.analytical_ground_truth import claim_lineage  # noqa: E402
from benchmarks.analytical_ground_truth import pack as product_market  # noqa: E402
from benchmarks.analytical_ground_truth.pack import (  # noqa: E402
    STEP_CONFIG,
    STEP_CONFIG_HASH,
)


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    graph_id: Any
    graph_run_id: Any
    graph_node_id: Any
    policy: dict[str, Any]
    policy_hash: str
    manifest: Any
    observations: Any
    expected_counts: Any
    expected_rows: Any
    attempts_for: Any
    normalized_result_rows: Any
    query_filename: str


SCENARIOS = {
    "product_market": Scenario(
        name="product_market",
        graph_id=product_market.GRAPH_ID,
        graph_run_id=product_market.GRAPH_RUN_ID,
        graph_node_id=product_market.GRAPH_NODE_ID,
        policy=product_market.POLICY,
        policy_hash=product_market.POLICY_HASH,
        manifest=product_market.manifest,
        observations=product_market.observations,
        expected_counts=product_market.expected_counts,
        expected_rows=product_market.expected_rows,
        attempts_for=product_market.attempts_for,
        normalized_result_rows=product_market.normalized_result_rows,
        query_filename="product_market.sql",
    ),
    "claim_lineage": Scenario(
        name="claim_lineage",
        graph_id=claim_lineage.GRAPH_ID,
        graph_run_id=claim_lineage.GRAPH_RUN_ID,
        graph_node_id=claim_lineage.GRAPH_NODE_ID,
        policy=claim_lineage.POLICY,
        policy_hash=claim_lineage.POLICY_HASH,
        manifest=claim_lineage.manifest,
        observations=claim_lineage.observations,
        expected_counts=claim_lineage.expected_counts,
        expected_rows=claim_lineage.expected_rows,
        attempts_for=claim_lineage.attempts_for,
        normalized_result_rows=claim_lineage.normalized_result_rows,
        query_filename="claim_lineage.sql",
    ),
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "load", "verify", "run"),
    )
    parser.add_argument(
        "--scenario",
        choices=tuple(SCENARIOS),
        default="product_market",
    )
    parser.add_argument("--lake", default="atlas_test")
    parser.add_argument(
        "--allow-load-lake",
        action="store_true",
        help="allow the documented atlas_load overlay target",
    )
    parser.add_argument("--filler-elements", type=int)
    parser.add_argument("--batch-size", type=int, default=96)
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    _validate_lake(
        arguments.lake,
        allow_load_lake=arguments.allow_load_lake,
    )
    os.environ["DUCKBASIN_LAKE"] = arguments.lake
    scenario = SCENARIOS[arguments.scenario]
    source = scenario.observations(
        filler_elements=arguments.filler_elements
    )
    if arguments.command == "plan":
        scenario_manifest = scenario.manifest()
        _emit(
            {
                "lake": arguments.lake,
                "scenario": scenario.name,
                "manifest": scenario_manifest,
                "expected_counts": scenario.expected_counts(source),
                "distinct_html_documents": len(
                    {
                        item.document_id
                        for item in source
                        if item.document_id is not None
                    }
                ),
                "expected_result_rows": len(
                    scenario.expected_rows(source)
                ),
                "filler_elements": (
                    arguments.filler_elements
                    if arguments.filler_elements is not None
                    else (
                        scenario_manifest.get("default_filler_elements")
                        or scenario_manifest.get(
                            "default_filler_paragraphs"
                        )
                    )
                ),
            }
        )
        return
    if arguments.batch_size <= 0 or arguments.batch_size > 250:
        raise SystemExit("--batch-size must be between 1 and 250")
    if arguments.command == "load":
        _load(scenario, source, batch_size=arguments.batch_size)
    elif arguments.command == "verify":
        _verify(scenario, source)
    else:
        _run(scenario, source)


def _load(scenario: Scenario, source, *, batch_size: int) -> None:
    from repository import repository_ingestor_from_env
    from repository.catalogue import (
        CrawlAttemptRecord,
        CrawlRecord,
        CrawlStepRecord,
        NormalizedUrl,
    )
    from repository.objects.html import identify_html

    started = time.perf_counter()
    created = {
        "crawls": 0,
        "documents": 0,
        "raw_objects": 0,
    }
    with repository_ingestor_from_env() as ingestor:
        expected = scenario.expected_counts(source)
        expected_crawl_ids = {item.crawl_id for item in source}
        existing_crawl_ids = _existing_pack_crawl_ids(
            ingestor.catalogue,
            scenario.graph_id,
        )
        if existing_crawl_ids == expected_crawl_ids:
            actual = _current_counts(
                ingestor.catalogue,
                scenario.graph_id,
            )
            if actual != expected:
                raise SystemExit(
                    "all deterministic crawls exist but related counts differ:\n"
                    + json.dumps(
                        {"expected": expected, "actual": actual},
                        indent=2,
                        sort_keys=True,
                    )
                )
            _emit(
                {
                    "event": "load_complete",
                    "created": created,
                    "skipped_crawls": len(source),
                    "counts": actual,
                    "elapsed_seconds": round(
                        time.perf_counter() - started,
                        3,
                    ),
                }
            )
            return
        unique_document_ids = list(
            dict.fromkeys(
                item.document_id
                for item in source
                if item.document_id is not None
            )
        )
        known_documents = ingestor.catalogue_service.get_documents(
            unique_document_ids
        )
        stored_documents: set[str] = set()
        for batch_index, offset in enumerate(range(0, len(source), batch_size), start=1):
            batch_source = source[offset : offset + batch_size]
            if all(
                item.crawl_id in existing_crawl_ids
                for item in batch_source
            ):
                _emit(
                    {
                        "event": "batch_skipped",
                        "batch": batch_index,
                        "batches": (
                            len(source) + batch_size - 1
                        ) // batch_size,
                        "rows": len(batch_source),
                    },
                    compact=True,
                )
                continue
            prepared = []
            for item in batch_source:
                normalized = NormalizedUrl.from_normalized_url(
                    item.requested_url
                )
                attempt_count = scenario.attempts_for(item)
                completed_at = item.captured_at + timedelta(milliseconds=200)
                started_at = item.captured_at - timedelta(
                    seconds=attempt_count
                )
                crawl = CrawlRecord(
                    crawl_id=item.crawl_id,
                    document_id=item.document_id,
                    graph_id=scenario.graph_id,
                    graph_run_id=scenario.graph_run_id,
                    graph_node_id=scenario.graph_node_id,
                    requested_url=item.requested_url,
                    url=item.requested_url,
                    scheme=normalized.scheme,
                    host=normalized.host,
                    port=normalized.port,
                    registrable_domain=normalized.registrable_domain,
                    path=normalized.path,
                    query=normalized.query,
                    started_at=started_at,
                    completed_at=completed_at,
                    content_captured_at=(
                        item.captured_at
                        if item.document_id is not None
                        else None
                    ),
                    status_code=item.status_code,
                    response_media_type=(
                        "text/html"
                        if item.document_id is not None
                        else None
                    ),
                    policy_schema_version=1,
                    effective_policy_hash=scenario.policy_hash,
                    effective_policy=scenario.policy,
                    outcome=item.outcome,
                    failure_code=(
                        "http_503" if item.outcome == "failed" else None
                    ),
                    failure_stage=(
                        "navigation" if item.outcome == "failed" else None
                    ),
                    failure_retryable=(
                        True if item.outcome == "failed" else None
                    ),
                    failure_detail=(
                        "Synthetic terminal HTTP 503"
                        if item.outcome == "failed"
                        else None
                    ),
                )
                attempts = _attempt_records(
                    CrawlAttemptRecord,
                    item=item,
                    attempt_count=attempt_count,
                    started_at=started_at,
                    completed_at=completed_at,
                )
                steps = (
                    ()
                    if item.outcome == "failed"
                    else (
                        CrawlStepRecord(
                            crawl_id=item.crawl_id,
                            attempt_number=attempt_count,
                            step_ordinal=1,
                            method="wait_dynamic",
                            method_version=1,
                            config_hash=STEP_CONFIG_HASH,
                            config_json=STEP_CONFIG,
                            started_at=item.captured_at
                            - timedelta(milliseconds=200),
                            duration_ms=200,
                            iterations=1,
                            stop_reason="dom_quiet",
                            before_element_count=0,
                            after_element_count=0,
                            before_text_chars=0,
                            after_text_chars=0,
                            before_link_count=0,
                            after_link_count=0,
                            before_scroll_height=0,
                            after_scroll_height=0,
                        ),
                    )
                )
                if (
                    item.html is not None
                    and item.document_id not in stored_documents
                ):
                    identity = identify_html(item.html)
                    stored = ingestor.store_raw(
                        item.html,
                        source_url=item.requested_url,
                        crawl_id=item.crawl_id,
                        captured_at=item.captured_at,
                        content_type="text/html",
                        identity=identity,
                    )
                    created["raw_objects"] += int(stored.created)
                    stored_documents.add(item.document_id)
                value = ingestor.prepare_from_raw(
                    crawl=crawl,
                    crawl_attempts=attempts,
                    crawl_steps=steps,
                    known_documents=known_documents,
                )
                if value.document is not None:
                    known_documents[value.document.document_id] = value.document
                prepared.append(value)
            results = ingestor.commit_prepared_batch(prepared)
            created["crawls"] += sum(value.crawl_created for value in results)
            created["documents"] += sum(
                value.document_created for value in results
            )
            existing_crawl_ids.update(
                value.crawl_id for value in results
            )
            _emit(
                {
                    "event": "batch_committed",
                    "batch": batch_index,
                    "batches": (len(source) + batch_size - 1) // batch_size,
                    "rows": len(results),
                    "snapshot": results[-1].repository_snapshot,
                    "elapsed_seconds": round(time.perf_counter() - started, 3),
                },
                compact=True,
            )
        actual = _current_counts(
            ingestor.catalogue,
            scenario.graph_id,
        )
        if actual != expected:
            raise SystemExit(
                "post-load counts differ from the frozen pack:\n"
                + json.dumps(
                    {"expected": expected, "actual": actual},
                    indent=2,
                    sort_keys=True,
                )
            )
        _emit(
            {
                "event": "load_complete",
                "created": created,
                "counts": actual,
                "elapsed_seconds": round(
                    time.perf_counter() - started,
                    3,
                ),
            }
        )


def _attempt_records(
    record_type,
    *,
    item,
    attempt_count: int,
    started_at,
    completed_at,
):
    records = []
    for attempt_number in range(1, attempt_count + 1):
        attempt_started = started_at + timedelta(
            seconds=attempt_number - 1
        )
        attempt_completed = (
            completed_at
            if attempt_number == attempt_count
            else attempt_started + timedelta(milliseconds=400)
        )
        retry = attempt_number < attempt_count
        failed = item.outcome == "failed"
        records.append(
            record_type(
                crawl_id=item.crawl_id,
                attempt_number=attempt_number,
                started_at=attempt_started,
                completed_at=attempt_completed,
                requested_url=item.requested_url,
                url=item.requested_url,
                status_code=503 if retry or failed else item.status_code,
                response_media_type=(
                    "text/html"
                    if not retry and not failed
                    else None
                ),
                outcome=(
                    "retry"
                    if retry
                    else ("failed" if failed else "success")
                ),
                failure_code=(
                    "http_503" if retry or failed else None
                ),
                retry_after_seconds=1.0 if retry else None,
            )
        )
    return tuple(records)


def _verify(scenario: Scenario, source) -> None:
    from repository.catalogue import catalogue_from_env

    expected = scenario.expected_counts(source)
    with catalogue_from_env(threads=4, memory_limit="4GB") as catalogue:
        actual = _current_counts(catalogue, scenario.graph_id)
    if actual != expected:
        raise SystemExit(
            "ground-truth load verification failed:\n"
            + json.dumps(
                {"expected": expected, "actual": actual},
                indent=2,
                sort_keys=True,
            )
        )
    _emit({"status": "verified", "counts": actual})


def _existing_pack_crawl_ids(catalogue, graph_id) -> set:
    cursor = catalogue.trusted_connection.execute(
        "SELECT crawl_id FROM main.crawls WHERE graph_id = $graph_id",
        {"graph_id": str(graph_id)},
    )
    return {row[0] for row in cursor.fetchall()}


def _current_counts(catalogue, graph_id) -> dict[str, int]:
    crawl_cursor = catalogue.trusted_connection.execute(
        """
        SELECT crawl_id, document_id
        FROM main.crawls
        WHERE graph_id = $graph_id
        """,
        {"graph_id": str(graph_id)},
    )
    crawl_rows = crawl_cursor.fetchall()
    crawl_ids = [row[0] for row in crawl_rows]
    placeholders = ", ".join("?" for _ in crawl_ids)

    def related_count(table_name: str) -> int:
        if not crawl_ids:
            return 0
        cursor = catalogue.trusted_connection.execute(
            f"SELECT count(*) FROM main.{table_name} "
            f"WHERE crawl_id IN ({placeholders})",
            crawl_ids,
        )
        return int(cursor.fetchone()[0])

    document_ids = sorted(
        {row[1] for row in crawl_rows if row[1] is not None}
    )
    document_placeholders = ", ".join("?" for _ in document_ids)
    element_count = (
        int(
            catalogue.trusted_connection.execute(
                "SELECT count(*) FROM main.elements "
                f"WHERE document_id IN ({document_placeholders})",
                document_ids,
            ).fetchone()[0]
        )
        if document_ids
        else 0
    )
    return {
        "crawls": len(crawl_rows),
        "documents": len(document_ids),
        "elements": element_count,
        "crawl_attempts": related_count("crawl_attempts"),
        "crawl_steps": related_count("crawl_steps"),
    }


def _run(scenario: Scenario, source) -> None:
    import httpx
    import pyarrow as pa

    from atlas_sql import AtlasCompiler
    from repository.catalogue import catalogue_from_env
    from repository.catalogue.compiler_definitions import (
        read_catalogue_compiler_definitions,
    )

    sql = (PACK_ROOT / "queries" / scenario.query_filename).read_text(
        encoding="utf-8"
    )
    negative_sql = (
        PACK_ROOT / "queries" / "unbounded_elements.sql"
    ).read_text(encoding="utf-8")
    connected_at = time.perf_counter()
    with catalogue_from_env(threads=8, memory_limit="8GB") as catalogue:
        connection_seconds = time.perf_counter() - connected_at
        metadata_started = time.perf_counter()
        definitions = read_catalogue_compiler_definitions(
            catalogue.trusted_connection,
            catalogue_alias=catalogue.config.alias,
        )
        metadata_seconds = time.perf_counter() - metadata_started
        compiler = AtlasCompiler.embedded(
            catalogue_revision=definitions.revision
        )
        compile_started = time.perf_counter()
        compilation = compiler.compile(
            sql,
            purpose=definitions.interactive_purpose(),
            coverage_source=(
                f"analytical_ground_truth_{scenario.name}"
            ),
        )
        compile_seconds = time.perf_counter() - compile_started
        if not compilation.valid or compilation.executable_sql is None:
            raise SystemExit(
                "hero query did not compile: "
                + "; ".join(value.message for value in compilation.diagnostics)
            )
        negative = compiler.compile(
            negative_sql,
            purpose=definitions.interactive_purpose(),
            coverage_source="analytical_ground_truth_negative",
        )
        if negative.valid and negative.executable_sql is not None:
            raise SystemExit(
                "intentionally unbounded elements query was not rejected"
            )
    api_url = os.getenv("ATLAS_API_URL") or "http://127.0.0.1:8000"
    execute_started = time.perf_counter()
    with httpx.Client(base_url=api_url.rstrip("/") + "/", timeout=120.0) as client:
        response = client.post(
            "catalogue/query-executions",
            json={"sql": sql},
        )
        if response.status_code != 200:
            raise SystemExit(
                f"interactive execution failed ({response.status_code}): "
                f"{response.text}"
            )
        query_id = response.headers.get("X-Atlas-Query-ID")
        table = pa.ipc.open_stream(response.content).read_all()
        query_state = (
            client.get(f"catalogue/query-executions/{query_id}").json()
            if query_id is not None
            else None
        )
    execution_seconds = time.perf_counter() - execute_started
    actual = scenario.normalized_result_rows(table.to_pylist())
    expected = scenario.expected_rows(source)
    if actual != expected:
        mismatch = next(
            (
                {"expected": expected_value, "actual": actual_value}
                for expected_value, actual_value in zip(
                    expected,
                    actual,
                    strict=False,
                )
                if expected_value != actual_value
            ),
            {
                "expected_row_count": len(expected),
                "actual_row_count": len(actual),
            },
        )
        raise SystemExit(
            "ground-truth query result mismatch:\n"
            + json.dumps(mismatch, indent=2, sort_keys=True, default=str)
        )
    _emit(
        {
            "status": "passed",
            "scenario": scenario.name,
            "query_id": query_id,
            "query_state": query_state,
            "result_rows": len(actual),
            "compiler": {
                "outcome": compilation.outcome.value,
                "rewrites": [
                    {
                        "rule": value.rule,
                        "evidence": value.evidence,
                    }
                    for value in compilation.applied_rewrites
                ],
                "document_scope": (
                    compilation.document_scope.model_dump(mode="json")
                    if compilation.document_scope is not None
                    else None
                ),
                "negative_diagnostics": [
                    {
                        "code": value.code,
                        "message": value.message,
                    }
                    for value in negative.diagnostics
                ],
            },
            "timings_seconds": {
                "connection": round(connection_seconds, 6),
                "metadata": round(metadata_seconds, 6),
                "compilation": round(compile_seconds, 6),
                "execution_and_transfer": round(execution_seconds, 6),
            },
            "sample": actual[:3],
        }
    )


def _validate_lake(lake: str, *, allow_load_lake: bool) -> None:
    if lake == "atlas":
        raise SystemExit("the ground-truth pack always refuses the production atlas lake")
    if lake == "atlas_test":
        return
    if lake == "atlas_load" and allow_load_lake:
        return
    raise SystemExit(
        "the ground-truth pack accepts atlas_test only; "
        "use --allow-load-lake explicitly for atlas_load"
    )


def _emit(value: dict[str, Any], *, compact: bool = False) -> None:
    print(
        json.dumps(
            value,
            indent=None if compact else 2,
            sort_keys=True,
            default=str,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
