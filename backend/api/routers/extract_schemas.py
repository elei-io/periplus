from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.session import get_session
from extract_schemas.schemas import (
    ExtractSchemaDetailRecord,
    ExtractSchemaListResponse,
    ExtractSchemaUpdateRequest,
)
from extract_schemas.service import (
    count_extract_schemas,
    extract_schema_summary,
    get_extract_schema,
    list_extract_schemas,
    task_run_count,
    update_extract_schema,
    warning_count,
)
from metrics.history import DEFAULT_WINDOW_SECONDS, extract_schema_metrics
from metrics.schemas import HistoryMetricsResponse

router = APIRouter(prefix="/extract-schemas", tags=["extract-schemas"])


def _detail_record(session: Session, schema) -> ExtractSchemaDetailRecord:
    return ExtractSchemaDetailRecord(
        id=schema.id,
        identity_key=schema.identity_key,
        match=schema.match,
        enabled=schema.enabled,
        priority=schema.priority,
        prompt=schema.prompt,
        prompt_hash=schema.prompt_hash,
        schema_type=schema.schema_type,
        target_json_hash=schema.target_json_hash,
        domain=schema.domain,
        path=schema.path,
        extraction_schema=schema.schema_json,
        schema_hash=schema.schema_hash,
        generated_from_crawl_id=schema.generated_from_crawl_id,
        generated_from_artifact_id=schema.generated_from_artifact_id,
        generated_by_task_run_id=schema.generated_by_task_run_id,
        inputs_json=schema.inputs_json,
        validation_status=schema.validation_status,
        failure_count=schema.failure_count,
        last_failed_at=schema.last_failed_at,
        last_error=schema.last_error,
        warnings_json=schema.warnings_json,
        created_at=schema.created_at,
        updated_at=schema.updated_at,
        task_run_count=task_run_count(session, schema.id),
        warning_count=warning_count(schema),
    )


@router.get("/metrics", response_model=HistoryMetricsResponse)
def metrics(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    prompt: Annotated[str | None, Query()] = None,
    schema_type: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    warnings: Annotated[bool | None, Query()] = None,
    window_seconds: Annotated[int, Query(ge=60, le=7 * 24 * 60 * 60)] = DEFAULT_WINDOW_SECONDS,
) -> HistoryMetricsResponse:
    return extract_schema_metrics(
        session=session,
        match_pattern=match_pattern,
        prompt=prompt,
        schema_type=schema_type,
        enabled=enabled,
        warnings=warnings,
        window_seconds=window_seconds,
    )


@router.get("/", response_model=ExtractSchemaListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    prompt: Annotated[str | None, Query()] = None,
    schema_type: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    warnings: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ExtractSchemaListResponse:
    return ExtractSchemaListResponse(
        items=list_extract_schemas(
            session=session,
            match_pattern=match_pattern,
            prompt=prompt,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
            limit=limit,
            offset=offset,
        ),
        total=count_extract_schemas(
            session=session,
            match_pattern=match_pattern,
            prompt=prompt,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
        ),
        limit=limit,
        offset=offset,
        summary=extract_schema_summary(
            session=session,
            match_pattern=match_pattern,
            prompt=prompt,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
        ),
    )


@router.get("/{schema_id}", response_model=ExtractSchemaDetailRecord)
def get(
    schema_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> ExtractSchemaDetailRecord:
    schema = get_extract_schema(session=session, schema_id=schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extract schema not found.")

    return _detail_record(session, schema)


@router.patch("/{schema_id}", response_model=ExtractSchemaDetailRecord)
def update(
    schema_id: UUID,
    request: ExtractSchemaUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> ExtractSchemaDetailRecord:
    schema = get_extract_schema(session=session, schema_id=schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Extract schema not found.")

    try:
        updated = update_extract_schema(session=session, schema=schema, request=request)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return _detail_record(session, updated)
