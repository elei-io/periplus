from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import os
from pathlib import Path
import tempfile

import pyarrow as pa

from config import get_int
from control.catalogue_materializations.models import CatalogueMaterialization
from control.catalogue_queries.models import CatalogueQueryRevision
from db.session import session_scope
from materialization.fencing import StaleMaterializationJob, require_current_scope_job
from materialization.queue import MaterializationCommitJob, MaterializationScopeJob
from repository.catalogue import Catalogue, catalogue_from_env
from repository.catalogue.materializations import scoped_view_query
from repository.catalogue.query import classify_select
from repository.catalogue.views import CatalogueViewStore
from repository.objects.config import object_store_from_env, staging_root_from_env


def compute_scope(job: MaterializationScopeJob) -> MaterializationCommitJob:
    """Evaluate one document and stage a bounded Arrow object for the writer."""

    started_at = datetime.now(UTC)
    with session_scope() as session:
        materialization = require_current_scope_job(
            session.get(CatalogueMaterialization, job.materialization_id), job
        )
        if job.query_revision_id is not None:
            revision = session.get(CatalogueQueryRevision, job.query_revision_id)
            if revision is None or revision.query_id != materialization.query_id:
                raise StaleMaterializationJob(
                    f"query revision {job.query_revision_id} is no longer attached"
                )
            query_sql = revision.sql
            bound_view_uuid = None
        else:
            if materialization.bound_ducklake_view_uuid is None:
                raise StaleMaterializationJob("view materialization has no bound source")
            query_sql = None
            bound_view_uuid = materialization.bound_ducklake_view_uuid

    maximum_rows = get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_ROWS")
    maximum_bytes = get_int("ATLAS_MATERIALIZATION_MAX_OUTPUT_BYTES")
    temporary_path = _temporary_arrow_path(job)
    row_count = 0
    output_bytes = 0
    try:
        with catalogue_from_env() as catalogue:
            if query_sql is not None:
                sql = query_sql
            else:
                assert bound_view_uuid is not None
                view = CatalogueViewStore(catalogue).get(bound_view_uuid)
                if view is None:
                    raise StaleMaterializationJob(
                        f"bound view {bound_view_uuid} is no longer current"
                    )
                sql = scoped_view_query(
                    catalogue, view, scope_kind=job.scope_kind, scope_column=job.scope_column
                )
            classify_select(sql)
            catalogue.connection.execute("SET memory_limit = '512MB'")
            catalogue.connection.execute(
                f'USE "{catalogue.config.alias}"."{catalogue.config.schema}"'
            )
            reader = catalogue.connection.execute(
                sql, {f"{job.scope_kind}_id": job.scope_id}
            ).fetch_record_batch(rows_per_batch=4096)
            row_count, output_bytes = _write_bounded_arrow(
                reader,
                temporary_path,
                maximum_rows=maximum_rows,
                maximum_bytes=maximum_bytes,
            )

        staging_key = (
            f"staging/materializations/{job.definition_revision_id}/"
            f"{job.operation_id}.arrow"
        )
        digest = _file_sha256(temporary_path)
        file_bytes = temporary_path.stat().st_size
        store = object_store_from_env()
        with temporary_path.open("rb") as content:
            created = store.put_if_absent(staging_key, content)
        if not created:
            with store.open(staging_key) as content:
                existing_digest = _stream_sha256(content)
            if existing_digest != digest:
                raise RuntimeError(
                    f"staging object {staging_key!r} already exists with different bytes"
                )
        return MaterializationCommitJob(
            scope=job,
            staging_key=staging_key,
            staging_sha256=digest,
            row_count=row_count,
            output_bytes=output_bytes,
            file_bytes=file_bytes,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
    finally:
        temporary_path.unlink(missing_ok=True)


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
