"""Typed acquisition discovery, reconnaissance, and planning capabilities."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, Literal, get_args
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field

from api.catalogue_control import CatalogueControl
from control.crawl_graphs.schemas import (
    MAX_GRAPH_RUN_CRAWLS,
    CrawlGraphDetail,
)
from control.crawl_graphs.service import (
    detail,
    get_graph,
    list_graphs as list_graph_records,
)
from control.crawl_schedules.schemas import (
    CrawlScheduleResource,
    CrawlScheduleUpdate,
)
from control.crawl_schedules.service import (
    get_schedule_resource,
    list_schedule_resources,
)
from repository.ingestion.queue import (
    crawl_ingestion_request_id,
    ensure_ingestion_results,
    get_ingestion_state,
)
from runtime.graph_queue import (
    ensure_graph_storage,
    get_crawl_request,
    get_graph_run,
    normalize_request_url,
    request_identity,
)
from runtime.graph_runs import deterministic_request_id
from runtime.nats_client import connect_nats


BRAVE_WEB_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"

BraveCountry = Literal[
    "AR", "AU", "AT", "BE", "BR", "CA", "CL", "DK", "FI", "FR", "DE",
    "GR", "HK", "IN", "ID", "IT", "JP", "KR", "MY", "MX", "NL", "NZ",
    "NO", "CN", "PL", "PT", "PH", "RU", "SA", "ZA", "ES", "SE", "CH",
    "TW", "TR", "GB", "US", "ALL",
]
BraveSearchLanguage = Literal[
    "ar", "eu", "bn", "bg", "ca", "zh-hans", "zh-hant", "hr", "cs", "da",
    "nl", "en", "en-gb", "et", "fi", "fr", "gl", "de", "el", "gu", "he",
    "hi", "hu", "is", "it", "jp", "kn", "ko", "lv", "lt", "ms", "ml",
    "mr", "nb", "pl", "pt-br", "pt-pt", "pa", "ro", "ru", "sr", "sk",
    "sl", "es", "sv", "ta", "te", "th", "tr", "uk", "vi",
]
BraveUiLanguage = Literal[
    "es-AR", "en-AU", "de-AT", "nl-BE", "fr-BE", "pt-BR", "en-CA", "fr-CA",
    "es-CL", "da-DK", "fi-FI", "fr-FR", "de-DE", "el-GR", "zh-HK", "en-IN",
    "en-ID", "it-IT", "ja-JP", "ko-KR", "en-MY", "es-MX", "nl-NL", "en-NZ",
    "no-NO", "zh-CN", "pl-PL", "en-PH", "ru-RU", "en-ZA", "es-ES", "sv-SE",
    "fr-CH", "de-CH", "zh-TW", "tr-TR", "en-GB", "en-US", "es-US",
]

_BRAVE_COUNTRIES = frozenset(get_args(BraveCountry))
_BRAVE_SEARCH_LANGUAGES = frozenset(get_args(BraveSearchLanguage))
_BRAVE_UI_LANGUAGES = frozenset(get_args(BraveUiLanguage))
_BRAVE_SEARCH_LANGUAGE_ALIASES = {
    "ja": "jp",
    "no": "nb",
}


class BraveSearchError(RuntimeError):
    """A safe, retriable Brave failure suitable for the agent repair loop."""


class BraveSearchParameterError(BraveSearchError):
    """A Brave request whose arguments should be revised before retrying."""


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
    """One user-reviewable acquisition proposed by the Atlas agent."""

    model_config = ConfigDict(extra="forbid")

    graph_id: UUID
    graph_slug: str
    name: str = Field(default="Acquisition", min_length=1, max_length=200)
    purpose: str = Field(default="Retain relevant public web evidence.", min_length=1, max_length=1_000)
    mode: Literal["reconnaissance", "corpus", "monitoring"] = "corpus"
    start_urls: list[str] = Field(min_length=1, max_length=100)
    max_crawls: int = Field(ge=1, le=MAX_GRAPH_RUN_CRAWLS)
    recommended_run_type: Literal["one_off", "scheduled"]
    schedule_summary: str | None = Field(default=None, max_length=500)
    expected_coverage: str = Field(default="", max_length=2_000)
    success_criteria: str = Field(default="", max_length=2_000)


class AcquisitionPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1_000)
    mode: Literal["reconnaissance", "corpus", "monitoring"]
    graph_id: UUID
    start_urls: list[str] = Field(min_length=1, max_length=100)
    max_crawls: int = Field(ge=1, le=MAX_GRAPH_RUN_CRAWLS)
    recommended_run_type: Literal["one_off", "scheduled"]
    schedule_summary: str | None = Field(default=None, max_length=500)
    expected_coverage: str = Field(default="", max_length=2_000)
    success_criteria: str = Field(default="", max_length=2_000)


class CrawlGraphSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    slug: str
    description: str | None
    system_owned: bool
    node_count: int
    edge_count: int


class CrawlScheduleSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    graph_id: UUID
    graph_slug: str
    name: str
    status: str
    enabled: bool
    root_count: int
    max_crawls: int
    next_run_at: str | None
    last_run_id: UUID | None
    last_error: str | None


class GraphRunInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    graph_id: UUID
    status: str
    trigger_kind: str
    trigger_schedule_id: UUID | None
    trigger_urls: list[str]
    max_crawls: int
    crawl_limit_reached: bool
    request_count: int
    pending_request_count: int
    acquisition_pending_count: int
    failed_request_count: int
    error_count: int
    failure_groups: list[dict[str, Any]]
    created_at: str
    started_at: str | None
    last_progress_at: str | None
    completed_at: str | None
    error: str | None


class PageInspection(BaseModel):
    """Outcome of one bounded page acquisition for catalogue inspection."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        "catalogue_ready",
        "acquisition_failed",
        "ingestion_failed",
        "still_running",
    ]
    graph_run_id: UUID
    crawl_id: UUID | None = None
    requested_url: str
    document_id: str | None = None
    repository_snapshot: int | None = None
    message: str


class ScheduleChangeProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["update", "pause", "resume", "delete"]
    schedule_id: UUID
    graph_id: UUID
    schedule_name: str
    reason: str = Field(min_length=1, max_length=1_000)
    replacement: CrawlScheduleUpdate | None = None


class AcquisitionTools:
    """Capabilities for discovery, bounded reconnaissance, and acquisition plans."""

    def __init__(
        self,
        catalogue_control: CatalogueControl,
        *,
        brave_api_key: str | None,
        http_client: httpx.AsyncClient | None = None,
        page_crawl_submitter: Callable[[UUID, str], Awaitable[UUID]] | None = None,
    ) -> None:
        self.catalogue_control = catalogue_control
        self._brave_api_key = brave_api_key
        self._http_client = http_client
        self._page_crawl_submitter = page_crawl_submitter

    async def inspect_live_page(
        self, url: str, *, wait_seconds: float = 45.0
    ) -> PageInspection:
        """Acquire one page normally and wait for its base catalogue commit."""

        if self._page_crawl_submitter is None:
            raise RuntimeError("Live page inspection is unavailable in this adapter.")
        normalized_url = _normalize_public_url(url)
        if not 1 <= wait_seconds <= 45:
            raise ValueError("Page inspection wait must be between 1 and 45 seconds.")

        graphs = await self.list_graphs()
        graph = next((value for value in graphs if value.slug == "single-page"), None)
        if graph is None:
            raise RuntimeError("The seeded single-page crawl graph is unavailable.")
        run_id = await self._page_crawl_submitter(graph.id, normalized_url)
        crawl_id = deterministic_request_id(
            request_identity(run_id, normalize_request_url(normalized_url))
        )

        client = await connect_nats()
        try:
            jetstream = client.jetstream()
            runs, requests, _workers = await ensure_graph_storage(jetstream)
            ingestion_results = await ensure_ingestion_results(jetstream)
            deadline = monotonic() + wait_seconds
            document_id: str | None = None
            while True:
                request = await get_crawl_request(requests, crawl_id)
                if request is not None:
                    document_id = request.document_id
                    ingestion = await get_ingestion_state(
                        ingestion_results, crawl_ingestion_request_id(request.id)
                    )
                    if ingestion is not None and ingestion.status == "succeeded":
                        assert ingestion.result is not None
                        return PageInspection(
                            status="catalogue_ready",
                            graph_run_id=run_id,
                            crawl_id=request.id,
                            requested_url=normalized_url,
                            document_id=ingestion.result.document_id,
                            repository_snapshot=ingestion.result.repository_snapshot,
                            message=(
                                "The page's base crawl and DOM evidence is available "
                                "in the catalogue."
                            ),
                        )
                    if ingestion is not None and ingestion.status == "failed":
                        return PageInspection(
                            status="ingestion_failed",
                            graph_run_id=run_id,
                            crawl_id=request.id,
                            requested_url=normalized_url,
                            document_id=request.document_id,
                            message=ingestion.error or "Catalogue ingestion failed.",
                        )
                    if request.status in {"failed", "cancelled"}:
                        return PageInspection(
                            status="acquisition_failed",
                            graph_run_id=run_id,
                            crawl_id=request.id,
                            requested_url=normalized_url,
                            document_id=request.document_id,
                            message=request.error or f"Page acquisition {request.status}.",
                        )

                run = await get_graph_run(runs, run_id)
                if run is None:
                    raise RuntimeError(
                        f"Page inspection graph run {run_id} disappeared from operational state."
                    )
                if monotonic() >= deadline:
                    return PageInspection(
                        status="still_running",
                        graph_run_id=run_id,
                        crawl_id=crawl_id,
                        requested_url=normalized_url,
                        document_id=document_id,
                        message=(
                            "Atlas is still acquiring or ingesting this page. The durable "
                            "run continues in the background."
                        ),
                    )
                await asyncio.sleep(0.5)
        finally:
            await client.drain()

    async def list_graphs(self) -> list[CrawlGraphSummary]:
        """Return compact live graph summaries from Postgres."""

        return await self.catalogue_control.run(
            lambda session, _catalogue: [
                CrawlGraphSummary(
                    id=value.id,
                    slug=value.slug,
                    description=value.description,
                    system_owned=value.system_owned,
                    node_count=len(value.nodes),
                    edge_count=len(value.edges),
                )
                for value in (
                    detail(get_graph(session, record.id))
                    for record in list_graph_records(session)
                )
            ]
        )

    async def get_graph(self, graph_id: UUID) -> CrawlGraphDetail:
        """Return one live graph with its nodes and scoped SQL edges."""

        return await self.catalogue_control.run(
            lambda session, _catalogue: detail(get_graph(session, graph_id))
        )

    async def list_schedules(self) -> list[CrawlScheduleSummary]:
        """Return compact live schedule summaries from Postgres."""

        return await self.catalogue_control.run(
            lambda session, _catalogue: [
                CrawlScheduleSummary(
                    id=value.id,
                    graph_id=value.graph_id,
                    graph_slug=value.graph_slug,
                    name=value.name,
                    status=value.status,
                    enabled=value.enabled,
                    root_count=len(value.root_urls),
                    max_crawls=value.max_crawls,
                    next_run_at=(value.next_run_at.isoformat() if value.next_run_at else None),
                    last_run_id=value.last_run_id,
                    last_error=value.last_error,
                )
                for value in list_schedule_resources(session)
            ]
        )

    async def get_schedule(self, schedule_id: UUID) -> CrawlScheduleResource:
        """Return one schedule with its cadence, roots, graph, and latest state."""

        return await self.catalogue_control.run(
            lambda session, _catalogue: get_schedule_resource(session, schedule_id)
        )

    async def inspect_graph_run(self, run_id: UUID) -> GraphRunInspection:
        """Read authoritative current graph-run state from NATS."""

        client = await connect_nats()
        try:
            runs, _requests, _workers = await ensure_graph_storage(
                client.jetstream()
            )
            run = await get_graph_run(runs, run_id)
        finally:
            await client.drain()
        if run is None:
            raise ValueError(f"Graph run {run_id} was not found or has expired.")
        return GraphRunInspection(
            id=run.id,
            graph_id=run.graph_id,
            status=run.status,
            trigger_kind=run.trigger_kind,
            trigger_schedule_id=run.trigger_schedule_id,
            trigger_urls=list(run.trigger_urls),
            max_crawls=run.max_crawls,
            crawl_limit_reached=run.crawl_limit_reached,
            request_count=run.request_count,
            pending_request_count=run.pending_request_count,
            acquisition_pending_count=run.acquisition_pending_count,
            failed_request_count=run.failed_request_count,
            error_count=run.error_count,
            failure_groups=[group.model_dump(mode="json") for group in run.failure_groups],
            created_at=run.created_at.isoformat(),
            started_at=run.started_at.isoformat() if run.started_at else None,
            last_progress_at=(run.last_progress_at.isoformat() if run.last_progress_at else None),
            completed_at=run.completed_at.isoformat() if run.completed_at else None,
            error=run.error,
        )

    async def search_web(
        self,
        query: str,
        *,
        count: int = 10,
        country: BraveCountry = "US",
        search_lang: BraveSearchLanguage = "en",
        ui_lang: BraveUiLanguage = "en-US",
        safesearch: Literal["off", "moderate", "strict"] = "moderate",
        freshness: Literal["any", "day", "week", "month", "year"] = "any",
    ) -> SeedSearchResponse:
        """Search the public web for candidate crawl seeds without retaining results."""

        normalized = " ".join(query.split())
        if not normalized:
            raise BraveSearchParameterError("Web search query must not be blank.")
        if len(normalized) > 400 or len(normalized.split()) > 50:
            raise BraveSearchParameterError(
                "Web search query must contain at most 400 characters and 50 words."
            )
        if not 1 <= count <= 10:
            raise BraveSearchParameterError(
                "Web search count must be between 1 and 10."
            )
        normalized_country = country.strip().upper()
        if normalized_country not in _BRAVE_COUNTRIES:
            raise BraveSearchParameterError(
                f"Brave Search does not support country {normalized_country!r}."
            )
        normalized_search_lang = search_lang.strip().lower()
        normalized_search_lang = _BRAVE_SEARCH_LANGUAGE_ALIASES.get(
            normalized_search_lang, normalized_search_lang
        )
        if normalized_search_lang not in _BRAVE_SEARCH_LANGUAGES:
            raise BraveSearchParameterError(
                f"Brave Search does not support search language {search_lang!r}."
            )
        normalized_ui_lang = _canonical_ui_language(ui_lang)
        if normalized_ui_lang not in _BRAVE_UI_LANGUAGES:
            raise BraveSearchParameterError(
                f"Brave Search does not support UI language {ui_lang!r}."
            )
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
        max_crawls: int,
        recommended_run_type: Literal["one_off", "scheduled"],
        schedule_summary: str | None = None,
        *,
        name: str = "Acquisition",
        purpose: str = "Retain relevant public web evidence.",
        mode: Literal["reconnaissance", "corpus", "monitoring"] = "corpus",
        expected_coverage: str = "",
        success_criteria: str = "",
    ) -> AcquisitionPlan:
        """Validate a model-selected graph and its derived public start URLs."""

        graph = await self.catalogue_control.run(
            lambda session, _catalogue: detail(get_graph(session, graph_id))
        )
        if graph.root_node_id is None:
            raise ValueError(f"Crawl graph {graph.slug!r} has no root node.")

        normalized_urls: list[str] = []
        for url in start_urls:
            normalized = _normalize_public_url(url)
            if normalized not in normalized_urls:
                normalized_urls.append(normalized)
        if not normalized_urls:
            raise ValueError("An acquisition plan requires at least one start URL.")
        if max_crawls < len(normalized_urls):
            raise ValueError(
                "The maximum crawl budget cannot be smaller than the number of start URLs."
            )

        return AcquisitionPlan(
            graph_id=graph.id,
            graph_slug=graph.slug,
            name=name,
            purpose=purpose,
            mode=mode,
            start_urls=normalized_urls,
            max_crawls=max_crawls,
            recommended_run_type=recommended_run_type,
            schedule_summary=(
                schedule_summary.strip() if schedule_summary else None
            ),
            expected_coverage=expected_coverage,
            success_criteria=success_criteria,
        )

    async def prepare_plans(
        self, plans: list[AcquisitionPlanInput]
    ) -> list[AcquisitionPlan]:
        """Validate several independently approvable acquisitions."""

        if not 1 <= len(plans) <= 10:
            raise ValueError("Propose between one and ten acquisition plans.")
        return [
            await self.prepare_plan(
                graph_id=plan.graph_id,
                start_urls=plan.start_urls,
                max_crawls=plan.max_crawls,
                recommended_run_type=plan.recommended_run_type,
                schedule_summary=plan.schedule_summary,
                name=plan.name,
                purpose=plan.purpose,
                mode=plan.mode,
                expected_coverage=plan.expected_coverage,
                success_criteria=plan.success_criteria,
            )
            for plan in plans
        ]

    async def prepare_schedule_change(
        self,
        action: Literal["update", "pause", "resume", "delete"],
        schedule_id: UUID,
        reason: str,
        replacement: CrawlScheduleUpdate | None = None,
    ) -> ScheduleChangeProposal:
        schedule = await self.get_schedule(schedule_id)
        if action == "update" and replacement is None:
            raise ValueError("Updating a schedule requires its complete replacement definition.")
        if action != "update" and replacement is not None:
            raise ValueError("Only an update may include a replacement definition.")
        return ScheduleChangeProposal(
            action=action,
            schedule_id=schedule.id,
            graph_id=schedule.graph_id,
            schedule_name=schedule.name,
            reason=reason,
            replacement=replacement,
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
        try:
            response = await client.get(
                BRAVE_WEB_SEARCH_URL,
                params=params,
                headers={
                    "Accept": "application/json",
                    "X-Subscription-Token": self._brave_api_key,
                },
            )
        except httpx.HTTPError as exc:
            raise BraveSearchError(
                "Brave Search could not be reached. Revise or retry the search."
            ) from exc
        if response.is_error:
            raise _brave_response_error(response)
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


def _normalize_public_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"URL is not public HTTP(S): {url!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Public URLs must not contain credentials.")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _canonical_ui_language(value: str) -> str:
    parts = value.strip().replace("_", "-").split("-")
    if len(parts) != 2:
        return value.strip()
    return f"{parts[0].lower()}-{parts[1].upper()}"


