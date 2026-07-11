from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from db.session import get_session
from control.query_schemas.schemas import QuerySchemaDetailRecord, QuerySchemaListResponse, QuerySchemaUpdateRequest
from control.query_schemas.service import (
    count_query_schemas,
    delete_query_schema,
    get_query_schema,
    list_query_schemas,
    update_query_schema,
    warning_count,
)

router = APIRouter(prefix="/query-schemas", tags=["query-schemas"])


def _detail_record(schema) -> QuerySchemaDetailRecord:
    return QuerySchemaDetailRecord(
        id=schema.id,
        identity_key=schema.identity_key,
        url_match_id=schema.url_match_id,
        match=schema.match,
        enabled=schema.enabled,
        priority=schema.priority,
        schema_type=schema.schema_type,
        domain=schema.domain,
        path=schema.path,
        extraction_schema=schema.schema_json,
        params_json=schema.params_json,
        evidence_json=schema.evidence_json,
        schema_hash=schema.schema_hash,
        generated_from_crawl_id=schema.generated_from_crawl_id,
        generated_from_document_id=schema.generated_from_document_id,
        generated_by_task_run_id=schema.generated_by_task_run_id,
        inputs_json=schema.inputs_json,
        warnings_json=schema.warnings_json,
        created_at=schema.created_at,
        updated_at=schema.updated_at,
        param_count=len(schema.params_json or []),
        evidence_count=len(schema.evidence_json or []),
        warning_count=warning_count(schema),
    )


@router.get("/", response_model=QuerySchemaListResponse)
def list_(
    session: Annotated[Session, Depends(get_session)],
    match_pattern: Annotated[str | None, Query()] = None,
    domain: Annotated[str | None, Query()] = None,
    schema_type: Annotated[str | None, Query()] = None,
    enabled: Annotated[bool | None, Query()] = None,
    warnings: Annotated[bool | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QuerySchemaListResponse:
    return QuerySchemaListResponse(
        items=list_query_schemas(
            session=session,
            match_pattern=match_pattern,
            domain=domain,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
            limit=limit,
            offset=offset,
        ),
        total=count_query_schemas(
            session=session,
            match_pattern=match_pattern,
            domain=domain,
            schema_type=schema_type,
            enabled=enabled,
            warnings=warnings,
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/{schema_id}", response_model=QuerySchemaDetailRecord)
def get(
    schema_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> QuerySchemaDetailRecord:
    schema = get_query_schema(session=session, schema_id=schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Query schema not found.")

    return _detail_record(schema)


@router.patch("/{schema_id}", response_model=QuerySchemaDetailRecord)
def update(
    schema_id: UUID,
    request: QuerySchemaUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> QuerySchemaDetailRecord:
    schema = get_query_schema(session=session, schema_id=schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Query schema not found.")

    updated = update_query_schema(
        session=session,
        schema=schema,
        enabled=request.enabled,
        priority=request.priority,
    )
    return _detail_record(updated)


@router.delete("/{schema_id}", status_code=204)
def delete(
    schema_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    schema = get_query_schema(session=session, schema_id=schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Query schema not found.")

    delete_query_schema(session=session, schema=schema)
