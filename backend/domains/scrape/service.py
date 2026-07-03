import asyncio
import json
import mimetypes
import time
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.models import CrawlResult, MarkdownGenerationResult

from domains.progress import CrawlProgressCallback, CrawlProgressEvent, emit_crawl_progress

from .cache import cache_root
from .models import ArtifactFormat, OutputFormat, ScrapeArtifact, ScrapeOutput, ScrapePage, ScrapeStats

_MANIFEST_FILE = "manifest.json"
_USER_AGENT = "Atlas Scraper/0.1"


class _ImageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_by_name = {name.lower(): value for name, value in attrs if value}
        if tag.lower() == "img" and attrs_by_name.get("src"):
            self.urls.append(attrs_by_name["src"])

        if tag.lower() in {"img", "source"} and attrs_by_name.get("srcset"):
            self.urls.extend(_urls_from_srcset(attrs_by_name["srcset"]))


def _urls_from_srcset(srcset: str) -> list[str]:
    return [candidate.strip().split()[0] for candidate in srcset.split(",") if candidate.strip()]


def _normalize_url(url: str, base_url: str) -> str | None:
    absolute_url = urljoin(base_url, url)
    clean_url, _ = urldefrag(absolute_url)
    return clean_url if urlparse(clean_url).scheme in {"http", "https"} else None


