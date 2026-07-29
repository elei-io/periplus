"""One-pass document projection shared by CDC and maintenance."""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass

import pyarrow as pa
from atlas.ingestion.objects.html import RawHtmlRepository
from atlas.materialization import metrics as materialization_metrics
from atlas.materialization.bulk import (
    MaterialMutation,
    commit_document_projection,
    commit_material_mutations,
    delete_material_keys,
)
from atlas.materialization.contracts import DOCUMENT_PROJECTIONS
from atlas.materialization.document_projection import (
    LINK_SCHEMA,
    DocumentObservation,
    DocumentProjection,
    DocumentProjectionSource,
    ducklake_varchar_bucket,
    project_documents,
)
from atlas.materialization.document_sources import (
    changed_document_ids,
    document_observation_rows,
    html_content_sources,
)
from atlas.materialization.pipeline import (
    BoundedStagePlan,
    StageSelection,
)
from atlas.materialization.sql import (
    SQL_ID_BATCH_SIZE,
    sql_string,
    sql_string_list,
    value_batches,
)
from atlas.platform.catalogue import Catalogue
from atlas.platform.catalogue.schema import LINKS, PARTITION_BUCKETS
from atlas.urls import normalize_url

_PARTITION_ITEMS = 64
_PARTITION_BYTES = 16 * 1024 * 1024
_WRITE_TARGET_BYTES = 32 * 1024 * 1024
_LINK_SHARD_LOCKS = tuple(
    threading.Lock() for _ in range(PARTITION_BUCKETS)
)


@dataclass(frozen=True, slots=True)
class DocumentProjectionOutput:
    projection: DocumentProjection | None = None
    removed_hashes: frozenset[str] = frozenset()
    replaced_document_ids: frozenset[str] = frozenset()
    finalize_link_source_page_ids: frozenset[str] = frozenset()


@contextmanager
def _target_write_timing(
    target: str,
    operation: str,
) -> Iterator[None]:
    started = time.perf_counter()
    try:
        yield
    except BaseException:
        materialization_metrics.target_write(
            workload="documents",
            target=target,
            operation=operation,
            outcome="error",
            seconds=time.perf_counter() - started,
        )
        raise
    else:
        materialization_metrics.target_write(
            workload="documents",
            target=target,
            operation=operation,
            outcome="success",
            seconds=time.perf_counter() - started,
        )


def document_stage_plan(
    html_repository: RawHtmlRepository,
    parallelism: int,
    *,
    targets: dict[str, str] | None = None,
    select=None,
    enabled_targets: frozenset[str] = frozenset(DOCUMENT_PROJECTIONS),
    item_budget: int = _PARTITION_ITEMS,
    byte_budget: int = _PARTITION_BYTES,
    write_target_bytes: int = _WRITE_TARGET_BYTES,
) -> BoundedStagePlan[
    DocumentProjectionSource, DocumentProjectionOutput
]:
    table_names = {
        target: target for target in DOCUMENT_PROJECTIONS
    } | (targets or {})
    shadow_link_generation = (
        {"links", "link_occurrences"}.issubset(enabled_targets)
        and all(
            table_names[target].startswith("_atlas_rebuild_")
            for target in ("links", "link_occurrences")
        )
    )
    ordered_targets = tuple(
        target
        for target in DOCUMENT_PROJECTIONS
        if target in enabled_targets
    )
    if not ordered_targets:
        raise ValueError("at least one document projection target is required")
    return BoundedStagePlan(
        name="documents",
        target=table_names[ordered_targets[0]],
        select=select or select_document_changes,
        project=lambda sources: project_document_sources(
            sources, html_repository
        ),
        write=lambda catalogue, output: write_document_output(
            catalogue,
            output,
            targets=table_names,
            enabled_targets=enabled_targets,
        ),
        source_bytes=lambda source: source.content_bytes,
        item_budget=item_budget,
        byte_budget=byte_budget,
        parallelism=parallelism,
        additional_targets=tuple(
            table_names[target] for target in ordered_targets[1:]
        ),
        writer_parallelism=parallelism,
        output_bytes=lambda output: (
            output.projection.bytes_for(enabled_targets)
            if output.projection is not None
            else 0
        ),
        combine_outputs=combine_document_outputs,
        partition_output=lambda output: partition_document_output(
            output,
            enabled_targets=enabled_targets,
            target_bytes=write_target_bytes,
            max_partitions=parallelism,
            partition_shadow_links_by_source=shadow_link_generation,
        ),
        partition_key=lambda source: ducklake_varchar_bucket(
            source.content_sha256, PARTITION_BUCKETS
        ),
    )


