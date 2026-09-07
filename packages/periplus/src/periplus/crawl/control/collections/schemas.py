"""Frozen collection intent; selection and acquisition have independent identities."""

from datetime import datetime
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, TypeAdapter, field_validator, model_validator

from periplus.urls import normalize_url


class CollectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seed_urls: tuple[str, ...] = Field(default=(), max_length=1000)
    seed_description: str | None = Field(default=None, min_length=1, max_length=4000)
    seed_sql: str | None = Field(default=None, max_length=20000)
    seed_parameters: tuple[JsonValue, ...] = Field(default=(), max_length=100)
    follow_sql: str = Field(
        default="SELECT target_url AS url FROM nav.links", max_length=20000
    )
    max_depth: int = Field(default=0, ge=0, le=100)
    page_limit: int = Field(default=25, ge=1, le=100000)
    result_max_age_seconds: int = Field(default=300, ge=0, le=3600)
    visibility: Literal["public", "private"] = "public"
    access_context: str = Field(default="public", min_length=1, max_length=200)
    allowed_sections: tuple[str, ...] = Field(default=(), max_length=100)
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def bounded_intent(self):
        if self.seed_parameters and not self.seed_sql:
            raise ValueError("seed parameters require seed SQL")
        if len(self.model_dump_json().encode()) > 256 * 1024:
            raise ValueError("collection intent exceeds 256 KiB")
        if len(json.dumps({"sql": self.seed_sql or "", "parameters": self.seed_parameters},
                          ensure_ascii=False).encode()) > 120 * 1024:
            raise ValueError("seed SQL and parameters exceed the query request budget")
        return self

    @field_validator("seed_description")
    @classmethod
    def description(cls, value: str | None) -> str | None:
        if value is not None:
            value = value.strip()
            if not value:
                raise ValueError("source description cannot be blank")
        return value

    @field_validator("allowed_sections")
    @classmethod
    def sections(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        sections = []
        for value in values:
            if len(value) > 1000:
                raise ValueError("allowed section exceeds 1,000 characters")
            url = TypeAdapter(HttpUrl).validate_python(value)
            if url.username or url.password or url.query is not None or url.fragment is not None:
                raise ValueError("allowed sections cannot contain credentials, queries, or fragments")
            sections.append(str(url).rstrip("/"))
        return tuple(dict.fromkeys(sections))

    @field_validator("seed_urls")
    @classmethod
    def urls(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(normalize_url(value) for value in values))

    @field_validator("deadline_at")
    @classmethod
    def aware_deadline(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() is None:
            raise ValueError("deadline must include a timezone")
        return value


class SelectionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    depth: int = Field(ge=0)
    parent_observation_id: UUID | None = None
    rule_id: str = Field(min_length=1, max_length=200)