def _request_hash(url: str, download_images: bool, output_formats: list[OutputFormat]) -> str:
    payload = {
        "url": url,
        "download_images": download_images,
        "output_formats": sorted(output_formats),
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def _url_slug(url: str) -> str:
    parsed_url = urlparse(url)
    slug = f"{parsed_url.netloc}{parsed_url.path}".strip("/") or "page"
    return "".join(char if char.isalnum() else "-" for char in slug).strip("-")[:64] or "page"


def _cache_dir_for_url(
    url: str,
    download_images: bool,
    output_formats: list[OutputFormat],
) -> Path:
    return cache_root() / f"{_url_slug(url)}-{_request_hash(url, download_images, output_formats)}"


def _manifest_path(cache_dir: Path) -> Path:
    return cache_dir / _MANIFEST_FILE


def _write_text_artifact(path: Path, value: str, artifact_format: ArtifactFormat) -> ScrapeArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return ScrapeArtifact(format=artifact_format, path=str(path), bytes=path.stat().st_size)


def _write_bytes_artifact(path: Path, value: bytes, artifact_format: ArtifactFormat) -> ScrapeArtifact:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return ScrapeArtifact(format=artifact_format, path=str(path), bytes=path.stat().st_size)


def _artifact_from_cached_file(artifact: dict) -> ScrapeArtifact | None:
    path = Path(str(artifact.get("path", "")))
    if not path.is_file():
        return None

    bytes_written = path.stat().st_size
    if bytes_written < 1:
        return None

    return ScrapeArtifact(
        format=artifact["format"],
        path=str(path),
        source_url=artifact.get("source_url"),
        bytes=bytes_written,
    )


def _cached_page(
    url: str,
    cache_dir: Path,
    output_formats: list[OutputFormat],
    download_images: bool,
) -> ScrapePage | None:
    manifest_path = _manifest_path(cache_dir)
    if not manifest_path.is_file():
        return None

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    request = manifest.get("request", {})
    if request != {
        "url": url,
        "download_images": download_images,
        "output_formats": sorted(output_formats),
    }:
        return None

    artifacts = []
    for artifact in manifest.get("artifacts", []):
        cached_artifact = _artifact_from_cached_file(artifact)
        if cached_artifact is None:
            return None

        artifacts.append(cached_artifact)

    cached_formats = {artifact.format for artifact in artifacts}
    if not set(output_formats).issubset(cached_formats):
        return None

    return ScrapePage(
        url=manifest.get("url", url),
        success=True,
        cached=True,
        status_code=manifest.get("status_code"),
        duration_seconds=0.0,
        cache_dir=str(cache_dir),
        artifacts=artifacts,
    )


def _write_manifest(
    cache_dir: Path,
    url: str,
    output_formats: list[OutputFormat],
    download_images: bool,
    page: ScrapePage,
) -> None:
    manifest = {
        "request": {
            "url": url,
            "download_images": download_images,
            "output_formats": sorted(output_formats),
        },
        "url": page.url,
        "status_code": page.status_code,
        "artifacts": [artifact.model_dump() for artifact in page.artifacts],
    }
    manifest_path = _manifest_path(cache_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _markdown_text(markdown: str | MarkdownGenerationResult | None) -> str:
    if markdown is None:
        return ""

    if isinstance(markdown, str):
        return markdown

    return markdown.raw_markdown


def _image_urls_from_result(result: CrawlResult, page_url: str) -> list[str]:
    image_urls: list[str] = []
    for image in result.media.get("images", []):
        raw_url = image.get("src") or image.get("url") or image.get("href")
        if raw_url:
            image_urls.append(raw_url)

    parser = _ImageParser()
    parser.feed(result.html or "")
    image_urls.extend(parser.urls)

    normalized_urls: list[str] = []
    seen_urls: set[str] = set()
    for image_url in image_urls:
        normalized_url = _normalize_url(image_url, page_url)
        if normalized_url and normalized_url not in seen_urls:
            seen_urls.add(normalized_url)
            normalized_urls.append(normalized_url)

    return normalized_urls


def _image_extension(image_url: str, content_type: str | None) -> str:
    if content_type:
        extension = mimetypes.guess_extension(content_type.split(";")[0].strip())
        if extension:
            return extension

    extension = Path(urlparse(image_url).path).suffix
    return extension if extension else ".img"


def _download_image(image_url: str, image_dir: Path) -> ScrapeArtifact:
    request = Request(image_url, headers={"User-Agent": _USER_AGENT})
    with urlopen(request, timeout=30) as response:
        data = response.read()
        extension = _image_extension(image_url, response.headers.get("Content-Type"))

    image_path = image_dir / f"{sha256(image_url.encode()).hexdigest()[:16]}{extension}"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(data)
    return ScrapeArtifact(
        format="image",
        path=str(image_path),
        source_url=image_url,
        bytes=image_path.stat().st_size,
    )


async def _download_images(result: CrawlResult, page_url: str, cache_dir: Path) -> list[ScrapeArtifact]:
    artifacts: list[ScrapeArtifact] = []
    for image_url in _image_urls_from_result(result, page_url):
        try:
            artifact = await asyncio.to_thread(_download_image, image_url, cache_dir / "images")
        except Exception:
            continue

        artifacts.append(artifact)

    return artifacts


def _dedupe_output_formats(output_formats: list[OutputFormat]) -> list[OutputFormat]:
    deduped: list[OutputFormat] = []
    for output_format in output_formats:
        if output_format not in deduped:
            deduped.append(output_format)

    return deduped


async def _write_artifacts(
    result: CrawlResult,
    cache_dir: Path,
    output_formats: list[OutputFormat],
    download_images: bool,
) -> list[ScrapeArtifact]:
    artifacts: list[ScrapeArtifact] = []

    if "html" in output_formats:
        artifacts.append(_write_text_artifact(cache_dir / "page.html", result.html or "", "html"))

    if "markdown" in output_formats:
        artifacts.append(
            _write_text_artifact(cache_dir / "page.md", _markdown_text(result.markdown), "markdown")
        )

    if "pdf" in output_formats and result.pdf:
        artifacts.append(_write_bytes_artifact(cache_dir / "page.pdf", result.pdf, "pdf"))

    if download_images:
        artifacts.extend(await _download_images(result, result.url, cache_dir))

    return artifacts


async def _scrape_url(
    crawler: AsyncWebCrawler,
    url: str,
    output_formats: list[OutputFormat],
    download_images: bool,
    progress_callback: CrawlProgressCallback | None,
) -> ScrapePage:
    cache_dir = _cache_dir_for_url(url, download_images, output_formats)
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(url=url, label="scrape", status="started"),
    )
    start_time = time.perf_counter()

    try:
        result = await crawler.arun(
            url=url,
            config=CrawlerRunConfig(
                cache_mode=CacheMode.BYPASS,
                magic=True,
                verbose=False,
                pdf="pdf" in output_formats,
                wait_for_images=download_images,
            ),
        )
        artifacts = await _write_artifacts(
            result=result,
            cache_dir=cache_dir,
            output_formats=output_formats,
            download_images=download_images,
        )
    except Exception as exc:
        duration = time.perf_counter() - start_time
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=url, label="scrape", status="failed", duration=duration, error=str(exc)),
        )
        return ScrapePage(
            url=url,
            success=False,
            duration_seconds=duration,
            cache_dir=str(cache_dir),
            artifacts=[],
            error=str(exc),
        )

    duration = time.perf_counter() - start_time
    await emit_crawl_progress(
        progress_callback,
        CrawlProgressEvent(
            url=url,
            label="scrape",
            status="succeeded" if result.success else "failed",
            duration=duration,
            error=result.error_message,
        ),
    )

    return ScrapePage(
        url=result.url,
        success=result.success,
        cached=False,
        status_code=result.status_code,
        duration_seconds=duration,
        cache_dir=str(cache_dir),
        artifacts=artifacts,
        error=result.error_message,
    )