def select_document_changes(
    catalogue: Catalogue,
    start_snapshot: int,
    end_snapshot: int,
) -> StageSelection[DocumentProjectionSource, DocumentProjectionOutput]:
    affected_hashes = _changed_html_hashes(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    document_ids = changed_document_ids(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    live_documents = {
        content_hash: (object_key, storage_encoding)
        for content_hash, object_key, storage_encoding in html_content_sources(
            catalogue,
            snapshot=end_snapshot,
            content_hashes=affected_hashes,
        )
    }
    sizes = (
        {
            str(content_hash): int(content_bytes)
            for content_hash, content_bytes
            in catalogue.trusted_remote_rows(
                f"""
                SELECT content_sha256, max(content_bytes)
                FROM ingest.documents AT (VERSION => {end_snapshot})
                WHERE content_sha256 IN (
                  {sql_string_list(affected_hashes)}
                )
                GROUP BY content_sha256
                """
            )
        }
        if affected_hashes
        else {}
    )
    observations_by_hash: dict[str, list[DocumentObservation]] = defaultdict(
        list
    )
    for (
        document_id,
        visit_id,
        content_hash,
        source_url,
        observed_at,
        _content_bytes,
    ) in document_observation_rows(
        catalogue,
        document_ids,
        snapshot=end_snapshot,
    ):
        if (
            content_hash is None
            or source_url is None
            or observed_at is None
        ):
            continue
        observations_by_hash[str(content_hash)].append(
            DocumentObservation(
                visit_id=str(visit_id),
                document_id=str(document_id),
                source_url=normalize_url(str(source_url)),
                observed_at=observed_at,
            )
        )
    covered_hashes = _covered_html_hashes(catalogue, affected_hashes)
    removed_hashes = covered_hashes - live_documents.keys()
    corrections = _changed_document_corrections(
        catalogue,
        start_snapshot=start_snapshot,
        end_snapshot=end_snapshot,
    )
    sources = tuple(
        DocumentProjectionSource(
            content_sha256=content_hash,
            object_key=live_documents[content_hash][0],
            storage_encoding=live_documents[content_hash][1],
            content_bytes=sizes.get(content_hash, 0),
            observations=tuple(
                sorted(
                    observations_by_hash.get(content_hash, []),
                    key=lambda value: value.document_id,
                )
            ),
        )
        for content_hash in sorted(live_documents)
    )
    mutations = (
        (
            DocumentProjectionOutput(
                removed_hashes=frozenset(removed_hashes),
                replaced_document_ids=frozenset(corrections),
            ),
        )
        if removed_hashes or corrections
        else ()
    )
    return StageSelection(items=sources, initial_outputs=mutations)


def project_document_sources(
    sources: tuple[DocumentProjectionSource, ...],
    html_repository: RawHtmlRepository,
) -> DocumentProjectionOutput:
    return DocumentProjectionOutput(
        projection=project_documents(html_repository, sources)
    )


def combine_document_outputs(
    outputs: tuple[DocumentProjectionOutput, ...],
) -> DocumentProjectionOutput:
    """Coalesce bounded projector results before byte-oriented writes."""

    projections = tuple(
        output.projection
        for output in outputs
        if output.projection is not None
    )
    if not projections:
        return DocumentProjectionOutput()
    if len(projections) == 1:
        return DocumentProjectionOutput(projection=projections[0])
    return DocumentProjectionOutput(
        projection=DocumentProjection(
            content_hashes=frozenset().union(
                *(projection.content_hashes for projection in projections)
            ),
            document_ids=frozenset().union(
                *(projection.document_ids for projection in projections)
            ),
            content_stats=pa.concat_tables(
                [
                    projection.content_stats
                    for projection in projections
                ]
            ),
            html_elements=pa.concat_tables(
                [
                    projection.html_elements
                    for projection in projections
                ]
            ),
            jsonld_values=pa.concat_tables(
                [
                    projection.jsonld_values
                    for projection in projections
                ]
            ),
            links=_concat_links(
                tuple(
                    projection.links
                    for projection in projections
                ),
            ),
            link_occurrences=pa.concat_tables(
                [
                    projection.link_occurrences
                    for projection in projections
                ]
            ),
        )
    )


def _concat_links(tables: tuple[pa.Table, ...]) -> pa.Table:
    combined = pa.concat_tables(tables)
    rows: dict[str, dict[str, object]] = {}
    for raw in combined.to_pylist():
        link_id = str(raw["link_id"])
        current = rows.get(link_id)
        if current is None:
            rows[link_id] = raw
            continue
        current["first_seen_at"] = min(
            current["first_seen_at"], raw["first_seen_at"]
        )
        current["last_seen_at"] = max(
            current["last_seen_at"], raw["last_seen_at"]
        )
        current["visit_count"] = (
            int(current["visit_count"]) + int(raw["visit_count"])
        )
        current["distinct_content_count"] = (
            int(current["distinct_content_count"])
            + int(raw["distinct_content_count"])
        )
        current["occurrence_count"] = (
            int(current["occurrence_count"]) + int(raw["occurrence_count"])
        )
    return pa.Table.from_pylist(
        [rows[key] for key in sorted(rows)],
        schema=LINK_SCHEMA,
    )


def partition_document_output(
    output: DocumentProjectionOutput,
    *,
    enabled_targets: frozenset[str],
    target_bytes: int = _WRITE_TARGET_BYTES,
    max_partitions: int = 8,
    partition_shadow_links_by_source: bool = False,
) -> tuple[DocumentProjectionOutput, ...]:
    """Split one parsed projection into stable, byte-oriented writes."""

    if target_bytes < 1 or max_partitions < 1:
        raise ValueError("document output partition bounds must be positive")
    projection = output.projection
    if projection is None:
        return (output,)
    total_bytes = projection.bytes_for(enabled_targets)
    partition_count = min(
        max_partitions,
        max(1, math.ceil(total_bytes / target_bytes)),
    )
    if partition_count == 1:
        return (output,)
    partitions: list[DocumentProjectionOutput] = []
    for partition_index in range(partition_count):
        content_stats = _filter_output_partition(
            projection.content_stats,
            key="content_sha256",
            partition_index=partition_index,
            partition_count=partition_count,
        )
        html_elements = _filter_output_partition(
            projection.html_elements,
            key="content_sha256",
            partition_index=partition_index,
            partition_count=partition_count,
        )
        jsonld_values = _filter_output_partition(
            projection.jsonld_values,
            key="content_sha256",
            partition_index=partition_index,
            partition_count=partition_count,
        )
        links = _filter_output_partition(
            projection.links,
            key=(
                "source_page_id"
                if partition_shadow_links_by_source
                else "link_id"
            ),
            partition_index=partition_index,
            partition_count=partition_count,
        )
        link_occurrences = _filter_output_partition(
            projection.link_occurrences,
            key="link_id",
            partition_index=partition_index,
            partition_count=partition_count,
        )
        split = DocumentProjection(
            content_hashes=frozenset(
                str(value)
                for table in (
                    content_stats,
                    html_elements,
                    jsonld_values,
                )
                for value in table["content_sha256"].to_pylist()
            ),
            document_ids=frozenset(
                str(value)
                for value in link_occurrences["document_id"].to_pylist()
            ),
            content_stats=content_stats,
            html_elements=html_elements,
            jsonld_values=jsonld_values,
            links=links,
            link_occurrences=link_occurrences,
        )
        if split.bytes_for(enabled_targets) > 0:
            partitions.append(DocumentProjectionOutput(projection=split))
    return tuple(partitions)


def _filter_output_partition(
    table: pa.Table,
    *,
    key: str,
    partition_index: int,
    partition_count: int,
) -> pa.Table:
    if table.num_rows == 0:
        return table
    assignments: dict[str, int] = {}
    mask: list[bool] = []
    for raw_value in table[key].to_pylist():
        value = str(raw_value)
        assigned = assignments.get(value)
        if assigned is None:
            assigned = _output_partition(value, partition_count)
            assignments[value] = assigned
        mask.append(assigned == partition_index)
    return table.filter(pa.array(mask))


def _output_partition(value: str, partition_count: int) -> int:
    physical_bucket = ducklake_varchar_bucket(value, PARTITION_BUCKETS)
    return physical_bucket % partition_count


def write_document_output(
    catalogue: Catalogue,
    output: DocumentProjectionOutput,
    *,
    targets: dict[str, str] | None = None,
    enabled_targets: frozenset[str] = frozenset(DOCUMENT_PROJECTIONS),
) -> int:
    table_names = {
        target: target for target in DOCUMENT_PROJECTIONS
    } | (targets or {})
    if output.finalize_link_source_page_ids:
        if {"links", "link_occurrences"}.issubset(enabled_targets):
            with _target_write_timing("links", "finalize_rollups"):
                _refresh_link_rollups_for_sources(
                    catalogue,
                    output.finalize_link_source_page_ids,
                    links_table=table_names["links"],
                    occurrences_table=table_names["link_occurrences"],
                )
        return 0
    if output.projection is None:
        return apply_document_corrections(
            catalogue,
            output,
            targets=table_names,
            enabled_targets=enabled_targets,
        )
    projection = output.projection
    shadow_generation = all(
        table_names[target].startswith("_atlas_rebuild_")
        for target in enabled_targets
    )
    if shadow_generation:
        missing_projection = _missing_shadow_projection(
            catalogue,
            projection,
            table_names=table_names,
            enabled_targets=enabled_targets,
        )
        with _target_write_timing("bulk_document_bundle", "commit"):
            return commit_document_projection(
                catalogue,
                missing_projection,
                targets=table_names,
                enabled_targets=enabled_targets,
            )
    link_ids = frozenset(
        str(value)
        for table in (projection.links, projection.link_occurrences)
        for value in table["link_id"].to_pylist()
    )
    link_shards = (
        {
            ducklake_varchar_bucket(
                str(value),
                len(_LINK_SHARD_LOCKS),
            )
            for value in link_ids
        }
        if enabled_targets & {"links", "link_occurrences"}
        else set()
    )
    with ExitStack() as locks:
        for shard in sorted(link_shards):
            locks.enter_context(_LINK_SHARD_LOCKS[shard])
        missing_projection = _missing_shadow_projection(
            catalogue,
            projection,
            table_names=table_names,
            enabled_targets=enabled_targets,
        )
        with _target_write_timing("bulk_document_bundle", "commit"):
            written = commit_document_projection(
                catalogue,
                missing_projection,
                targets=table_names,
                enabled_targets=enabled_targets,
            )
        if {"links", "link_occurrences"}.issubset(enabled_targets):
            with _target_write_timing("links", "refresh_rollups"):
                _refresh_link_rollups(
                    catalogue,
                    link_ids,
                    links_table=table_names["links"],
                    occurrences_table=table_names["link_occurrences"],
                )
    return written


def _missing_shadow_projection(
    catalogue: Catalogue,
    projection: DocumentProjection,
    *,
    table_names: dict[str, str],
    enabled_targets: frozenset[str],
) -> DocumentProjection:
    tables = {
        "content_stats": projection.content_stats,
        "html_elements": projection.html_elements,
        "jsonld_values": projection.jsonld_values,
        "links": projection.links,
        "link_occurrences": projection.link_occurrences,
    }
    identity_columns = {
        "content_stats": "content_sha256",
        "html_elements": "content_sha256",
        "jsonld_values": "content_sha256",
        "links": "link_id",
        "link_occurrences": "occurrence_id",
    }
    partition_columns = {
        "links": "source_page_id",
        "link_occurrences": "link_id",
    }
    missing = {}
    for target, table in tables.items():
        missing[target] = (
            missing_arrow_slices(
                catalogue,
                table,
                table_name=table_names[target],
                identity_column=identity_columns[target],
                identities=(
                    projection.content_hashes
                    if target
                    in {"content_stats", "html_elements", "jsonld_values"}
                    else None
                ),
                partition_column=partition_columns.get(target),
            )
            if target in enabled_targets
            else table.slice(0, 0)
        )
    return DocumentProjection(
        content_hashes=frozenset(
            str(value)
            for target in ("content_stats", "html_elements", "jsonld_values")
            if target in enabled_targets
            for value in missing[target]["content_sha256"].to_pylist()
        ),
        document_ids=(
            frozenset(
                str(value)
                for value in missing["link_occurrences"][
                    "document_id"
                ].to_pylist()
            )
            if "link_occurrences" in enabled_targets
            else frozenset()
        ),
        content_stats=missing["content_stats"],
        html_elements=missing["html_elements"],
        jsonld_values=missing["jsonld_values"],
        links=missing["links"],
        link_occurrences=missing["link_occurrences"],
    )


def _refresh_link_rollups(
    catalogue: Catalogue,
    link_ids: frozenset[str] | set[str],
    *,
    links_table: str,
    occurrences_table: str,
) -> None:
    if not link_ids:
        return
    identifiers = sql_string_list(set(link_ids))
    _commit_link_rollup_scope(
        catalogue,
        links_table=links_table,
        occurrences_table=occurrences_table,
        predicate=f"link.link_id IN ({identifiers})",
    )


def _refresh_link_rollups_for_sources(
    catalogue: Catalogue,
    source_page_ids: frozenset[str],
    *,
    links_table: str,
    occurrences_table: str,
) -> None:
    if not source_page_ids:
        return
    identifiers = sql_string_list(set(source_page_ids))
    _commit_link_rollup_scope(
        catalogue,
        links_table=links_table,
        occurrences_table=occurrences_table,
        predicate=f"link.source_page_id IN ({identifiers})",
    )


def _commit_link_rollup_scope(
    catalogue: Catalogue,
    *,
    links_table: str,
    occurrences_table: str,
    predicate: str,
) -> None:
    replacement_rows = [
        {
            "link_id": str(row[0]),
            "source_page_id": str(row[1]),
            "target_page_id": str(row[2]),
            "source_url": str(row[3]),
            "target_url": str(row[4]),
            "relation_scope": str(row[5]),
            "first_seen_at": row[6],
            "last_seen_at": row[7],
            "visit_count": int(row[8]),
            "distinct_content_count": int(row[9]),
            "occurrence_count": int(row[10]),
        }
        for row in catalogue.trusted_remote_rows(
            f"""
            SELECT
              link.link_id,
              link.source_page_id,
              link.target_page_id,
              link.source_url,
              link.target_url,
              link.relation_scope,
              min(occurrence.observed_at),
              max(occurrence.observed_at),
              count(DISTINCT occurrence.visit_id),
              count(DISTINCT occurrence.content_sha256),
              count(*)
            FROM material.{links_table} AS link
            JOIN material.{occurrences_table} AS occurrence
              USING (link_id)
            WHERE {predicate}
            GROUP BY ALL
            ORDER BY link.link_id
            """
        )
    ]
    deleted_ids = frozenset(
        str(link_id)
        for (link_id,) in catalogue.trusted_remote_rows(
            f"""
            SELECT link.link_id
            FROM material.{links_table} AS link
            WHERE {predicate}
              AND NOT EXISTS (
                SELECT 1
                FROM material.{occurrences_table} AS occurrence
                WHERE occurrence.link_id = link.link_id
              )
            ORDER BY link.link_id
            """
        )
    )
    mutations = []
    if deleted_ids:
        mutations.append(
            MaterialMutation(
                file_id="00-delete-link-rollups",
                relation=LINKS,
                table_name=links_table,
                table=pa.table(
                    {
                        "link_id": pa.array(
                            sorted(deleted_ids),
                            type=pa.string(),
                        )
                    }
                ),
                mutation_mode="delete",
                match_columns=("link_id",),
            )
        )
    if replacement_rows:
        mutations.append(
            MaterialMutation(
                file_id="10-replace-link-rollups",
                relation=LINKS,
                table_name=links_table,
                table=pa.Table.from_pylist(
                    replacement_rows,
                    schema=LINK_SCHEMA,
                ),
                mutation_mode="replace",
                match_columns=("link_id",),
            )
        )
    commit_material_mutations(catalogue, tuple(mutations))


def apply_document_corrections(
    catalogue: Catalogue,
    output: DocumentProjectionOutput,
    *,
    targets: dict[str, str] | None = None,
    enabled_targets: frozenset[str] = frozenset(DOCUMENT_PROJECTIONS),
) -> int:
    table_names = {
        target: target for target in DOCUMENT_PROJECTIONS
    } | (targets or {})
    if not output.removed_hashes and not output.replaced_document_ids:
        return 0
    affected_link_ids: set[str] = set()
    shadow_generation = table_names[
        "link_occurrences"
    ].startswith("_atlas_rebuild_")
    if (
        output.replaced_document_ids
        and "link_occurrences" in enabled_targets
        and not shadow_generation
    ):
        document_ids = sql_string_list(set(output.replaced_document_ids))
        affected_link_ids = {
            str(link_id)
            for (link_id,) in catalogue.trusted_remote_rows(
                "SELECT DISTINCT link_id::VARCHAR "
                "FROM material."
                f"{table_names['link_occurrences']} "
                f"WHERE document_id IN ({document_ids})"
            )
        }
    deletions = {
        target: (
            table_names[target],
            "content_sha256",
            "VARCHAR",
            output.removed_hashes,
        )
        for target in (
            "content_stats",
            "html_elements",
            "jsonld_values",
        )
        if target in enabled_targets
    }
    if "link_occurrences" in enabled_targets:
        deletions["link_occurrences"] = (
            table_names["link_occurrences"],
            "document_id",
            "UUID",
            output.replaced_document_ids,
        )
    delete_material_keys(catalogue, deletions)
    if (
        affected_link_ids
        and "links" in enabled_targets
        and not shadow_generation
    ):
        _refresh_link_rollups(
            catalogue,
            affected_link_ids,
            links_table=table_names["links"],
            occurrences_table=table_names["link_occurrences"],
        )
    return 0


def missing_arrow_slices(
    catalogue: Catalogue,
    table: pa.Table,
    *,
    table_name: str,
    identity_column: str,
    identities: frozenset[str] | None = None,
    partition_column: str | None = None,
) -> pa.Table:
    if table.num_rows == 0:
        return table
    lookup_column = partition_column or identity_column
    values = (
        frozenset(
            str(value) for value in table[lookup_column].to_pylist()
        )
        if partition_column is not None
        else identities
        or frozenset(
            str(value) for value in table[identity_column].to_pylist()
        )
    )
    existing: set[str] = set()
    for batch in value_batches(sorted(values), SQL_ID_BATCH_SIZE):
        existing.update(
            str(value)
            for (value,) in catalogue.trusted_remote_rows(
                f"""
                SELECT DISTINCT {identity_column}::VARCHAR
                FROM material.{table_name}
                WHERE {lookup_column} IN (
                  {sql_string_list(set(batch))}
                )
                """
            )
        )
    if not existing:
        return table
    keep = pa.array(
        [
            str(value) not in existing
            for value in table[identity_column].to_pylist()
        ]
    )
    return table.filter(keep)


def _changed_document_corrections(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> set[str]:
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT document_id::VARCHAR, change_type
        FROM ducklake_table_changes(
          {sql_string(catalogue.config.alias)}, 'ingest', 'documents',
          {start_snapshot}, {end_snapshot}
        )
        WHERE document_id IS NOT NULL
        """
    )
    changes: dict[str, set[str]] = defaultdict(set)
    for document_id, change_type in rows:
        changes[str(document_id)].add(str(change_type))
    return {
        document_id
        for document_id, kinds in changes.items()
        if kinds != {"insert"}
    }


def _changed_html_hashes(
    catalogue: Catalogue,
    *,
    start_snapshot: int,
    end_snapshot: int,
) -> set[str]:
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT content_sha256
        FROM ducklake_table_changes(
          {sql_string(catalogue.config.alias)}, 'ingest', 'documents',
          {start_snapshot}, {end_snapshot}
        )
        WHERE lower(detected_media_type) = 'text/html'
          AND content_sha256 IS NOT NULL
        """
    )
    return {str(content_hash) for (content_hash,) in rows}


def _covered_html_hashes(
    catalogue: Catalogue,
    content_hashes: set[str],
) -> set[str]:
    if not content_hashes:
        return set()
    rows = catalogue.trusted_remote_rows(
        f"""
        SELECT DISTINCT content_sha256
        FROM material.content_stats
        WHERE content_sha256 IN ({sql_string_list(content_hashes)})
        """
    )
    return {str(content_hash) for (content_hash,) in rows}
