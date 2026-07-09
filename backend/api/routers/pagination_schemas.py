from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from db.session import get_session
from pagination_schemas.schemas import PaginationSchemaRecord, PaginationSchemaUpdateRequest
from pagination_schemas.service import get_pagination_schema, list_pagination_schemas, update_pagination_schema

router = APIRouter(prefix="/pagination-schemas", tags=["pagination-schemas"])


@router.get("/", response_model=list[PaginationSchemaRecord])
def list_(session: Annotated[Session, Depends(get_session)]) -> list[PaginationSchemaRecord]:
    return [PaginationSchemaRecord.model_validate(schema) for schema in list_pagination_schemas(session)]


@router.get("/{schema_id}", response_model=PaginationSchemaRecord)
def get(
    schema_id: UUID,
    session: Annotated[Session, Depends(get_session)],
) -> PaginationSchemaRecord:
    schema = get_pagination_schema(session, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Pagination schema not found.")
    return PaginationSchemaRecord.model_validate(schema)


@router.patch("/{schema_id}", response_model=PaginationSchemaRecord)
def update(
    schema_id: UUID,
    request: PaginationSchemaUpdateRequest,
    session: Annotated[Session, Depends(get_session)],
) -> PaginationSchemaRecord:
    schema = get_pagination_schema(session, schema_id)
    if schema is None:
        raise HTTPException(status_code=404, detail="Pagination schema not found.")
    return PaginationSchemaRecord.model_validate(update_pagination_schema(session, schema, request))
