import asyncio
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlencode, unquote, urljoin, urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from actions.extract.service import extract as extract_service
from actions.crawl.service import crawl as crawl_service
from actions.shared.progress import CrawlProgressCallback
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
    crawl_config: dict[str, object] | None = None
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
        crawl_config={"mode": "static", "wait": "none", "concurrency": 1},
        paginates=True,
    ),
    "brave": SearchProviderConfig(
        value="brave",
        label="Brave Search",
        base_url="https://search.brave.com/search",
        search_param_name="q",
        crawl_config={"mode": "app", "wait": "stable", "concurrency": 1},
        paginates=True,
    ),
    "yahoo": SearchProviderConfig(
        value="yahoo",
        label="Yahoo Search",
        base_url="https://search.yahoo.com/search",
        search_param_name="p",
        crawl_config={"mode": "static", "wait": "none", "concurrency": 1},
        paginates=True,
    ),
}


class _DuckDuckGoNextFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_form = False
        self._current_form: dict[str, str] = {}
        self._current_action = ""
        self._current_is_next_form = False
        self.next_action = ""
        self.next_fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)

        if tag == "form":
            self._in_form = True
            self._current_form = {}
            self._current_action = attrs_dict.get("action") or ""
            self._current_is_next_form = False
            return

        if tag != "input" or not self._in_form:
            return

        value = attrs_dict.get("value") or ""
        name = attrs_dict.get("name")
        if value == "Next":
            self._current_is_next_form = True
        elif name:
            self._current_form[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            if self._current_is_next_form:
                self.next_action = self._current_action
                self.next_fields = dict(self._current_form)

            self._in_form = False
            self._current_form = {}
            self._current_action = ""
            self._current_is_next_form = False


class _NextLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._current_href = ""
        self._current_score = 0
        self._current_text: list[str] = []
        self.next_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return

        attrs_dict = dict(attrs)
        self._current_href = attrs_dict.get("href") or ""
        self._current_text = []
        values = " ".join(
            attrs_dict.get(name) or ""
            for name in ("aria-label", "class", "id", "rel", "title")
        ).lower()
        self._current_score = 1 if "next" in values else 0

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return

        text = " ".join(self._current_text).strip().lower()
        if self._current_score or text in {"next", "next >"} or "next page" in text:
            self.next_href = self._current_href

        self._current_href = ""
        self._current_score = 0
        self._current_text = []


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


def _next_page_url(
    html: str | None,
    current_url: str,
    provider: SearchProviderConfig,
) -> str | None:
    if not html:
        return None

    duckduckgo_parser = _DuckDuckGoNextFormParser()
    duckduckgo_parser.feed(html)
    if duckduckgo_parser.next_fields:
        action = urljoin("https://html.duckduckgo.com", duckduckgo_parser.next_action or "/html/")
        return f"{action}?{urlencode(duckduckgo_parser.next_fields)}"

    link_parser = _NextLinkParser()
    link_parser.feed(html)
    if not link_parser.next_href:
        return None

    next_url = urljoin(current_url, link_parser.next_href)
    provider_domain = urlparse(provider.base_url).netloc.lower()
    next_domain = urlparse(next_url).netloc.lower()
    if next_domain != provider_domain:
        return None

    return next_url


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


def _crawl_config(provider: SearchProviderConfig) -> dict[str, object]:
    config = provider.crawl_config or {}
    return {
        "mode": config.get("mode", "static"),
        "wait": config.get("wait", "none"),
        "concurrency": max(1, int(config.get("concurrency", 1))),
    }


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
    crawl_config = _crawl_config(provider_config)

    page_url: str | None = search_url
    for _page_number in range(1, min(max_pages, _MAX_PAGES) + 1):
        if not page_url:
            break

        extract_output = await extract_service(
            url=page_url,
            prompt=provider_config.prompt,
            target_json_example=_SCHEMA_TARGET_JSON_EXAMPLE,
            schema_type="css",
            mode=crawl_config["mode"],  # type: ignore[arg-type]
            wait=crawl_config["wait"],  # type: ignore[arg-type]
            match=provider_config.match,
            progress_callback=progress_callback,
            session=session,
            task_run_id=task_run_id,
        )
        if not extract_output.success:
            break

        previous_count = len(results)
        for item in _parse_search_results(extract_output.results, provider_config):
            if item.url in seen_urls:
                continue

            seen_urls.add(item.url)
            results.append(item)

        if len(results) == previous_count:
            break

        if not provider_config.paginates:
            page_url = None
            continue

        crawl_output = await crawl_service(
            urls=[extract_output.url],
            mode=crawl_config["mode"],  # type: ignore[arg-type]
            wait=crawl_config["wait"],  # type: ignore[arg-type]
            concurrency=crawl_config["concurrency"],  # type: ignore[arg-type]
            progress_callback=progress_callback,
            session=session,
            task_run_id=task_run_id,
        )
        page = crawl_output.pages[0] if crawl_output.pages else None
        page_url = _next_page_url(page.html, page.url, provider_config) if page and page.html else None

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
