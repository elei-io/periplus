import asyncio
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from actions.extract.schemas import ExtractOutput
from actions.extract.service import extract as extract_service
from actions.shared.progress import CrawlProgressCallback
from actions.shared.query_schema.schemas import QueryParamOutput
from actions.shared.search_url import build_search_url, default_search_match

from .schemas import SearchProvider, SearchResult

_SCHEMA_TARGET_JSON_EXAMPLE = json.dumps(
    {
        "title": "Cursor Docs - Agent, Rules, MCP, Skills & CLI",
        "url": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fcursor.com%2Fdocs",
        "description": "Official Cursor documentation.",
    }
)
_MAX_PAGES = 25


@dataclass(frozen=True)
class SearchProviderConfig:
    value: SearchProvider
    label: str
    base_url: str
    search_param_name: str
    extra_params: dict[str, str] | None = None
    paginates: bool = False

    @property
    def match(self) -> str:
        return default_search_match(self.base_url, self.search_param_name)

    @property
    def prompt(self) -> str:
        return (
            f"Extract {self.label} web search results. Return one object per result with: "
            "title as visible result title text, url as the result link href, and description "
            "as visible snippet text. "
            "Use stable CSS selectors and avoid navigation, filters, ads, knowledge panels, or icons."
        )


SEARCH_PROVIDERS: dict[SearchProvider, SearchProviderConfig] = {
    "duckduckgo": SearchProviderConfig(
        value="duckduckgo",
        label="DuckDuckGo HTML",
        base_url="https://html.duckduckgo.com/html/",
        search_param_name="q",
        paginates=True,
    ),
    "brave": SearchProviderConfig(
        value="brave",
        label="Brave Search",
        base_url="https://search.brave.com/search",
        search_param_name="q",
        paginates=True,
    ),
    "yahoo": SearchProviderConfig(
        value="yahoo",
        label="Yahoo Search",
        base_url="https://search.yahoo.com/search",
        search_param_name="p",
        paginates=True,
    ),
}


def _normalize_url(href: str) -> str:
    if href.startswith("//"):
        href = f"https:{href}"

    parsed = urlparse(href)
    if not parsed.netloc and "." in parsed.path and " " not in parsed.path:
        href = f"https://{href}"
        parsed = urlparse(href)

    if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
        target = parse_qs(parsed.query).get("uddg")
        if target:
            return unquote(target[0])

    if "google." in parsed.netloc and parsed.path == "/url":
        query = parse_qs(parsed.query).get("q")
        if query:
            return unquote(query[0])

    return href


def _is_result_link(href: str, provider: SearchProviderConfig) -> bool:
    if not href or href.startswith(("javascript:", "#")):
        return False

    domain = urlparse(href).netloc.lower()
    if not domain:
        return False

    provider_domain = urlparse(provider.base_url).netloc.lower()
    if domain == provider_domain or domain.endswith(f".{provider_domain}"):
        return False

    return True


def _is_provider_page(url: str, provider: SearchProviderConfig) -> bool:
    provider_domain = urlparse(provider.base_url).netloc.lower()
    domain = urlparse(url).netloc.lower()
    return domain == provider_domain


def _parse_search_results(
    extracted_content: str | list | None,
    provider: SearchProviderConfig,
) -> list[SearchResult]:
    if not extracted_content:
        return []

    extracted_results = json.loads(extracted_content) if isinstance(extracted_content, str) else extracted_content
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    for item in extracted_results:
        if not isinstance(item, dict):
            continue

        url = _normalize_url(item.get("url", ""))
        title = item.get("title", "")
        if not _is_result_link(url, provider) or url in seen_urls or not title:
            continue

        seen_urls.add(url)
        results.append(
            SearchResult(
                url=url,
                title=title,
                description=item.get("description", ""),
            )
        )

    return results


def _candidate_url_for_param_value(
    query_params: QueryParamOutput,
    *,
    key: str,
    value: str,
    provider: SearchProviderConfig,
) -> str | None:
    for candidate in query_params.candidates:
        if not _is_provider_page(candidate.url, provider):
            continue
        query = parse_qs(urlparse(candidate.url).query, keep_blank_values=True)
        if value in query.get(key, []):
            return candidate.url
    return None


