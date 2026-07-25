"""Deterministic, resumable synthetic load generation for ``atlas_load``.

This is trusted load-test infrastructure, not an application write path. Rows
are generated inside DuckBasin so multi-billion-row DOM fixtures do not cross
the client boundary. Each batch is one DuckLake transaction and has
deterministic crawl identities, allowing a rerun to verify and skip committed
batches after either an orderly stop or an uncertain client acknowledgement.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import md5, sha256
import json
import time
from typing import Any
from uuid import UUID

import duckdb

from repository.catalogue.client import Catalogue
from repository.catalogue.operations import (
    is_retryable_catalogue_transaction_conflict,
    is_retryable_catalogue_unavailability,
)


DATASET_NAME = "atlas-compiler-load-v1"
EXPECTED_LAKE = "atlas_load"
_GRAPH_ID = UUID(md5(f"{DATASET_NAME}:graph".encode()).hexdigest())
_SOURCE_EDGE_ID = UUID(md5(f"{DATASET_NAME}:source-edge".encode()).hexdigest())
_PARSER_OPTIONS_HASH = sha256(b"atlas-synthetic-dom-v1").hexdigest()


@dataclass(frozen=True, slots=True)
class SyntheticLoadConfig:
    start_date: date
    days: int = 60
    crawls_per_day: int = 25_000
    batch_size: int = 500

    def __post_init__(self) -> None:
        if self.days <= 0:
            raise ValueError("days must be greater than zero")
        if self.crawls_per_day <= 0:
            raise ValueError("crawls_per_day must be greater than zero")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if self.batch_size % 100:
            raise ValueError("batch_size must be a multiple of 100")
        if self.crawls_per_day % self.batch_size:
            raise ValueError("crawls_per_day must be divisible by batch_size")

    @property
    def total_crawls(self) -> int:
        return self.days * self.crawls_per_day

    @property
    def batch_count(self) -> int:
        return self.total_crawls // self.batch_size

    @property
    def manifest(self) -> dict[str, object]:
        return {
            "dataset": DATASET_NAME,
            "start_date": self.start_date.isoformat(),
            "days": self.days,
            "crawls_per_day": self.crawls_per_day,
            "batch_size": self.batch_size,
            "html_percent": 96,
            "artifact_percent": 1,
            "failed_percent": 2,
            "skipped_percent": 1,
            "document_reuse_rule": "every twentieth HTML crawl reuses its predecessor",
            "element_distribution": "5000 + two deterministic uniforms[0,5000]",
        }

    @property
    def effective_policy(self) -> dict[str, object]:
        return {
            "accepted_content_types": [
                "text/html",
                "application/xhtml+xml",
                "application/pdf",
            ],
            "completion": {
                "wait_dynamic": {"enabled": True},
                "wait_fixed": {"enabled": True, "duration_ms": 250},
                "scroll": {"enabled": True},
                "expand": {"enabled": True},
            },
            "synthetic_load": self.manifest,
        }

    @property
    def effective_policy_json(self) -> str:
        return json.dumps(
            self.effective_policy,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def effective_policy_hash(self) -> str:
        return sha256(self.effective_policy_json.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class SyntheticBatch:
    index: int
    start: int
    stop: int

    @property
    def row_count(self) -> int:
        return self.stop - self.start


def synthetic_batches(config: SyntheticLoadConfig) -> Iterator[SyntheticBatch]:
    for index, start in enumerate(
        range(0, config.total_crawls, config.batch_size)
    ):
        yield SyntheticBatch(
            index=index,
            start=start,
            stop=min(start + config.batch_size, config.total_crawls),
        )


def expected_counts(config: SyntheticLoadConfig) -> dict[str, int]:
    counts = {
        "crawls": config.total_crawls,
        "crawl_attempts": 0,
        "crawl_steps": 0,
        "documents": 0,
        "elements": 0,
        "artifacts": 0,
    }
    for ordinal in range(config.total_crawls):
        slot = ordinal % 100
        is_html = slot not in {90, 91, 92, 93}
        if slot == 90:
            counts["artifacts"] += 1
        attempts = 3 if ordinal % 50 == 0 else 2 if ordinal % 10 == 0 else 1
        counts["crawl_attempts"] += attempts
        if is_html:
            counts["crawl_steps"] += (
                1
                + int(ordinal % 10 < 6)
                + int(ordinal % 4 == 0)
                + int(ordinal % 20 == 0)
            )
            if ordinal % 20 != 19:
                counts["documents"] += 1
                counts["elements"] += _element_count(ordinal)
    return counts


def batch_insert_sql(
    config: SyntheticLoadConfig,
    batch: SyntheticBatch,
) -> tuple[tuple[str, str], ...]:
    base = _base_ctes(config, batch)
    documents = f"""
        {base},
        batch_documents AS (
            SELECT
                document_ordinal,
                min(observed_at) AS first_seen_at,
                {_element_count_sql("document_ordinal")} AS element_count
            FROM prepared
            WHERE is_html
            GROUP BY document_ordinal
        )
        INSERT INTO main.documents BY NAME
        SELECT
            sha256({_literal(f"{DATASET_NAME}:document:")} ||
                   CAST(document_ordinal AS VARCHAR)) AS document_id,
            'synthetic/html/' ||
                substr(sha256({_literal(f"{DATASET_NAME}:document:")} ||
                              CAST(document_ordinal AS VARCHAR)), 1, 2) || '/' ||
                sha256({_literal(f"{DATASET_NAME}:document:")} ||
                       CAST(document_ordinal AS VARCHAR)) || '.html.zst' AS object_key,
            'text/html' AS content_type,
            'utf-8' AS encoding,
            64000 + element_count * 24 AS size_bytes,
            12000 + element_count * 5 AS compressed_size_bytes,
            'zstd' AS compression,
            1 AS dom_schema_version,
            'atlas-synthetic' AS parser_name,
            '1.0.0' AS parser_version,
            {_literal(_PARSER_OPTIONS_HASH)} AS parser_options_hash,
            element_count,
            first_seen_at
        FROM batch_documents
        ORDER BY document_id
    """
    elements = f"""
        {base},
        batch_documents AS (
            SELECT DISTINCT
                document_ordinal,
                {_element_count_sql("document_ordinal")} AS element_count
            FROM prepared
            WHERE is_html
        ),
        projected AS (
            SELECT
                sha256({_literal(f"{DATASET_NAME}:document:")} ||
                       CAST(document_ordinal AS VARCHAR)) AS document_id,
                element_count,
                element_index
            FROM batch_documents
            CROSS JOIN LATERAL range(element_count) AS generated(element_index)
        )
        INSERT INTO main.elements BY NAME
        SELECT
            document_id,
            CAST(element_index AS INTEGER) AS element_index,
            CASE
                WHEN element_index = 0 THEN NULL
                WHEN element_index = 1 THEN 0
                ELSE 1
            END::INTEGER AS parent_index,
            CAST(
                CASE WHEN element_index <= 1
                     THEN element_count - 1
                     ELSE element_index
                END AS INTEGER
            ) AS subtree_end_index,
            CAST(
                CASE WHEN element_index = 0 THEN 0
                     WHEN element_index = 1 THEN 1
                     ELSE 2
                END AS INTEGER
            ) AS depth,
            CASE
                WHEN element_index = 0 THEN 'html'
                WHEN element_index = 1 THEN 'body'
                WHEN element_index % 40 = 0 THEN 'a'
                WHEN element_index % 37 = 0 THEN 'img'
                WHEN element_index % 17 = 0 THEN 'p'
                WHEN element_index % 13 = 0 THEN 'li'
                WHEN element_index % 11 = 0 THEN 'span'
                ELSE 'div'
            END AS tag,
            NULL::VARCHAR AS namespace_uri,
            CASE
                WHEN element_index % 40 = 0 THEN
                    map(
                        ['href'],
                        ['https://target' ||
                         CAST(element_index % 1000 AS VARCHAR) ||
                         '.example/item/' ||
                         CAST(element_index % 10000 AS VARCHAR)]
                    )
                WHEN element_index % 37 = 0 THEN
                    map(
                        ['src', 'alt'],
                        ['/assets/image-' ||
                         CAST(element_index % 500 AS VARCHAR) || '.webp',
                         'Synthetic image']
                    )
                ELSE CAST(map() AS MAP(VARCHAR, VARCHAR))
            END AS attributes,
            CASE
                WHEN element_index % 17 = 0 OR element_index % 40 = 0
                    THEN 'Synthetic text ' ||
                         CAST(element_index % 1000 AS VARCHAR)
                ELSE ''
            END AS text_direct,
            CASE WHEN element_index % 29 = 0 THEN ' ' ELSE '' END AS text_tail
        FROM projected
    """
    artifacts = f"""
        {base}
        INSERT INTO main.artifacts BY NAME
        SELECT
            sha256({_literal(f"{DATASET_NAME}:artifact:")} ||
                   CAST(global_ordinal AS VARCHAR)) AS artifact_id,
            'synthetic/artifacts/' ||
                sha256({_literal(f"{DATASET_NAME}:artifact:")} ||
                       CAST(global_ordinal AS VARCHAR)) || '.pdf' AS object_key,
            200000 + (global_ordinal % 1000000) AS size_bytes,
            'application/pdf' AS response_media_type,
            'application/pdf' AS detected_media_type,
            'synthetic-signature' AS detector_name,
            '1.0.0' AS detector_version,
            0.99::DOUBLE AS detection_confidence,
            observed_at AS first_seen_at
        FROM prepared
        WHERE outcome_slot = 90
        ORDER BY artifact_id
    """
    crawls = f"""
        {base}
        INSERT INTO main.crawls BY NAME
        SELECT
            {_uuid_sql(_literal(f"{DATASET_NAME}:crawl:") + " || CAST(global_ordinal AS VARCHAR)")} AS crawl_id,
            CASE WHEN is_html
                THEN sha256({_literal(f"{DATASET_NAME}:document:")} ||
                            CAST(document_ordinal AS VARCHAR))
            END AS document_id,
            CASE WHEN outcome_slot = 90
                THEN sha256({_literal(f"{DATASET_NAME}:artifact:")} ||
                            CAST(global_ordinal AS VARCHAR))
            END AS artifact_id,
            UUID {_literal(str(_GRAPH_ID))} AS graph_id,
            {_uuid_sql(_literal(f"{DATASET_NAME}:run:") + " || CAST(day_index AS VARCHAR)")} AS graph_run_id,
            {_uuid_sql(_literal(f"{DATASET_NAME}:node:") + " || CAST(global_ordinal % 3 AS VARCHAR)")} AS graph_node_id,
            CASE WHEN day_ordinal % 100 <> 0
                THEN {_uuid_sql(_literal(f"{DATASET_NAME}:crawl:") + " || CAST(global_ordinal - 1 AS VARCHAR)")}
            END AS source_crawl_id,
            CASE WHEN day_ordinal % 100 <> 0
                THEN UUID {_literal(str(_SOURCE_EDGE_ID))}
            END AS source_edge_id,
            requested_url,
            effective_url AS url,
            'https' AS scheme,
            host,
            443 AS port,
            registrable_domain,
            path,
            query,
            observed_at AS started_at,
            observed_at + attempt_count * INTERVAL 1 SECOND AS completed_at,
            CASE WHEN is_html OR outcome_slot = 90
                THEN observed_at + attempt_count * INTERVAL 1 SECOND -
                     INTERVAL 100 MILLISECOND
            END AS content_captured_at,
            CASE
                WHEN is_html OR outcome_slot = 90 THEN 200
                WHEN outcome_slot = 91 THEN 503
                WHEN outcome_slot = 92 THEN 504
                ELSE 451
            END AS status_code,
            CASE
                WHEN is_html THEN 'text/html'
                WHEN outcome_slot = 90 THEN 'application/pdf'
            END AS response_media_type,
            1 AS policy_schema_version,
            {_literal(config.effective_policy_hash)} AS effective_policy_hash,
            CAST({_literal(config.effective_policy_json)} AS JSON) AS effective_policy,
            CASE
                WHEN outcome_slot IN (91, 92) THEN 'failed'
                WHEN outcome_slot = 93 THEN 'skipped'
                ELSE 'success'
            END AS outcome,
            CASE
                WHEN outcome_slot = 91 THEN 'http_503'
                WHEN outcome_slot = 92 THEN 'navigation_timeout'
            END AS failure_code,
            CASE WHEN outcome_slot IN (91, 92) THEN 'navigation' END AS failure_stage,
            CASE WHEN outcome_slot IN (91, 92) THEN true END AS failure_retryable,
            CASE
                WHEN outcome_slot = 91 THEN 'Synthetic terminal HTTP 503'
                WHEN outcome_slot = 92 THEN 'Synthetic navigation timeout'
            END AS failure_detail
        FROM prepared
        ORDER BY registrable_domain, host, path, completed_at, crawl_id
    """
    attempts = f"""
        {base},
        expanded_attempts AS (
            SELECT prepared.*, attempt_number
            FROM prepared
            CROSS JOIN LATERAL
                range(1, attempt_count + 1) AS attempts(attempt_number)
        )
        INSERT INTO main.crawl_attempts BY NAME
        SELECT
            {_uuid_sql(_literal(f"{DATASET_NAME}:crawl:") + " || CAST(global_ordinal AS VARCHAR)")} AS crawl_id,
            CAST(attempt_number AS INTEGER) AS attempt_number,
            observed_at + (attempt_number - 1) * INTERVAL 1 SECOND AS started_at,
            observed_at + attempt_number * INTERVAL 1 SECOND -
                INTERVAL 100 MILLISECOND AS completed_at,
            requested_url,
            effective_url AS url,
            CASE
                WHEN attempt_number < attempt_count THEN 503
                WHEN is_html OR outcome_slot = 90 THEN 200
                WHEN outcome_slot = 91 THEN 503
                WHEN outcome_slot = 92 THEN 504
                ELSE 451
            END AS status_code,
            CASE
                WHEN attempt_number < attempt_count THEN 'text/html'
                WHEN is_html THEN 'text/html'
                WHEN outcome_slot = 90 THEN 'application/pdf'
            END AS response_media_type,
            CASE
                WHEN attempt_number < attempt_count THEN 'retry'
                WHEN outcome_slot IN (91, 92) THEN 'failed'
                WHEN outcome_slot = 93 THEN 'skipped'
                ELSE 'success'
            END AS outcome,
            CASE
                WHEN attempt_number < attempt_count THEN 'http_503'
                WHEN outcome_slot = 91 THEN 'http_503'
                WHEN outcome_slot = 92 THEN 'navigation_timeout'
            END AS failure_code,
            CASE WHEN attempt_number < attempt_count THEN 1.5::DOUBLE END
                AS retry_after_seconds
        FROM expanded_attempts
        ORDER BY crawl_id, attempt_number
    """
    steps = f"""
        {base},
        candidates AS (
            SELECT prepared.*, 1 AS method_order,
                   'wait_dynamic' AS method,
                   '{{"idle_ms":500,"maximum_ms":5000}}' AS config_json
            FROM prepared WHERE is_html
            UNION ALL
            SELECT prepared.*, 2, 'wait_fixed',
                   '{{"duration_ms":250}}'
            FROM prepared WHERE is_html AND global_ordinal % 20 = 0
            UNION ALL
            SELECT prepared.*, 3, 'scroll',
                   '{{"maximum_iterations":12,"settle_ms":100}}'
            FROM prepared WHERE is_html AND global_ordinal % 10 < 6
            UNION ALL
            SELECT prepared.*, 4, 'expand',
                   '{{"maximum_clicks":8}}'
            FROM prepared WHERE is_html AND global_ordinal % 4 = 0
        ),
        numbered AS (
            SELECT *,
                row_number() OVER (
                    PARTITION BY global_ordinal ORDER BY method_order
                ) AS step_ordinal,
                {_element_count_sql("document_ordinal")} AS element_count
            FROM candidates
        )
        INSERT INTO main.crawl_steps BY NAME
        SELECT
            {_uuid_sql(_literal(f"{DATASET_NAME}:crawl:") + " || CAST(global_ordinal AS VARCHAR)")} AS crawl_id,
            CAST(attempt_count AS INTEGER) AS attempt_number,
            CAST(step_ordinal AS INTEGER) AS step_ordinal,
            method,
            1 AS method_version,
            sha256(config_json) AS config_hash,
            CAST(config_json AS JSON) AS config_json,
            observed_at + (attempt_count - 1) * INTERVAL 1 SECOND +
                step_ordinal * INTERVAL 100 MILLISECOND AS started_at,
            CAST(
                CASE method
                    WHEN 'wait_dynamic' THEN 500 + global_ordinal % 1500
                    WHEN 'wait_fixed' THEN 250
                    WHEN 'scroll' THEN 300 + global_ordinal % 1200
                    ELSE 100 + global_ordinal % 600
                END AS BIGINT
            ) AS duration_ms,
            CAST(
                CASE method
                    WHEN 'scroll' THEN 1 + global_ordinal % 12
                    WHEN 'expand' THEN 1 + global_ordinal % 8
                    ELSE 1
                END AS INTEGER
            ) AS iterations,
            CASE method
                WHEN 'wait_dynamic' THEN 'network_idle'
                WHEN 'wait_fixed' THEN 'duration_elapsed'
                WHEN 'scroll' THEN 'height_stable'
                ELSE 'no_candidates'
            END AS stop_reason,
            CAST(greatest(2, element_count - step_ordinal * 25) AS BIGINT)
                AS before_element_count,
            CAST(element_count AS BIGINT) AS after_element_count,
            CAST(greatest(0, element_count * 8 - step_ordinal * 100) AS BIGINT)
                AS before_text_chars,
            CAST(element_count * 8 AS BIGINT) AS after_text_chars,
            CAST(greatest(0, element_count // 40 - step_ordinal) AS BIGINT)
                AS before_link_count,
            CAST(element_count // 40 AS BIGINT) AS after_link_count,
            CAST(800 + step_ordinal * 100 AS BIGINT) AS before_scroll_height,
            CAST(900 + step_ordinal * 100 AS BIGINT) AS after_scroll_height
        FROM numbered
        ORDER BY crawl_id, attempt_number, step_ordinal
    """
    return (
        ("documents", documents),
        ("elements", elements),
        ("artifacts", artifacts),
        ("crawls", crawls),
        ("crawl_attempts", attempts),
        ("crawl_steps", steps),
    )


def load_synthetic_catalogue(
    catalogue: Catalogue,
    config: SyntheticLoadConfig,
    *,
    max_batches: int | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    if catalogue.lake_slug != EXPECTED_LAKE:
        raise ValueError(
            f"synthetic loader requires lake {EXPECTED_LAKE!r}, "
            f"got {catalogue.lake_slug!r}"
        )
    if max_batches is not None and max_batches <= 0:
        raise ValueError("max_batches must be greater than zero")
    emit = progress or (lambda _event: None)
    prepare_synthetic_catalogue(catalogue, config)

    loaded = 0
    skipped = 0
    started = time.monotonic()
    for batch in synthetic_batches(config):
        if max_batches is not None and loaded + skipped >= max_batches:
            break
        status = load_synthetic_batch(catalogue, config, batch)
        if status == "skipped":
            skipped += 1
            emit(_progress_event("skipped", batch, started))
            continue
        loaded += 1
        emit(_progress_event("loaded", batch, started))

    return {
        "lake": catalogue.lake_slug,
        "dataset": DATASET_NAME,
        "config": asdict(config),
        "expected_counts": expected_counts(config),
        "loaded_batches": loaded,
        "skipped_batches": skipped,
        "total_batches": config.batch_count,
        "elapsed_seconds": time.monotonic() - started,
    }


def prepare_synthetic_catalogue(
    catalogue: Catalogue,
    config: SyntheticLoadConfig,
) -> None:
    if catalogue.lake_slug != EXPECTED_LAKE:
        raise ValueError(
            f"synthetic loader requires lake {EXPECTED_LAKE!r}, "
            f"got {catalogue.lake_slug!r}"
        )
    catalogue.bootstrap()
    _validate_lake_ownership(catalogue, config)


def load_synthetic_batch(
    catalogue: Catalogue,
    config: SyntheticLoadConfig,
    batch: SyntheticBatch,
) -> str:
    """Load or verify one atomic batch, returning ``loaded`` or ``skipped``."""

    if catalogue.lake_slug != EXPECTED_LAKE:
        raise ValueError(
            f"synthetic loader requires lake {EXPECTED_LAKE!r}, "
            f"got {catalogue.lake_slug!r}"
        )
    present = _batch_present_count(catalogue, config, batch)
    if present == batch.row_count:
        return "skipped"
    if present:
        raise RuntimeError(
            f"synthetic batch {batch.index} is partially present: "
            f"{present}/{batch.row_count} crawls"
        )
    _load_batch_with_retry(catalogue, config, batch)
    verified = _batch_present_count(catalogue, config, batch)
    if verified != batch.row_count:
        raise RuntimeError(
            f"synthetic batch {batch.index} commit could not be verified: "
            f"{verified}/{batch.row_count} crawls"
        )
    return "loaded"


def is_batch_capacity_pressure(exc: BaseException) -> bool:
    if not isinstance(exc, duckdb.Error):
        return False
    message = str(exc).lower()
    return any(
        fragment in message
        for fragment in (
            "could not allocate block",
            "failed to allocate data",
            "failed to pin block",
            "out of memory",
        )
    )


def current_counts(catalogue: Catalogue) -> dict[str, int]:
    return {
        table: int(catalogue.trusted_remote_rows(
            f"SELECT count(*) FROM main.{table}"
        )[0][0])
        for table in (
            "crawls",
            "crawl_attempts",
            "crawl_steps",
            "documents",
            "elements",
            "artifacts",
        )
    }


def _load_batch_with_retry(
    catalogue: Catalogue,
    config: SyntheticLoadConfig,
    batch: SyntheticBatch,
    *,
    attempts: int = 5,
) -> None:
    delay = 1.0
    for attempt in range(1, attempts + 1):
        try:
            with catalogue.remote_transaction():
                for _table, sql in batch_insert_sql(config, batch):
                    catalogue.trusted_remote_execute(sql)
            return
        except Exception as exc:
            if not (
                is_retryable_catalogue_transaction_conflict(exc)
                or is_retryable_catalogue_unavailability(exc)
            ) or attempt >= attempts:
                raise
            # The commit acknowledgement may have been lost. Probe before any
            # retry so a committed deterministic batch is never appended twice.
            present = _batch_present_count(catalogue, config, batch)
            if present == batch.row_count:
                return
            if present:
                raise RuntimeError(
                    f"synthetic batch {batch.index} became partially visible: "
                    f"{present}/{batch.row_count} crawls"
                ) from exc
            time.sleep(delay)
            delay = min(30.0, delay * 2)


def _validate_lake_ownership(
    catalogue: Catalogue,
    config: SyntheticLoadConfig,
) -> None:
    graph_id = _literal(str(_GRAPH_ID))
    policy_hash = _literal(config.effective_policy_hash)
    total, owned, mismatched = catalogue.trusted_remote_rows(
        "SELECT "
        "count(*), "
        f"count(*) FILTER (WHERE graph_id = UUID {graph_id}), "
        "count(*) FILTER (WHERE "
        f"graph_id = UUID {graph_id} "
        f"AND effective_policy_hash <> {policy_hash}) "
        "FROM main.crawls"
    )[0]
    if int(total) != int(owned):
        raise RuntimeError(
            f"{EXPECTED_LAKE} contains {int(total) - int(owned)} "
            "crawl rows outside the synthetic dataset"
        )
    if int(mismatched):
        raise RuntimeError(
            "the existing synthetic dataset uses a different load configuration"
        )
    if int(total) == 0:
        nonempty = {
            table: count
            for table, count in current_counts(catalogue).items()
            if count
        }
        if nonempty:
            raise RuntimeError(
                f"{EXPECTED_LAKE} contains non-crawl data: {nonempty}"
            )


def _batch_present_count(
    catalogue: Catalogue,
    config: SyntheticLoadConfig,
    batch: SyntheticBatch,
) -> int:
    expected = _batch_identity_cte(config, batch)
    rows = catalogue.trusted_remote_rows(
        f"""
        {expected}
        SELECT count(*)
        FROM expected
        JOIN main.crawls USING (crawl_id)
        WHERE graph_id = UUID {_literal(str(_GRAPH_ID))}
          AND effective_policy_hash = {_literal(config.effective_policy_hash)}
        """
    )
    return int(rows[0][0])


def _batch_identity_cte(
    config: SyntheticLoadConfig,
    batch: SyntheticBatch,
) -> str:
    del config
    return f"""
        WITH expected AS (
            SELECT
                {_uuid_sql(_literal(f"{DATASET_NAME}:crawl:") + " || CAST(global_ordinal AS VARCHAR)")} AS crawl_id
            FROM range({batch.start}, {batch.stop})
                 AS generated(global_ordinal)
        )
    """


def _base_ctes(
    config: SyntheticLoadConfig,
    batch: SyntheticBatch,
) -> str:
    start = config.start_date.isoformat()
    return f"""
        WITH source AS (
            SELECT
                global_ordinal,
                global_ordinal // {config.crawls_per_day} AS day_index,
                global_ordinal % {config.crawls_per_day} AS day_ordinal,
                global_ordinal % 100 AS outcome_slot,
                CASE WHEN global_ordinal % 20 = 19
                     THEN global_ordinal - 1
                     ELSE global_ordinal
                END AS document_ordinal,
                CASE WHEN global_ordinal % 50 = 0 THEN 3
                     WHEN global_ordinal % 10 = 0 THEN 2
                     ELSE 1
                END AS attempt_count
            FROM range({batch.start}, {batch.stop})
                 AS generated(global_ordinal)
        ),
        timed AS (
            SELECT *,
                TIMESTAMPTZ {_literal(start + " 00:00:00+00")} +
                    day_index * INTERVAL 1 DAY +
                    CAST(
                        floor(day_ordinal * 86400.0 /
                              {config.crawls_per_day}) AS BIGINT
                    ) * INTERVAL 1 SECOND AS observed_at,
                outcome_slot NOT IN (90, 91, 92, 93) AS is_html,
                CASE
                    WHEN global_ordinal % 10 < 7
                        THEN global_ordinal % 100
                    ELSE 100 + global_ordinal % 900
                END AS domain_ordinal
            FROM source
        ),
        prepared AS (
            SELECT *,
                'domain' || lpad(CAST(domain_ordinal AS VARCHAR), 4, '0') ||
                    '.example' AS registrable_domain,
                'www.domain' ||
                    lpad(CAST(domain_ordinal AS VARCHAR), 4, '0') ||
                    '.example' AS host,
                '/section/' || CAST(global_ordinal % 100 AS VARCHAR) ||
                    '/page/' || CAST(global_ordinal AS VARCHAR) AS path,
                CASE WHEN global_ordinal % 5 = 0
                    THEN 'campaign=' || CAST(global_ordinal % 17 AS VARCHAR)
                    ELSE ''
                END AS query,
                'https://www.domain' ||
                    lpad(CAST(domain_ordinal AS VARCHAR), 4, '0') ||
                    '.example/section/' ||
                    CAST(global_ordinal % 100 AS VARCHAR) ||
                    '/page/' || CAST(global_ordinal AS VARCHAR) ||
                    CASE WHEN global_ordinal % 5 = 0
                        THEN '?campaign=' ||
                             CAST(global_ordinal % 17 AS VARCHAR)
                        ELSE ''
                    END AS requested_url,
                'https://www.domain' ||
                    lpad(CAST(domain_ordinal AS VARCHAR), 4, '0') ||
                    '.example/section/' ||
                    CAST(global_ordinal % 100 AS VARCHAR) ||
                    '/page/' || CAST(global_ordinal AS VARCHAR) ||
                    CASE WHEN global_ordinal % 5 = 0
                        THEN '?campaign=' ||
                             CAST(global_ordinal % 17 AS VARCHAR)
                        ELSE ''
                    END AS effective_url
            FROM timed
        )
    """


def _element_count(ordinal: int) -> int:
    return (
        5_000
        + ((ordinal * 1_103_515_245 + 12_345) % 5_001)
        + ((ordinal * 214_013 + 2_531_011) % 5_001)
    )


def _element_count_sql(expression: str) -> str:
    return (
        f"5000 + (({expression} * 1103515245 + 12345) % 5001) + "
        f"(({expression} * 214013 + 2531011) % 5001)"
    )


def _uuid_sql(value_sql: str) -> str:
    digest = f"md5({value_sql})"
    return (
        "CAST("
        f"substr({digest}, 1, 8) || '-' || "
        f"substr({digest}, 9, 4) || '-' || "
        f"substr({digest}, 13, 4) || '-' || "
        f"substr({digest}, 17, 4) || '-' || "
        f"substr({digest}, 21, 12) AS UUID)"
    )


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _progress_event(
    status: str,
    batch: SyntheticBatch,
    started: float,
) -> dict[str, Any]:
    return {
        "status": status,
        "batch": batch.index + 1,
        "batch_start": batch.start,
        "batch_stop": batch.stop,
        "elapsed_seconds": time.monotonic() - started,
    }
