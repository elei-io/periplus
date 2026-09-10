"""Frozen bounded candidates and a retry-safe admission cursor."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from periplus.crawl.runtime.selection_sql import MAX_FOLLOW_LINKS, selected_urls


class SelectionCheckpoint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    urls: tuple[str, ...]
    cursor: int = Field(default=0, ge=0)
    source_snapshot: str | None = None
    source_query_id: str | None = None
    selected_at: datetime | None = None

    @field_validator("urls")
    @classmethod
    def bounded_urls(cls, values):
        return selected_urls(values, max_rows=MAX_FOLLOW_LINKS)

    @model_validator(mode="after")
    def valid_cursor(self):
        if self.cursor > len(self.urls):
            raise ValueError("selection cursor exceeds frozen candidates")
        return self
