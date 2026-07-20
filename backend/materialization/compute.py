from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import os
from pathlib import Path
import tempfile

import pyarrow as pa

from config import get_int
from control.catalogue_materializations.models import CatalogueMaterialization
from db.session import session_scope
from materialization.fencing import StaleMaterializationJob, require_current_scope_job
from materialization.queue import MaterializationScopeJob
from observability import materialization_metrics
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.materializations import scoped_select
from repository.catalogue.query import classify_select
from repository.objects.config import staging_root_from_env


_MISSING_DOCUMENT_ID = "__atlas_missing_document__"


@dataclass(frozen=True)
class ComputedMaterializationScope:
    """One bounded scope result retained locally until its immediate commit."""

    scope: MaterializationScopeJob
    arrow_path: Path
    arrow_sha256: str
    row_count: int
    output_bytes: int
    file_bytes: int
    started_at: datetime
    completed_at: datetime


def compute_scope(job: MaterializationScopeJob) -> ComputedMaterializationScope:
    """Evaluate one scope into a bounded process-local Arrow file."""

    started_at = datetime.now(UTC)
    with materialization_metrics.operation_timer("compute.control_lookup"):
        with session_scope() as session:
            materialization = require_current_scope_job(
                session.get(CatalogueMaterialization, job.materialization_id), job
            )
            source_sql = materialization.source_sql

    maximum_rows = get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_ROWS")
    maximum_bytes = get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_BYTES")
    with materialization_metrics.operation_timer("compute.local_stage_create"):
        temporary_path = _temporary_arrow_path(job)
    row_count = 0
    output_bytes = 0
    try:
        with materialization_metrics.operation_timer("compute.catalogue_open"):
            catalogue = catalogue_from_env()
        try:
            with materialization_metrics.operation_timer("compute.query_setup"):
                _set_scope_variables(catalogue, job)
                sql = scoped_select(
                    source_sql,
                    scope_kind=job.scope_kind,
                    scope_column=job.scope_column,
                )
                classify_select(sql)
                catalogue.connection.execute("SET memory_limit = '512MB'")
                catalogue.connection.execute(
                    f'USE "{catalogue.config.alias}"."{catalogue.config.schema}"'
                )
            with materialization_metrics.operation_timer(
                "compute.scoped_scan_and_arrow"
            ):
                reader = catalogue.connection.execute(
                    sql, {f"{job.scope_kind}_id": job.scope_id}
                ).fetch_record_batch(rows_per_batch=4096)
                row_count, output_bytes = _write_bounded_arrow(
                    reader,
                    temporary_path,
                    maximum_rows=maximum_rows,
                    maximum_bytes=maximum_bytes,
                )
        finally:
            with materialization_metrics.operation_timer("compute.catalogue_close"):
                catalogue.close()

        with materialization_metrics.operation_timer("compute.checksum"):
            digest = _file_sha256(temporary_path)
            file_bytes = temporary_path.stat().st_size
        result = ComputedMaterializationScope(
            scope=job,
            arrow_path=temporary_path,
            arrow_sha256=digest,
            row_count=row_count,
            output_bytes=output_bytes,
            file_bytes=file_bytes,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise
    return result


def _set_scope_variables(
    catalogue: Catalogue,
    job: MaterializationScopeJob,
) -> None:
    """Expose immutable scope bindings to source SQL for physical pruning."""

    document_id = (
        None
        if job.scope_kind == "url"
        else (
            job.document_id
            if job.document_id is not None
            else (
                job.scope_id
                if job.scope_kind == "document"
                else _MISSING_DOCUMENT_ID
            )
        )
    )
    catalogue.connection.execute(
        "SET VARIABLE atlas_materialization_document_id = ?",
        [document_id],
    )
    if job.scope_kind == "url":
        catalogue.connection.execute(
            "SET VARIABLE atlas_materialization_url_id = ?",
            [job.scope_id],
        )
    if job.scope_kind == "crawl":
        catalogue.connection.execute(
            "SET VARIABLE atlas_materialization_crawl_id = ?",
            [job.scope_id],
        )


def _temporary_arrow_path(job: MaterializationScopeJob) -> Path:
    root = staging_root_from_env() / "materialization-compute"
    root.mkdir(parents=True, exist_ok=True)
    descriptor, value = tempfile.mkstemp(
        dir=root, prefix=f"{job.operation_id}.", suffix=".arrow"
    )
    os.close(descriptor)
    Path(value).unlink()
    return Path(value)


def _write_bounded_arrow(
    reader: pa.RecordBatchReader,
    path: Path,
    *,
    maximum_rows: int,
    maximum_bytes: int,
) -> tuple[int, int]:
    row_count = 0
    output_bytes = 0
    with pa.OSFile(str(path), "wb") as sink:
        with pa.ipc.new_file(sink, reader.schema) as writer:
            for batch in reader:
                row_count += batch.num_rows
                output_bytes += batch.nbytes
                if row_count > maximum_rows:
                    raise RuntimeError(
                        f"materialization exceeded its {maximum_rows} row limit"
                    )
                if output_bytes > maximum_bytes:
                    raise RuntimeError(
                        f"materialization exceeded its {maximum_bytes} byte limit"
                    )
                writer.write_batch(batch)
    return row_count, output_bytes


def _file_sha256(path: Path) -> str:
    with path.open("rb") as content:
        return _stream_sha256(content)


def _stream_sha256(content) -> str:
    digest = sha256()
    while chunk := content.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()
