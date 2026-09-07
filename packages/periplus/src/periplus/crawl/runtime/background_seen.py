"""Bounded historical eligibility checks; absence is valid only at a pinned snapshot."""
from collections.abc import Callable
from datetime import datetime
import json
from uuid import UUID
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from periplus.crawl.runtime.selection_contract import SelectionCheckpoint
from periplus.crawl.runtime.selection_sql import selected_urls
from periplus.query.service import QueryRequest


class BackgroundLookupUnavailable(RuntimeError):
    """Keep candidates pending; lookup failure never proves a URL unseen."""


class SeenCandidates(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    urls: tuple[str, ...] = Field(min_length=1, max_length=64)

    @field_validator("urls")
    @classmethod
    def normalize(cls, values):
        urls = selected_urls(values)
        if len(json.dumps(urls).encode()) > 64 * 1024:
            raise ValueError("background candidate batch exceeds 64 KiB")
        return urls

    def query(self) -> QueryRequest:
        # One bound array is reused in both branches. Filtering each URL role
        # directly lets DuckDB push candidate predicates below observation joins.
        return QueryRequest(sql="""
            SELECT DISTINCT url FROM (
                SELECT requested_url AS url FROM web.observation
                WHERE list_contains($1::VARCHAR[], requested_url)
                UNION ALL
                SELECT effective_url AS url FROM web.observation
                WHERE outcome = 'succeeded'
                  AND list_contains($1::VARCHAR[], effective_url)
            ) AS seen
            ORDER BY url
        """, parameters=[list(self.urls)])


class HistoricalSeenResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    candidates: SeenCandidates
    seen_urls: tuple[str, ...]
    snapshot: int = Field(ge=0)
    query_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def subset(self):
        if not set(self.seen_urls).issubset(self.candidates.urls):
            raise ValueError("historical result includes an unrequested URL")
        if len(set(self.seen_urls)) != len(self.seen_urls):
            raise ValueError("historical result repeats a URL")
        return self


class BackgroundCheck(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    parent_observation_id: UUID
    token: UUID
    candidates: SeenCandidates
    policy_version: int
    expires_at: datetime
    result: HistoricalSeenResult | None = None



class BackgroundAdmission(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    url: str
    status: Literal["admitted", "seen", "already_pending", "declined"]
    acquisition_id: UUID | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def identity(self):
        if (self.status == "admitted") != (self.acquisition_id is not None):
            raise ValueError("only admitted background decisions carry an acquisition ID")
        return self


def lookup_seen(candidates: SeenCandidates,
                select: Callable[[QueryRequest], SelectionCheckpoint]) -> HistoricalSeenResult:
    try:
        selected = select(candidates.query())
        if selected.source_snapshot is None or not selected.source_snapshot.isdecimal():
            raise ValueError("historical lookup has no source snapshot")
        return HistoricalSeenResult(
            candidates=candidates, seen_urls=selected.urls,
            snapshot=int(selected.source_snapshot), query_id=selected.source_query_id,
        )
    except (ValueError, RuntimeError) as exc:
        raise BackgroundLookupUnavailable("historical URL eligibility is unavailable") from exc


def perform_seen_check(store, parent_observation_id: UUID, candidates: SeenCandidates,
                       select: Callable[[QueryRequest], SelectionCheckpoint]) -> BackgroundCheck:
    # Registration precedes the lake transaction. A concurrent cleanup must retain
    # markers until this check has a snapshot, finishes, or expires.
    check = store.start_background_check(parent_observation_id, candidates)
    result = lookup_seen(candidates, select)
    return store.finish_background_check(check, result)
