"""Typed exploratory search over the analytical catalogue."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from repository.catalogue.search import SEARCH_TYPES, SearchType, compile_search


router = APIRouter(prefix="/search", tags=["search"])


class SearchTypeResponse(BaseModel):
    type: SearchType
    label: str
    description: str


class SearchRegistryResponse(BaseModel):
    items: list[SearchTypeResponse]


SearchQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1_000),
]


class SearchCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: SearchQuery
    result_type: SearchType
    limit: int = Field(default=50, ge=1, le=200)


class SearchCompileResponse(BaseModel):
    result_type: SearchType
    sql: str
    investigation_sql: str


@router.get("/types", response_model=SearchRegistryResponse)
def search_types() -> SearchRegistryResponse:
    return SearchRegistryResponse(
        items=[
            SearchTypeResponse(
                type=strategy.type,
                label=strategy.label,
                description=strategy.description,
            )
            for strategy in SEARCH_TYPES.values()
        ]
    )


@router.post("/compile", response_model=SearchCompileResponse)
def compile_search_request(payload: SearchCompileRequest) -> SearchCompileResponse:
    sql = compile_search(payload.result_type, payload.query, payload.limit)
    return SearchCompileResponse(
        result_type=payload.result_type,
        sql=sql,
        investigation_sql=sql,
    )
