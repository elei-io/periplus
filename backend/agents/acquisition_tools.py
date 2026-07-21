"""Typed, read-only acquisition-planning capabilities for Atlas agents."""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from api.catalogue_control import CatalogueControl
from control.crawl_graphs.schemas import CrawlGraphDetail
from control.crawl_graphs.service import (
    detail,
    get_graph,
    list_graphs as list_graph_records,
)
from control.crawl_schedules.schemas import CrawlScheduleResource
from control.crawl_schedules.service import list_schedule_resources


BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


class SeedSearchResult(BaseModel):
    """One ephemeral web result available to the agent and live response."""

    model_config = ConfigDict(extra="forbid")

    title: str
    url: str
    description: str | None = None


class SeedSearchResponse(BaseModel):
    """Bounded web discovery results that are never persisted by Atlas."""

    model_config = ConfigDict(extra="forbid")

    query: str
    results: list[SeedSearchResult] = Field(max_length=10)
    unavailable_reason: str | None = None


class AcquisitionPlan(BaseModel):
    """An ephemeral, user-reviewable plan derived by the Atlas agent."""

    model_config = ConfigDict(extra="forbid")

    graph_id: UUID
    graph_slug: str
    start_urls: list[str] = Field(min_length=1, max_length=100)
    recommended_run_type: Literal["one_off", "scheduled"]
    schedule_summary: str | None = Field(default=None, max_length=500)


class AcquisitionTools:
    """Read capabilities used to propose, but never execute, acquisition plans."""

    def __init__(
        self,
        catalogue_control: CatalogueControl,
        *,
        brave_api_key: str | None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.catalogue_control = catalogue_control
        self._brave_api_key = brave_api_key
        self._http_client = http_client

    async def list_graphs(self) -> list[CrawlGraphDetail]:
        """Return complete graph definitions so the agent can judge their behavior."""

        return await self.catalogue_control.run(
            lambda session, _catalogue: [
                detail(get_graph(session, record.id))
                for record in list_graph_records(session)
            ]
        )

    async def list_schedules(self) -> list[CrawlScheduleResource]:
        """Return all configured schedules with graph identity and root URLs."""

        return await self.catalogue_control.run(
            lambda session, _catalogue: list_schedule_resources(session)
        )

    async def search_web(
        self,
        query: str,
        *,
        count: int = 10,
        country: str = "US",
        search_lang: str = "en",
        ui_lang: str = "en-US",
        safesearch: Literal["off", "moderate", "strict"] = "moderate",
        freshness: Literal["any", "day", "week", "month", "year"] = "any",
    ) -> SeedSearchResponse:
        """Search the public web for candidate crawl seeds without retaining results."""

        normalized = " ".join(query.split())
        if not normalized:
            raise ValueError("Web search query must not be blank.")
        if len(normalized) > 400 or len(normalized.split()) > 50:
            raise ValueError(
                "Web search query must contain at most 400 characters and 50 words."
            )
        if not 1 <= count <= 10:
            raise ValueError("Web search count must be between 1 and 10.")
        normalized_country = country.strip().upper()
        if len(normalized_country) != 2 or not normalized_country.isalpha():
            raise ValueError("Web search country must be a two-letter country code.")
        normalized_search_lang = search_lang.strip().lower()
        if not _valid_language_code(normalized_search_lang):
            raise ValueError("Web search language is invalid.")
        normalized_ui_lang = ui_lang.strip()
        if not _valid_ui_language(normalized_ui_lang):
            raise ValueError("Web search UI language must look like en-US.")
        if self._brave_api_key is None:
            return SeedSearchResponse(
                query=normalized,
                results=[],
                unavailable_reason="Brave Search is not configured for this Atlas deployment.",
            )

        if self._http_client is not None:
            return await self._request_search(
                self._http_client,
                normalized,
                count,
                normalized_country,
                normalized_search_lang,
                normalized_ui_lang,
                safesearch,
                freshness,
            )

        async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
            return await self._request_search(
                client,
                normalized,
                count,
                normalized_country,
                normalized_search_lang,
                normalized_ui_lang,
                safesearch,
                freshness,
            )

    async def prepare_plan(
        self,
        graph_id: UUID,
        start_urls: list[str],
        recommended_run_type: Literal["one_off", "scheduled"],
        schedule_summary: str | None = None,
    ) -> AcquisitionPlan:
        """Validate a model-selected graph and its derived public start URLs."""

        graph = await self.catalogue_control.run(
            lambda session, _catalogue: detail(get_graph(session, graph_id))
        )
        if graph.root_node_id is None:
            raise ValueError(f"Crawl graph {graph.slug!r} has no root node.")

        normalized_urls: list[str] = []
        for url in start_urls:
            parsed = urlsplit(url.strip())
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(f"Acquisition start URL is not public HTTP(S): {url!r}")
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("Acquisition start URLs must not contain credentials.")
            normalized = urlunsplit(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
            )
            if normalized not in normalized_urls:
                normalized_urls.append(normalized)
        if not normalized_urls:
            raise ValueError("An acquisition plan requires at least one start URL.")

        return AcquisitionPlan(
            graph_id=graph.id,
            graph_slug=graph.slug,
            start_urls=normalized_urls,
            recommended_run_type=recommended_run_type,
            schedule_summary=(
                schedule_summary.strip() if schedule_summary else None
            ),
        )

    async def _request_search(
        self,
        client: httpx.AsyncClient,
        query: str,
        count: int,
        country: str,
        search_lang: str,
        ui_lang: str,
        safesearch: Literal["off", "moderate", "strict"],
        freshness: Literal["any", "day", "week", "month", "year"],
    ) -> SeedSearchResponse:
        params: dict[str, str | int | bool] = {
            "q": query,
            "count": count,
            "country": country,
            "search_lang": search_lang,
            "ui_lang": ui_lang,
            "safesearch": safesearch,
            "text_decorations": False,
        }
        freshness_value = {
            "day": "pd",
            "week": "pw",
            "month": "pm",
            "year": "py",
        }.get(freshness)
        if freshness_value is not None:
            params["freshness"] = freshness_value
        response = await client.get(
            BRAVE_WEB_SEARCH_URL,
            params=params,
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._brave_api_key,
            },
        )
        if response.is_error:
            raise ValueError(
                f"Brave Search failed with HTTP status {response.status_code}."
            )
        payload: Any = response.json()
        candidates = (
            payload.get("web", {}).get("results", [])
            if isinstance(payload, dict)
            else []
        )
        results: list[SeedSearchResult] = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            url = candidate.get("url")
            title = candidate.get("title")
            if not isinstance(url, str) or not isinstance(title, str):
                continue
            parsed = urlsplit(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            description = candidate.get("description")
            results.append(
                SeedSearchResult(
                    title=title.strip()[:500],
                    url=url.strip()[:2_000],
                    description=(
                        description.strip()[:2_000]
                        if isinstance(description, str)
                        else None
                    ),
                )
            )
            if len(results) == count:
                break
        return SeedSearchResponse(query=query, results=results)


def _valid_language_code(value: str) -> bool:
    parts = value.split("-")
    return 1 <= len(parts) <= 2 and all(
        part.isalpha() and 2 <= len(part) <= 4 for part in parts
    )


def _valid_ui_language(value: str) -> bool:
    parts = value.split("-")
    return (
        len(parts) == 2
        and parts[0].islower()
        and parts[1].isupper()
        and len(parts[0]) in {2, 3}
        and len(parts[1]) == 2
        and all(part.isalpha() for part in parts)
    )