def _pagination_urls(
    query_params: QueryParamOutput | None,
    *,
    provider: SearchProviderConfig,
    remaining_pages: int,
) -> list[str]:
    if query_params is None or remaining_pages <= 0:
        return []

    urls: list[str] = []
    seen: set[str] = set()
    pagination_params = [param for param in query_params.params if param.kind == "pagination"]

    indexed_values: list[tuple[int, str]] = []
    for param in pagination_params:
        if param.pagination_role != "index":
            continue
        for value in param.values:
            if not value.value.isdigit():
                continue
            candidate_url = _candidate_url_for_param_value(
                query_params,
                key=param.key,
                value=value.value,
                provider=provider,
            )
            if candidate_url and candidate_url != query_params.url:
                indexed_values.append((int(value.value), candidate_url))

    for _page_index, candidate_url in sorted(indexed_values, key=lambda item: item[0]):
        if candidate_url in seen:
            continue
        seen.add(candidate_url)
        urls.append(candidate_url)
        if len(urls) >= remaining_pages:
            return urls

    for param in pagination_params:
        if param.pagination_role != "next":
            continue
        for value in param.values:
            candidate_url = _candidate_url_for_param_value(
                query_params,
                key=param.key,
                value=value.value,
                provider=provider,
            )
            if candidate_url and candidate_url not in seen and candidate_url != query_params.url:
                return [candidate_url]

    return urls


async def _extract_search_page(
    *,
    page_url: str,
    provider_config: SearchProviderConfig,
    progress_callback: CrawlProgressCallback | None,
    session: Session | None,
    task_run_id: UUID | None,
) -> ExtractOutput:
    return await extract_service(
        url=page_url,
        extract_data=True,
        extract_query_params=True,
        prompt=provider_config.prompt,
        target_json_example=_SCHEMA_TARGET_JSON_EXAMPLE,
        schema_type="css",
        match=provider_config.match,
        progress_callback=progress_callback,
        session=session,
        task_run_id=task_run_id,
    )


async def search(
    query: str,
    max_pages: int = 1,
    provider: SearchProvider = "duckduckgo",
    progress_callback: CrawlProgressCallback | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
) -> list[SearchResult]:
    provider_config = SEARCH_PROVIDERS[provider]
    search_url = build_search_url(
        base_url=provider_config.base_url,
        search_param_name=provider_config.search_param_name,
        query=query,
        extra_params=provider_config.extra_params,
    )
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    page_queue: list[str] = [search_url]
    seen_page_urls: set[str] = set()
    processed_pages = 0
    page_limit = min(max_pages, _MAX_PAGES)
    while page_queue and processed_pages < page_limit:
        remaining_pages = page_limit - processed_pages
        batch_size = min(len(page_queue), remaining_pages)
        batch = [page_queue.pop(0) for _ in range(batch_size)]
        batch = [page_url for page_url in batch if page_url not in seen_page_urls]
        if not batch:
            break
        seen_page_urls.update(batch)

        if session is None and len(batch) > 1:
            extract_outputs = await asyncio.gather(
                *[
                    _extract_search_page(
                        page_url=page_url,
                        provider_config=provider_config,
                        progress_callback=progress_callback,
                        session=None,
                        task_run_id=None,
                    )
                    for page_url in batch
                ]
            )
        else:
            extract_outputs = []
            for page_url in batch:
                extract_outputs.append(
                    await _extract_search_page(
                        page_url=page_url,
                        provider_config=provider_config,
                        progress_callback=progress_callback,
                        session=session,
                        task_run_id=task_run_id,
                    )
                )

        for extract_output in extract_outputs:
            processed_pages += 1
            if not extract_output.success:
                continue

            previous_count = len(results)
            for item in _parse_search_results(extract_output.results, provider_config):
                if item.url in seen_urls:
                    continue

                seen_urls.add(item.url)
                results.append(item)

            if len(results) == previous_count or not provider_config.paginates:
                continue

            next_urls = _pagination_urls(
                extract_output.query_params,
                provider=provider_config,
                remaining_pages=page_limit - processed_pages - len(page_queue),
            )
            for next_url in next_urls:
                if next_url not in seen_page_urls and next_url not in page_queue:
                    page_queue.append(next_url)

    return results


def search_sync(
    query: str,
    max_pages: int = 1,
    provider: SearchProvider = "duckduckgo",
    progress_callback: CrawlProgressCallback | None = None,
) -> list[SearchResult]:
    return asyncio.run(
        search(
            query=query,
            max_pages=max_pages,
            provider=provider,
            progress_callback=progress_callback,
        )
    )
