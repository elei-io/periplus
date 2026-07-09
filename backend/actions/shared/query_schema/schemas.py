from typing import Any
from typing import Literal

from pydantic import BaseModel, Field, field_validator

QueryParamKind = Literal["text", "enum", "range", "sort", "pagination", "state", "unknown"]
PaginationRole = Literal["next", "prev", "index"]
_QUERY_PARAM_KINDS = {"text", "enum", "range", "sort", "pagination", "state", "unknown"}
_PAGINATION_ROLES = {"next", "prev", "index"}


class QueryParamCandidate(BaseModel):
    url: str
    source: str
    selector: str | None = None
    text: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)


class QueryParamResult(BaseModel):
    value: str
    label: str | None = None
    source: str | None = None
    confidence: float = Field(ge=0, le=1)


class QueryParamGroup(BaseModel):
    key: str
    kind: QueryParamKind = "unknown"
    pagination_role: PaginationRole | None = None
    best_effort_description: str
    confidence: float = Field(ge=0, le=1)
    values: list[QueryParamResult] = Field(default_factory=list)

    @field_validator("kind", mode="before")
    @classmethod
    def normalize_kind(cls, value: object) -> QueryParamKind:
        if isinstance(value, str) and value in _QUERY_PARAM_KINDS:
            return value  # type: ignore[return-value]
        return "unknown"

    @field_validator("pagination_role", mode="before")
    @classmethod
    def normalize_pagination_role(cls, value: object) -> PaginationRole | None:
        if isinstance(value, str) and value in _PAGINATION_ROLES:
            return value  # type: ignore[return-value]
        return None


class QuerySchema(BaseModel):
    schema_type: str = "css"
    extraction_schema: dict[str, Any] = Field(default_factory=dict)


class QueryParamOutput(BaseModel):
    url: str
    crawl_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    candidates: list[QueryParamCandidate] = Field(default_factory=list)
    query_schema: QuerySchema | None = None
    params: list[QueryParamGroup] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