def _brave_response_error(response: httpx.Response) -> BraveSearchError:
    status = response.status_code
    if status == 422:
        fields = _brave_validation_fields(response)
        suffix = f" ({', '.join(fields)})" if fields else ""
        return BraveSearchParameterError(
            f"Brave Search rejected the request parameters{suffix}."
        )
    if status == 429:
        return BraveSearchError(
            "Brave Search is temporarily rate limited. Revise or retry the search."
        )
    if status in {401, 403}:
        return BraveSearchError(
            "Brave Search authentication was rejected by the provider."
        )
    if status >= 500:
        return BraveSearchError(
            "Brave Search is temporarily unavailable. Revise or retry the search."
        )
    return BraveSearchError(f"Brave Search failed with HTTP status {status}.")


def _brave_validation_fields(response: httpx.Response) -> list[str]:
    """Extract only rejected field names, never provider payloads or identifiers."""

    try:
        payload: Any = response.json()
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    error = payload.get("error")
    if not isinstance(error, dict):
        return []
    metadata = error.get("meta")
    if not isinstance(metadata, dict):
        return []
    errors = metadata.get("errors")
    if not isinstance(errors, list):
        return []
    fields: list[str] = []
    for item in errors:
        if not isinstance(item, dict):
            continue
        location = item.get("loc")
        if not isinstance(location, list) or not location:
            continue
        field = location[-1]
        if isinstance(field, str) and field not in fields:
            fields.append(field)
    return fields