async def scrape(
    urls: list[str],
    download_images: bool = False,
    output_formats: list[OutputFormat] | None = None,
    progress_callback: CrawlProgressCallback | None = None,
) -> ScrapeOutput:
    output_formats = _dedupe_output_formats(output_formats or ["html"])
    start_time = time.perf_counter()
    pages_by_index: dict[int, ScrapePage] = {}
    cache_misses: list[tuple[int, str]] = []

    for index, url in enumerate(urls):
        cache_dir = _cache_dir_for_url(url, download_images, output_formats)
        cached_page = _cached_page(
            url=url,
            cache_dir=cache_dir,
            output_formats=output_formats,
            download_images=download_images,
        )
        if cached_page is None:
            cache_misses.append((index, url))
            continue

        pages_by_index[index] = cached_page
        await emit_crawl_progress(
            progress_callback,
            CrawlProgressEvent(url=url, label="cache", status="succeeded", duration=0.0),
        )

    if cache_misses:
        async with AsyncWebCrawler(
            config=BrowserConfig(headless=True, enable_stealth=True, verbose=False),
        ) as crawler:
            for index, url in cache_misses:
                page = await _scrape_url(
                    crawler=crawler,
                    url=url,
                    output_formats=output_formats,
                    download_images=download_images,
                    progress_callback=progress_callback,
                )
                pages_by_index[index] = page
                if page.success:
                    _write_manifest(
                        cache_dir=Path(page.cache_dir),
                        url=url,
                        output_formats=output_formats,
                        download_images=download_images,
                        page=page,
                    )

    pages = [pages_by_index[index] for index in range(len(urls))]
    artifacts = [artifact for page in pages for artifact in page.artifacts]
    return ScrapeOutput(
        cache_root=str(cache_root()),
        stats=ScrapeStats(
            requested_urls=len(urls),
            succeeded=sum(1 for page in pages if page.success),
            failed=sum(1 for page in pages if not page.success),
            cache_hits=sum(1 for page in pages if page.cached),
            artifacts=len(artifacts),
            images_downloaded=sum(1 for artifact in artifacts if artifact.format == "image"),
            bytes_written=sum(artifact.bytes for artifact in artifacts),
            duration_seconds=time.perf_counter() - start_time,
        ),
        pages=pages,
    )


def scrape_sync(
    urls: list[str],
    download_images: bool = False,
    output_formats: list[OutputFormat] | None = None,
    progress_callback: CrawlProgressCallback | None = None,
) -> ScrapeOutput:
    return asyncio.run(
        scrape(
            urls=urls,
            download_images=download_images,
            output_formats=output_formats,
            progress_callback=progress_callback,
        )
    )
