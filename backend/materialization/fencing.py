"""Validity checks shared by materialization evaluation and repository commit."""

from __future__ import annotations

from control.catalogue_materializations.models import CatalogueMaterialization
from materialization.queue import MaterializationScopeJob


class StaleMaterializationJob(RuntimeError):
    """The queued scope no longer belongs to the active materialization definition."""


def scope_job_is_current(
    materialization: CatalogueMaterialization | None,
    job: MaterializationScopeJob,
) -> bool:
    if materialization is None:
        return False
    source_enabled = (
        materialization.live_enabled
        if job.source == "live"
        else materialization.backfill_enabled
    )
    return bool(
        materialization.archived_at is None
        and materialization.dematerialization_requested_at is None
        and materialization.source_state == "current"
        and materialization.refresh_mode == "scope_incremental"
        and materialization.definition_revision_id == job.definition_revision_id
        and materialization.active_query_revision_id == job.query_revision_id
        and materialization.scope_kind == job.scope_kind
        and materialization.scope_column == job.scope_column
        and materialization.name == job.target_table
        and source_enabled
    )


def require_current_scope_job(
    materialization: CatalogueMaterialization | None,
    job: MaterializationScopeJob,
) -> CatalogueMaterialization:
    if not scope_job_is_current(materialization, job):
        raise StaleMaterializationJob(
            f"materialization scope {job.operation_id} is no longer active"
        )
    assert materialization is not None
    return materialization
