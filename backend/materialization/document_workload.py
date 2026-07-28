"""One-pass document projection shared by CDC and maintenance."""

from __future__ import annotations

import math
import threading
from collections import defaultdict
from contextlib import ExitStack
from dataclasses import dataclass

import pyarrow as pa
from control.urls import normalize_url
from repository.catalogue import Catalogue
from repository.catalogue.schema import PARTITION_BUCKETS
from repository.objects.html import RawHtmlRepository

from materialization.contracts import DOCUMENT_PROJECTIONS
from materialization.document_projection import (
    DocumentObservation,
    DocumentProjection,
    DocumentProjectionSource,
    ducklake_varchar_bucket,
    project_documents,
)
from materialization.document_sources import (
    changed_document_ids,
    document_observation_rows,
    html_documents,
)
from materialization.pipeline import (
    BoundedStagePlan,
    StageSelection,
)
from materialization.sql import (
    SQL_ID_BATCH_SIZE,
    sql_string,
    sql_string_list,
    value_batches,
)

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
        for content_hash, object_key, storage_encoding in html_documents(
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
            links=_concat_unique(
                tuple(
                    projection.links
                    for projection in projections
                ),
                key="link_id",
            ),
            link_observations=pa.concat_tables(
                [
                    projection.link_observations
                    for projection in projections
                ]
            ),
        )
    )


def _concat_unique(
    tables: tuple[pa.Table, ...],
    *,
    key: str,
) -> pa.Table:
    combined = pa.concat_tables(tables)
    seen: set[str] = set()
    indexes: list[int] = []
    for index, raw_value in enumerate(combined[key].to_pylist()):
        value = str(raw_value)
        if value in seen:
            continue
        seen.add(value)
        indexes.append(index)
    return combined.take(pa.array(indexes, type=pa.int64()))


def partition_document_output(
    output: DocumentProjectionOutput,
    *,
    enabled_targets: frozenset[str],
    target_bytes: int = _WRITE_TARGET_BYTES,
    max_partitions: int = 8,
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
            key="source_page_id",
            partition_index=partition_index,
            partition_count=partition_count,
        )
        link_observations = _filter_output_partition(
            projection.link_observations,
            key="document_id",
            partition_index=partition_index,
            partition_count=partition_count,
        )
        split = DocumentProjection(
            content_hashes=frozenset(
                str(value)
                for table in (html_elements, jsonld_values)
                for value in table["content_sha256"].to_pylist()
            ),
            document_ids=frozenset(
                str(value)
                for value in link_observations["document_id"].to_pylist()
            ),
            html_elements=html_elements,
            jsonld_values=jsonld_values,
            links=links,
            link_observations=link_observations,
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
    if output.projection is None:
        return apply_document_corrections(
            catalogue,
            output,
            targets=table_names,
            enabled_targets=enabled_targets,
        )
    projection = output.projection
    link_shards = (
        {
            ducklake_varchar_bucket(
                str(value),
                len(_LINK_SHARD_LOCKS),
            )
            for value in projection.links["source_page_id"].to_pylist()
        }
        if "links" in enabled_targets
        else set()
    )
    written = []
    with ExitStack() as locks:
        for shard in sorted(link_shards):
            locks.enter_context(_LINK_SHARD_LOCKS[shard])
        with catalogue.remote_transaction():
            for target, table, identity_column, identities, variants in (
                (
                    "html_elements",
                    projection.html_elements,
                    "content_sha256",
                    projection.content_hashes,
                    frozenset(),
                ),
                (
                    "jsonld_values",
                    projection.jsonld_values,
                    "content_sha256",
                    projection.content_hashes,
                    frozenset({"value"}),
                ),
                (
                    "links",
                    projection.links,
                    "link_id",
                    None,
                    frozenset(),
                ),
                (
                    "link_observations",
                    projection.link_observations,
                    "document_id",
                    projection.document_ids,
                    frozenset(),
                ),
            ):
                if target not in enabled_targets:
                    continue
                missing = missing_arrow_slices(
                    catalogue,
                    table,
                    table_name=table_names[target],
                    identity_column=identity_column,
                    identities=identities,
                )
                catalogue.append_arrow(
                    table_names[target],
                    missing,
                    schema_name="material",
                    variant_columns=variants,
                )
                written.append(missing)
    return sum(table.num_rows for table in written)


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
    with catalogue.remote_transaction():
        if output.removed_hashes and (
            enabled_targets & {"html_elements", "jsonld_values"}
        ):
            hashes = sql_string_list(set(output.removed_hashes))
            for target in ("html_elements", "jsonld_values"):
                if target in enabled_targets:
                    catalogue.trusted_remote_execute(
                        f"DELETE FROM material.{table_names[target]} "
                        f"WHERE content_sha256 IN ({hashes})"
                    )
        if (
            output.replaced_document_ids
            and "link_observations" in enabled_targets
        ):
            document_ids = sql_string_list(
                set(output.replaced_document_ids)
            )
            catalogue.trusted_remote_execute(
                "DELETE FROM material."
                f"{table_names['link_observations']} "
                f"WHERE document_id IN ({document_ids})"
            )
    return 0


def missing_arrow_slices(
    catalogue: Catalogue,
    table: pa.Table,
    *,
    table_name: str,
    identity_column: str,
    identities: frozenset[str] | None = None,
) -> pa.Table:
    if table.num_rows == 0:
        return table
    values = identities or frozenset(
        str(value) for value in table[identity_column].to_pylist()
    )
    existing: set[str] = set()
    for batch in value_batches(sorted(values), SQL_ID_BATCH_SIZE):
        existing.update(
            str(value)
            for (value,) in catalogue.trusted_remote_rows(
                f"""
                SELECT DISTINCT {identity_column}::VARCHAR
                FROM material.{table_name}
                WHERE {identity_column} IN (
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
        FROM material.html_elements
        WHERE content_sha256 IN ({sql_string_list(content_hashes)})
        """
    )
    return {str(content_hash) for (content_hash,) in rows}
