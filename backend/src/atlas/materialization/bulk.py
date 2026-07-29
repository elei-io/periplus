"""Deterministic Parquet staging for fixed document projections."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

import duckdb
import pyarrow as pa

from atlas.materialization.contracts import DOCUMENT_PROJECTIONS, RELATIONS
from atlas.materialization.document_projection import DocumentProjection
from atlas.platform.catalogue import (
    BulkCommitFile,
    Catalogue,
)
from atlas.platform.catalogue.schema import (
    TABLE_LAYOUTS,
    RelationName,
    expected_columns,
)

_BULK_MAX_FILES = 128


@dataclass(frozen=True, slots=True)
class MaterialMutation:
    file_id: str
    relation: RelationName
    table_name: str
    table: pa.Table
    mutation_mode: Literal["append", "replace", "delete"]
    match_columns: tuple[str, ...] = ()


def commit_material_mutations(
    catalogue: Catalogue,
    mutations: tuple[MaterialMutation, ...],
) -> int:
    """Commit typed, bounded material mutations in one Basin snapshot."""

    selected = tuple(
        mutation
        for mutation in mutations
        if mutation.table.num_rows
    )
    if not selected:
        return 0
    with TemporaryDirectory(prefix="atlas-material-mutate-") as temporary:
        root = Path(temporary)
        connection = duckdb.connect(":memory:", config={"threads": "1"})
        try:
            files = tuple(
                _write_relation_file(
                    connection,
                    root,
                    mutation=mutation,
                )
                for mutation in sorted(
                    selected,
                    key=lambda item: item.file_id,
                )
            )
        finally:
            connection.close()
        catalogue.commit_bulk_files(
            files,
            idempotency_key=_idempotency_key(files),
        )
    return sum(item.table.num_rows for item in selected)


def commit_document_projection(
    catalogue: Catalogue,
    projection: DocumentProjection,
    *,
    targets: Mapping[str, str],
    enabled_targets: frozenset[str],
) -> int:
    """Commit one replay-safe document output without a Quack mutation."""

    selected = tuple(
        target
        for target in DOCUMENT_PROJECTIONS
        if target in enabled_targets
    )
    if not selected:
        return 0
    with staged_document_projection(
        projection,
        targets=targets,
        enabled_targets=enabled_targets,
    ) as files:
        for chunk in _chunks(files, _BULK_MAX_FILES):
            catalogue.commit_bulk_files(
                chunk,
                idempotency_key=_idempotency_key(chunk),
            )
    return sum(
        getattr(projection, target).num_rows
        for target in selected
    )


def delete_material_keys(
    catalogue: Catalogue,
    deletions: Mapping[str, tuple[str, str, str, frozenset[str]]],
) -> None:
    """Atomically delete bounded target slices through Basin."""

    selected = {
        target: spec
        for target, spec in deletions.items()
        if spec[3]
    }
    if not selected:
        return
    mutations = tuple(
        MaterialMutation(
            file_id=f"delete-{target}",
            relation=RELATIONS[target],
            table_name=table_name,
            table=pa.table(
                {
                    column_name: pa.array(
                        sorted(values),
                        type=pa.string(),
                    )
                }
            ),
            mutation_mode="delete",
            match_columns=(column_name,),
        )
        for target, (
            table_name,
            column_name,
            _column_type,
            values,
        ) in sorted(selected.items())
    )
    commit_material_mutations(catalogue, mutations)


@contextmanager
def staged_document_projection(
    projection: DocumentProjection,
    *,
    targets: Mapping[str, str],
    enabled_targets: frozenset[str],
) -> Iterator[tuple[BulkCommitFile, ...]]:
    """Stage exact partition-aligned files for one bounded output."""

    with TemporaryDirectory(prefix="atlas-material-") as temporary:
        root = Path(temporary)
        files: list[BulkCommitFile] = []
        connection = duckdb.connect(":memory:", config={"threads": "1"})
        try:
            for target in DOCUMENT_PROJECTIONS:
                if target not in enabled_targets:
                    continue
                table = getattr(projection, target)
                files.extend(
                    _stage_target(
                        connection,
                        root,
                        target=target,
                        table_name=targets[target],
                        table=table,
                    )
                )
        finally:
            connection.close()
        yield tuple(sorted(files, key=lambda item: item.file_id))


def _stage_target(
    connection,
    root: Path,
    *,
    target: str,
    table_name: str,
    table: pa.Table,
) -> list[BulkCommitFile]:
    if table.num_rows == 0:
        return []
    return [
        _write_staged_file(
            connection,
            root,
            target=target,
            table_name=table_name,
            table=table,
            file_id=target,
            ingest_mode="copy",
        )
    ]


def _write_staged_file(
    connection,
    root: Path,
    *,
    target: str,
    table_name: str,
    table: pa.Table,
    file_id: str,
    ingest_mode: Literal["register", "copy"],
) -> BulkCommitFile:
    return _write_relation_file(
        connection,
        root,
        mutation=MaterialMutation(
            file_id=file_id,
            relation=RELATIONS[target],
            table_name=table_name,
            table=table,
            mutation_mode="append",
        ),
        ingest_mode=ingest_mode,
    )


def _write_relation_file(
    connection,
    root: Path,
    *,
    mutation: MaterialMutation,
    ingest_mode: Literal["register", "copy"] = "copy",
) -> BulkCommitFile:
    relation = mutation.relation
    columns = expected_columns()[relation]
    layout = TABLE_LAYOUTS[relation]
    available = set(mutation.table.column_names)
    if (
        mutation.mutation_mode != "delete"
        and not set(columns).issubset(available)
    ):
        raise ValueError(
            f"{relation.qualified} mutation must provide every target column"
        )
    if not set(mutation.match_columns).issubset(available):
        raise ValueError("mutation match columns are missing from staged rows")
    order_by = (
        ", ".join(layout.sort_by)
        if mutation.mutation_mode != "delete"
        else ", ".join(
            _quote_identifier(column)
            for column in mutation.match_columns
        )
    )
    registration = (
        f"_atlas_stage_{mutation.file_id.replace('-', '_')}"
    )
    path = root / f"{mutation.file_id}.parquet"
    connection.register(registration, mutation.table)
    try:
        projections = ", ".join(
            _projection(name, column.data_type)
            for name, column in columns.items()
            if name in available
        )
        connection.execute(
            f"""
            COPY (
              SELECT {projections}
              FROM {_quote_identifier(registration)}
              ORDER BY {order_by}
            ) TO ? (
              FORMAT PARQUET,
              COMPRESSION ZSTD,
              ROW_GROUP_SIZE 122880,
              PER_THREAD_OUTPUT FALSE
            )
            """,
            [str(path)],
        )
    finally:
        connection.unregister(registration)
    sha256, md5 = _checksums(path)
    return BulkCommitFile(
        file_id=mutation.file_id,
        schema="material",
        table=mutation.table_name,
        ingest_mode=ingest_mode,
        mutation_mode=mutation.mutation_mode,
        match_columns=mutation.match_columns,
        path=path,
        rows=mutation.table.num_rows,
        sha256=sha256,
        md5=md5,
    )


def _projection(name: str, data_type) -> str:
    quoted = _quote_identifier(name)
    renderer = getattr(data_type, "sql", None)
    target_type = str(renderer() if callable(renderer) else data_type)
    if target_type == "VARIANT":
        return f"{quoted}::JSON::VARIANT AS {quoted}"
    return f"{quoted}::{target_type} AS {quoted}"


def _checksums(path: Path) -> tuple[str, str]:
    sha256 = hashlib.sha256()
    md5 = hashlib.md5()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            sha256.update(block)
            md5.update(block)
    return sha256.hexdigest(), md5.hexdigest()


def _idempotency_key(files: tuple[BulkCommitFile, ...]) -> str:
    manifest = [
        {
            "file_id": item.file_id,
            "schema": item.schema,
            "table": item.table,
            "ingest_mode": item.ingest_mode,
            "mutation_mode": item.mutation_mode,
            "match_columns": list(item.match_columns),
            "bytes": item.bytes,
            "rows": item.rows,
            "sha256": item.sha256,
            "md5": item.md5,
            "partition_values": [
                {"key": value.key, "value": value.value}
                for value in item.partition_values
            ],
        }
        for item in files
    ]
    digest = hashlib.sha256(
        json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return f"atlas:material:append:v1:{digest}"


def _chunks(
    files: tuple[BulkCommitFile, ...],
    size: int,
) -> Iterator[tuple[BulkCommitFile, ...]]:
    for offset in range(0, len(files), size):
        yield files[offset : offset + size]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'
