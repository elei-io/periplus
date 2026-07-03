import asyncio
import json
import os
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlencode, unquote, urljoin, urlparse

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    JsonCssExtractionStrategy,
    JsonXPathExtractionStrategy,
    LLMConfig,
)
from dotenv import load_dotenv

from domains.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress

from .models import SearchResult

_SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"
_SCHEMA_PATH = Path(__file__).resolve().parents[2] / ".schemas" / "search.duckduckgo.json"
_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
_SCHEMA_PROMPT = (
    "Extract DuckDuckGo HTML search results. Return one object per result with: "
    "title as visible result title text, url as the result link href, and description "
    "as visible snippet text. "
    "Use stable CSS selectors and avoid navigation, filters, ads, or icons."
)
_SCHEMA_TARGET_JSON_EXAMPLE = json.dumps(
    {
        "title": "Cursor Docs - Agent, Rules, MCP, Skills & CLI",
        "url": "//duckduckgo.com/l/?uddg=https%3A%2F%2Fcursor.com%2Fdocs",
        "description": "Official Cursor documentation.",
    }
)
_BLOCKED_DOMAINS = {
    "duckduckgo.com",
    "html.duckduckgo.com",
    "google.com",
    "www.google.com",
}
_MAX_PAGES = 10


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


def _schema_llm_config() -> LLMConfig:
    load_dotenv(_ENV_PATH)
    provider = os.getenv("OPENROUTER_SEARCH_EXTRACTOR_MODEL", "openai/gpt-4o")
    if not provider.startswith("openrouter/"):
        provider = f"openrouter/{provider}"

    return LLMConfig(
        provider=provider,
        api_token=os.getenv("OPENROUTER_API_KEY"),
    )


async def _load_or_generate_schema(search_url: str) -> dict:
    if _SCHEMA_PATH.exists():
        return json.loads(_SCHEMA_PATH.read_text())

    schema = await JsonCssExtractionStrategy.agenerate_schema(
        url=search_url,
        schema_type="css",
        query=_SCHEMA_PROMPT,
        target_json_example=_SCHEMA_TARGET_JSON_EXAMPLE,
        llm_config=_schema_llm_config(),
        validate=True,
    )

    _SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SCHEMA_PATH.write_text(f"{json.dumps(schema, indent=2)}\n")
    return schema


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


def _is_xpath_schema(schema: dict) -> bool:
    selectors = [schema.get("baseSelector", "")]
    selectors.extend(field.get("selector", "") for field in schema.get("fields", []))
    return any(selector.startswith(("/", "./", ".//")) for selector in selectors)


def _next_page_url(html: str | None) -> str | None:
    if not html:
        return None

    parser = _DuckDuckGoNextFormParser()
    parser.feed(html)
    if not parser.next_fields:
        return None

    action = urljoin("https://html.duckduckgo.com", parser.next_action or "/html/")
    return f"{action}?{urlencode(parser.next_fields)}"


def _is_result_link(href: str) -> bool:
    if not href or href.startswith(("javascript:", "#")):
        return False

    domain = urlparse(href).netloc.lower()
    if not domain:
        return False

    if domain in _BLOCKED_DOMAINS or domain.endswith(".duckduckgo.com"):
        return False

    return "google." not in domain


def _parse_search_results(extracted_content: str | None) -> list[SearchResult]:
    if not extracted_content:
        return []

    extracted_results = json.loads(extracted_content)
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    for item in extracted_results:
        url = _normalize_url(item.get("url", ""))
        title = item.get("title", "")
        if not _is_result_link(url) or url in seen_urls or not title:
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


async def search(
    query: str,
    max_results: int = 10,
    progress_callback: CrawlProgressCallback | None = None,
) -> list[SearchResult]:
    search_url = _SEARCH_URL.format(query=quote_plus(query))
    schema = await _load_or_generate_schema(search_url)
    strategy_class = JsonXPathExtractionStrategy if _is_xpath_schema(schema) else JsonCssExtractionStrategy
    extraction_strategy = strategy_class(schema)
    results: list[SearchResult] = []
    seen_urls: set[str] = set()

    async with AsyncWebCrawler(
        config=BrowserConfig(headless=True, enable_stealth=True, verbose=False),
    ) as crawler:
        page_url: str | None = search_url
        for page_number in range(1, _MAX_PAGES + 1):
            if not page_url or len(results) >= max_results:
                break

            await emit_crawl_progress(
                progress_callback,
                CrawlProgressEvent(
                    url=page_url,
                    label=f"search page {page_number}",
                    status="started",
                ),
            )
            start_time = time.perf_counter()
            result = await crawler.arun(
                url=page_url,
                config=CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    extraction_strategy=extraction_strategy,
                    magic=True,
                    verbose=False,
                ),
            )
            duration = time.perf_counter() - start_time
            await emit_crawl_progress(
                progress_callback,
                CrawlProgressEvent(
                    url=page_url,
                    label=f"search page {page_number}",
                    status="succeeded" if result.success else "failed",
                    duration=duration,
                    error=result.error_message,
                ),
            )

            if not result.success:
                break

            previous_count = len(results)
            for item in _parse_search_results(result.extracted_content):
                if item.url in seen_urls:
                    continue

                seen_urls.add(item.url)
                results.append(item)
                if len(results) >= max_results:
                    break

            if len(results) == previous_count:
                break

            page_url = _next_page_url(result.html)

    return results


def search_sync(
    query: str,
    max_results: int = 10,
    progress_callback: CrawlProgressCallback | None = None,
) -> list[SearchResult]:
    return asyncio.run(
        search(
            query=query,
            max_results=max_results,
            progress_callback=progress_callback,
        )
    )
