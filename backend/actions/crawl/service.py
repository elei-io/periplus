import asyncio
from email.utils import parsedate_to_datetime
import hashlib
import json
import tempfile
import time
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import UUID
import httpx
from sqlalchemy.orm import Session

from actions.shared.crawl import (
    browser_config_for_mode,
    crawl_single_url,
    run_config_for_mode,
)
from actions.shared.cache import CacheOptions, ResolvedCachePolicy, resolve_cache_policy
from actions.shared.progress import ProgressReporter, ProgressEvent, emit_progress
from repository.catalogue import CrawlRecord
from dom import links_from_html
from config import get_int, get_optional
from control.crawl_policies.schemas import (
    BrowserProfileConfig,
    CrawlPolicySnapshot,
    FirecrawlProfileConfig,
    HttpProfileConfig,
    ProfileConfig,
)
from crawl4ai.utils import get_base_domain
from runtime.resource_governor import (
    DURABLE_RESOURCE_WAIT,
    ResourceCapacityUnavailable,
    ResourcePermitLost,
    object_request,
    remote_request,
    resource_permits,
)
from observability import crawl_metrics
from repository import (
    RepositoryCacheHit,
    RepositoryPipeline,
    identify_html,
    repository_ingestor_from_env,
)
from runtime.context import (
    GraphExecutionContext,
    commit_checkpoint,
    current_graph_execution,
    graph_execution_scope,
)
from control.urls import normalize_url

from .schemas import CrawlPage
from .schemas import CapturedArtifact
from repository.objects.artifact import ArtifactIdentity


_ERROR_RESPONSE_PREVIEW_BYTES = 512
_MAX_RETRY_AFTER_SECONDS = 300.0
_RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class RetryableAcquisitionError(RuntimeError):
    """Signal durable redelivery without committing a terminal crawl observation."""

    def __init__(self, page: CrawlPage) -> None:
        super().__init__(page.error or "Retryable page acquisition failure.")
        self.page = page
        self.retry_after_seconds = page.retry_after_seconds

if TYPE_CHECKING:
    from crawl4ai import AsyncWebCrawler
    from crawl4ai.models import CrawlResult
else:
    AsyncWebCrawler = CrawlResult = Any


_browser_capacity: tuple[int, asyncio.Semaphore] | None = None


@asynccontextmanager
async def _browser_slot():
    global _browser_capacity
    capacity = get_int("ATLAS_BROWSER_CONCURRENCY")
    if _browser_capacity is None or _browser_capacity[0] != capacity:
        _browser_capacity = (capacity, asyncio.Semaphore(capacity))
    semaphore = _browser_capacity[1]
    await semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _frozen_config(
    config: ProfileConfig,
    cache_policy: ResolvedCachePolicy,
) -> dict[str, Any]:
    profile_values = config.model_dump(mode="json")
    profile_values["cache"] = cache_policy.model_dump(mode="json")
    return profile_values


def _config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def _remote_domain(url: str) -> str:
    parsed = urlparse(normalize_url(url))
    host = (parsed.hostname or "").lower()
    try:
        ip_address(host)
    except ValueError:
        return get_base_domain(url) or host
    return host


def _exception_failure(exc: Exception, profile: str) -> tuple[str, str, bool]:
    detail = str(exc).lower()
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout", "request", True
    if "exceeds atlas html limit" in detail or "exceeds atlas artifact limit" in detail:
        return "response_too_large", "capture", False
    if profile == "browser":
        return "browser_navigation", "navigation", True
    if profile == "firecrawl":
        return "provider_failure", "provider", True
    return "acquisition_exception", "request", True


def _crawl_payload(result: CrawlResult) -> dict[str, Any]:
    """Keep only acquisition state; durable structure lives in the repository."""

    return _json_safe(
        {
            "url": result.url,
            "success": result.success,
            "status_code": result.status_code,
            "redirected_url": result.redirected_url,
            "redirected_status_code": result.redirected_status_code,
            "error_message": result.error_message,
            "cached_at": result.cached_at,
            "cache_status": result.cache_status,
        }
    )

async def _crawl_url(
    crawler: AsyncWebCrawler | None,
    http_client: httpx.AsyncClient,
    url: str,
    profile: str,
    config: ProfileConfig,
    progress_reporter: ProgressReporter | None,
) -> CrawlPage:
    await emit_progress(
        progress_reporter,
        ProgressEvent(resource=url, phase="crawl", status="started", message="Loading page."),
    )
    start_time = time.perf_counter()

    try:
        if profile == "http":
            http_config = HttpProfileConfig.model_validate(config.model_dump())
            async with asyncio.timeout(http_config.timeout_seconds):
                page = await _acquire_http(http_client, url, http_config)
        elif profile == "browser":
            if crawler is None:
                raise RuntimeError("Browser acquisition requires a crawler")
            page = await _acquire_browser(
                crawler, url, BrowserProfileConfig.model_validate(config.model_dump())
            )
        elif profile == "firecrawl":
            page = await _acquire_firecrawl(
                http_client, url, FirecrawlProfileConfig.model_validate(config.model_dump())
            )
        else:
            raise ValueError(f"Unsupported crawl profile: {profile}")
    except Exception as exc:
        duration = time.perf_counter() - start_time
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                resource=url,
                phase="crawl",
                status="failed",
                message="Page load failed.",
                duration=duration,
                error=str(exc),
            ),
        )
        failure_code, failure_stage, retryable = _exception_failure(exc, profile)
        page = CrawlPage(
            url=url,
            success=False,
            duration_seconds=duration,
            error=str(exc),
            failure_code=failure_code,
            failure_stage=failure_stage,
            failure_retryable=retryable,
        )
        return page

    duration = time.perf_counter() - start_time
    page = page.model_copy(update={"duration_seconds": duration})
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=url,
            phase="crawl",
            status="succeeded" if page.success else "failed",
            message=(
                f"Page loaded with HTTP {page.status_code}."
                if page.success and page.status_code
                else "Page loaded."
                if page.success
                else "Page load failed."
            ),
            metadata={"status_code": page.status_code, "profile": profile}
            if page.status_code
            else {"profile": profile},
            duration=duration,
            error=page.error,
        ),
    )
    return page


async def _acquire_browser(
    crawler: AsyncWebCrawler,
    url: str,
    config: BrowserProfileConfig,
) -> CrawlPage:
    result = await crawl_single_url(
        crawler=crawler,
        url=url,
        run_config=run_config_for_mode(
            mode=config.mode,
            wait=config.wait,
            **config.run_config_overrides,
        ),
        mode=config.mode,
        wait=config.wait,
    )
    response_headers = getattr(result, "response_headers", None) or {}
    content_type = next(
        (
            str(value)
            for key, value in response_headers.items()
            if str(key).lower() == "content-type"
        ),
        "",
    )
    media_type = content_type.partition(";")[0].strip().lower()
    if media_type and media_type not in {"text/html", "application/xhtml+xml"}:
        error = (
            f"Browser response is not HTML ({media_type}); exact artifact capture "
            "requires an HTTP crawl policy."
        )
        return CrawlPage(
            url=result.url,
            success=False,
            status_code=result.status_code,
            duration_seconds=0,
            crawl={
                **_crawl_payload(result),
                "success": False,
                "error_message": error,
                "response_media_type": media_type,
            },
            error=error,
            failure_code="unsupported_content_type",
            failure_stage="response",
            failure_retryable=False,
        )
    html = result.html or None
    error = str(result.error_message or "").strip() or None
    success = bool(result.success) or (
        html is not None
        and result.status_code is not None
        and 200 <= result.status_code <= 399
        and error is None
    )
    crawl = {
        **_crawl_payload(result),
        "success": success,
        "error_message": error,
    }
    return CrawlPage(
        url=result.url,
        success=success,
        status_code=result.status_code,
        duration_seconds=0,
        html=html,
        crawl=crawl,
        error=error,
        failure_code="browser_navigation" if not success else None,
        failure_stage="navigation" if not success else None,
        failure_retryable=True if not success else None,
    )


async def _acquire_http(
    client: httpx.AsyncClient,
    url: str,
    config: HttpProfileConfig,
) -> CrawlPage:
    headers = {
        "Accept": _http_accept_header(config),
        "User-Agent": config.user_agent,
        **config.headers,
    }
    async with client.stream(
        "GET",
        url,
        headers=headers,
        timeout=httpx.Timeout(
            config.timeout_seconds,
            connect=min(5.0, config.timeout_seconds),
            pool=min(1.0, config.timeout_seconds),
        ),
        follow_redirects=config.follow_redirects,
    ) as response:
        content_type = response.headers.get("content-type", "")
        media_type = content_type.partition(";")[0].strip().lower()
        final_url = str(response.url)
        if not response.is_success:
            preview = await _http_error_preview(response)
            error = f"HTTP {response.status_code}"
            if preview:
                error = f"{error}: {preview}"
            retryable = response.status_code in _RETRYABLE_HTTP_STATUSES
            return CrawlPage(
                url=final_url,
                success=False,
                status_code=response.status_code,
                duration_seconds=0,
                crawl={
                    "url": final_url,
                    "success": False,
                    "status_code": response.status_code,
                    "redirected_url": final_url if final_url != url else None,
                    "error_message": error,
                    "response_media_type": media_type or None,
                },
                error=error,
                failure_code="http_status",
                failure_stage="request",
                failure_retryable=retryable,
                retry_after_seconds=(
                    _retry_after_seconds(response.headers.get("retry-after"))
                    if retryable
                    else None
                ),
            )
        if media_type not in {"text/html", "application/xhtml+xml"}:
            if _artifact_media_type_allowed(media_type, config.artifact_media_types):
                policy_limit = config.artifact_max_bytes
                repository_limit = get_int("ATLAS_REPOSITORY_MAX_ARTIFACT_BYTES")
                limit = min(policy_limit, repository_limit)
                content_length = response.headers.get("content-length")
                if (
                    content_length is not None
                    and content_length.isdigit()
                    and int(content_length) > limit
                ):
                    raise ValueError(
                        f"HTTP artifact response exceeds Atlas artifact limit of {limit} bytes"
                    )
                content = tempfile.TemporaryFile(mode="w+b")
                digest = hashlib.sha256()
                size = 0
                try:
                    def retain(chunk: bytes) -> None:
                        nonlocal size
                        size += len(chunk)
                        if size > limit:
                            raise ValueError(
                                f"HTTP artifact response exceeds Atlas artifact limit of {limit} bytes"
                            )
                        digest.update(chunk)
                        content.write(chunk)

                    async for chunk in response.aiter_bytes():
                        retain(chunk)
                    content.seek(0)
                except BaseException:
                    content.close()
                    raise
                return CrawlPage(
                    url=final_url,
                    success=True,
                    status_code=response.status_code,
                    duration_seconds=0,
                    artifact=CapturedArtifact(
                        content=content,
                        identity=ArtifactIdentity(
                            sha256=digest.hexdigest(),
                            size_bytes=size,
                        ),
                        media_type=media_type,
                        filename=_response_filename(
                            response.headers.get("content-disposition")
                        ),
                    ),
                    crawl={
                        "url": final_url,
                        "success": True,
                        "status_code": response.status_code,
                        "redirected_url": final_url if final_url != url else None,
                        "error_message": None,
                        "response_media_type": media_type,
                    },
                )
            display_type = media_type or "missing Content-Type"
            error = f"HTTP response is not HTML ({display_type})."
            return CrawlPage(
                url=final_url,
                success=False,
                status_code=response.status_code,
                duration_seconds=0,
                html=None,
                crawl={
                    "url": final_url,
                    "success": False,
                    "status_code": response.status_code,
                    "redirected_url": final_url if final_url != url else None,
                    "error_message": error,
                },
                error=error,
                failure_code="unsupported_content_type",
                failure_stage="response",
                failure_retryable=False,
            )
        limit = get_int("ATLAS_REPOSITORY_MAX_HTML_BYTES")
        content_length = response.headers.get("content-length")
        if content_length is not None and content_length.isdigit() and int(content_length) > limit:
            raise ValueError(f"HTTP response exceeds Atlas HTML limit of {limit} bytes")
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > limit:
                raise ValueError(f"HTTP response exceeds Atlas HTML limit of {limit} bytes")
            chunks.append(chunk)
        body = b"".join(chunks)
        encoding = response.encoding or "utf-8"
        html = body.decode(encoding, errors="replace") or None
        final_url = str(response.url)
        crawl = {
            "url": final_url,
            "success": True,
            "status_code": response.status_code,
            "redirected_url": final_url if final_url != url else None,
            "error_message": None,
            "response_media_type": media_type,
        }
        return CrawlPage(
            url=final_url,
            success=True,
            status_code=response.status_code,
            duration_seconds=0,
            html=html,
            crawl=crawl,
        )


def _http_accept_header(config: HttpProfileConfig) -> str:
    values = ["text/html", "application/xhtml+xml;q=0.9"]
    quality = 0.8
    for media_type in config.artifact_media_types:
        values.append(f"{media_type};q={quality:.1f}")
        quality = max(0.1, quality - 0.1)
    return ", ".join(values)


async def _http_error_preview(response: httpx.Response) -> str | None:
    captured = bytearray()
    async for chunk in response.aiter_bytes():
        remaining = _ERROR_RESPONSE_PREVIEW_BYTES - len(captured)
        if remaining <= 0:
            break
        captured.extend(chunk[:remaining])
        if len(captured) >= _ERROR_RESPONSE_PREVIEW_BYTES:
            break
    if not captured:
        return None
    text = bytes(captured).decode(response.encoding or "utf-8", errors="replace")
    preview = " ".join(text.split())
    return preview or None


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        seconds = (retry_at - datetime.now(UTC)).total_seconds()
    return min(_MAX_RETRY_AFTER_SECONDS, max(0.0, seconds))


def _artifact_media_type_allowed(media_type: str, allowed: tuple[str, ...]) -> bool:
    if not media_type:
        return False
    return media_type in allowed or f"{media_type.partition('/')[0]}/*" in allowed


def _response_filename(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None
    from email.message import Message

    message = Message()
    message["content-disposition"] = content_disposition
    filename = message.get_filename()
    if filename is None:
        return None
    value = str(filename).strip()
    return value[:1024] or None


async def _acquire_firecrawl(
    client: httpx.AsyncClient,
    url: str,
    config: FirecrawlProfileConfig,
) -> CrawlPage:
    api_key = get_optional("FIRECRAWL_API_KEY")
    if api_key is None:
        raise RuntimeError("FIRECRAWL_API_KEY is required for the firecrawl profile")
    options = dict(config.provider_options)
    for reserved in ("url", "formats"):
        if reserved in options:
            raise ValueError(f"Firecrawl provider_options cannot override {reserved}")
    payload = {
        **options,
        "url": url,
        "formats": ["rawHtml"],
        "onlyMainContent": False,
        "maxAge": 0,
        "storeInCache": False,
        "timeout": int(config.timeout_seconds * 1000),
    }
    response = await client.post(
        f"{config.api_url.rstrip('/')}/v2/scrape",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload,
        timeout=config.timeout_seconds + 5,
    )
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Firecrawl returned invalid JSON with HTTP {response.status_code}") from exc
    if not isinstance(body, dict):
        raise RuntimeError(f"Firecrawl returned invalid JSON with HTTP {response.status_code}")
    if not response.is_success or not body.get("success"):
        error = body.get("error") or f"Firecrawl returned HTTP {response.status_code}"
        return CrawlPage(
            url=url,
            success=False,
            status_code=response.status_code,
            duration_seconds=0,
            error=str(error),
            crawl={"url": url, "success": False, "status_code": response.status_code, "error_message": str(error)},
            failure_code="provider_failure",
            failure_stage="provider",
            failure_retryable=(
                response.status_code in {408, 425, 429} or response.status_code >= 500
            ),
        )
    data = body.get("data") or {}
    metadata = data.get("metadata") or {}
    html = data.get("rawHtml")
    if not isinstance(html, str) or not html:
        raise RuntimeError("Firecrawl returned no rawHtml")
    limit = get_int("ATLAS_REPOSITORY_MAX_HTML_BYTES")
    if len(html.encode()) > limit:
        raise ValueError(f"Firecrawl response exceeds Atlas HTML limit of {limit} bytes")
    final_url = str(metadata.get("url") or metadata.get("sourceURL") or url)
    status_code_value = metadata.get("statusCode")
    status_code = status_code_value if isinstance(status_code_value, int) else 200
    success = 200 <= status_code <= 399
    error = None if success else str(metadata.get("error") or f"HTTP {status_code}")
    crawl = {
        "url": final_url,
        "success": success,
        "status_code": status_code,
        "redirected_url": final_url if final_url != url else None,
        "error_message": error,
        "provider": "firecrawl",
    }
    return CrawlPage(
        url=final_url,
        success=success,
        status_code=status_code,
        duration_seconds=0,
        html=html,
        crawl=crawl,
        error=error,
        failure_code="provider_failure" if not success else None,
        failure_stage="provider" if not success else None,
        failure_retryable=(status_code in {408, 425, 429} or status_code >= 500)
        if not success
        else None,
    )


async def _canonicalize_transient_links(page: CrawlPage) -> CrawlPage:
    """Project links for a no-store page that will not pass through DuckLake."""

    if page.html is None or page.crawl is None:
        return page
    links = await asyncio.to_thread(
        links_from_html,
        page.html,
        page_url=page.crawl.get("redirected_url") or page.url,
    )
    return page.model_copy(update={"crawl": {**page.crawl, "links": links}})


def _failure_values(
    page: CrawlPage,
    crawl: dict[str, Any] | None,
    *,
    profile: str,
    has_content: bool,
) -> tuple[str, str | None, str | None, bool | None, str | None]:
    raw_detail = page.error or (crawl or {}).get("error_message")
    detail = str(raw_detail).strip() if raw_detail is not None else None
    detail = detail or None
    if not has_content and detail is None:
        return "failed", "missing_content", "capture", False, "Acquisition produced no captured content."
    if not page.success and detail is None:
        detail = "Acquisition did not report success."
    if detail is None:
        return "success", None, None, None, None
    code = page.failure_code
    stage = page.failure_stage
    retryable = page.failure_retryable
    if code is None or stage is None or retryable is None:
        code, stage, retryable = _exception_failure(RuntimeError(str(detail)), profile)
    return (
        "partial" if has_content else "failed",
        code,
        stage,
        retryable,
        str(detail)[:2048],
    )


def _durable_crawl_id(
    *,
    crawl_request_id: UUID,
) -> UUID:
    """Use the logical request identity for retry-stable durable acquisition."""

    return crawl_request_id


@dataclass(frozen=True)
class _RunEnvelope:
    graph_id: UUID
    graph_run_id: UUID
    graph_node_id: UUID
    crawl_request_id: UUID
    purpose: str
    trial: dict | None
    source_crawl_id: UUID | None
    source_edge_id: UUID | None


def _run_envelope(
    *,
    crawl_request_id: UUID,
) -> _RunEnvelope:
    """Read the graph runtime provenance needed by repository ingestion."""

    execution = current_graph_execution()
    if execution is None or execution.crawl_request_id != crawl_request_id:
        raise RuntimeError(f"Crawl request {crawl_request_id} has no execution context")
    return _RunEnvelope(
        graph_id=execution.graph_id,
        graph_run_id=execution.graph_run_id,
        graph_node_id=execution.graph_node_id,
        crawl_request_id=execution.crawl_request_id,
        purpose=execution.purpose,
        trial=execution.trial,
        source_crawl_id=execution.source_crawl_id,
        source_edge_id=execution.source_edge_id,
    )


async def _persist_page(
    session: Session,
    *,
    crawl_request_id: UUID,
    requested_url: str,
    page: CrawlPage,
    profile: str,
    profile_config: ProfileConfig,
    policy: CrawlPolicySnapshot | None = None,
    repository_pipeline: RepositoryPipeline | None = None,
    cache_policy: ResolvedCachePolicy,
    retain_html: bool,
    include_links: bool,
    resource_grants=None,
) -> CrawlPage:
    normalized_url = normalize_url(requested_url)
    config_json = _frozen_config(profile_config, cache_policy)
    config_hash = _config_hash(config_json)
    crawl_payload = page.crawl or {}
    finished_at = _utc_now()
    duration_ms = int(page.duration_seconds * 1000)
    crawl_id = _durable_crawl_id(
        crawl_request_id=crawl_request_id,
    )

    async def persist_with(pipeline: RepositoryPipeline) -> CrawlPage:
        resumed = await _repository_retry_page(
            pipeline,
            session=session,
            crawl_id=crawl_id,
            crawl_request_id=crawl_request_id,
            requested_url=requested_url,
            normalized_url=normalized_url,
            config_hash=config_hash,
            progress_reporter=None,
            include_html=retain_html,
            include_links=include_links,
        )
        if resumed is not None:
            return resumed

        run_envelope = _run_envelope(crawl_request_id=crawl_request_id)
        commit_checkpoint(session)

        html_identity = (
            await asyncio.to_thread(identify_html, page.html)
            if page.html is not None
            else None
        )
        artifact_identity = (
            page.artifact.identity if page.artifact is not None else None
        )
        if html_identity is not None and artifact_identity is not None:
            raise ValueError("acquisition produced both HTML and an artifact")
        outcome, failure_code, failure_stage, failure_retryable, failure_detail = (
            _failure_values(
                page,
                crawl_payload,
                profile=profile,
                has_content=(
                    html_identity is not None or artifact_identity is not None
                ),
            )
        )
        trial = run_envelope.trial or {}
        record = CrawlRecord(
            crawl_id=crawl_id,
            document_id=(
                html_identity.document_id if html_identity is not None else None
            ),
            artifact_id=(
                artifact_identity.artifact_id
                if artifact_identity is not None
                else None
            ),
            graph_id=run_envelope.graph_id,
            graph_run_id=run_envelope.graph_run_id,
            graph_node_id=run_envelope.graph_node_id,
            crawl_request_id=run_envelope.crawl_request_id,
            purpose=run_envelope.purpose,
            trial_id=(
                UUID(str(run_envelope.trial["trial_id"]))
                if run_envelope.trial is not None
                else None
            ),
            source_crawl_id=run_envelope.source_crawl_id,
            source_edge_id=run_envelope.source_edge_id,
            requested_url=requested_url,
            normalized_url=normalized_url,
            final_url=crawl_payload.get("redirected_url") or page.url,
            captured_at=finished_at,
            status_code=page.status_code,
            duration_ms=duration_ms,
            response_media_type=(
                page.artifact.media_type
                if page.artifact is not None
                else crawl_payload.get("response_media_type")
            ),
            response_filename=(
                page.artifact.filename if page.artifact is not None else None
            ),
            profile=profile,
            crawl_profile_id=policy.profile.id if policy is not None else None,
            crawl_profile_slug=(policy.profile.slug if policy is not None else "unknown"),
            remote_concurrency=(policy.max_concurrency if policy is not None else 1),
            config_json=config_json,
            config_hash=config_hash,
            crawl_policy_id=(
                policy.id if policy is not None and policy.origin == "editable" else None
            ),
            outcome=outcome,
            failure_code=failure_code,
            failure_stage=failure_stage,
            failure_retryable=failure_retryable,
            failure_detail=failure_detail,
            trial_sampler_version=trial.get("sampler_version"),
            trial_sample_rate=trial.get("sample_share"),
            trial_candidate_strategy=trial.get("candidate_strategy"),
            trial_candidate_profile_id=trial.get("candidate_profile_id"),
            trial_candidate_profile_slug=trial.get("candidate_profile_slug"),
            trial_candidate_profile_config_hash=trial.get(
                "candidate_profile_config_hash"
            ),
        )
        result_page = page
        if page.html is not None:
            if resource_grants is None:
                await pipeline.store_raw(
                    captured_html=page.html,
                    identity=html_identity,
                )
            else:
                async with resource_permits(
                    resource_grants,
                    object_request(
                        f"raw-html:{crawl_id}",
                        direction="write",
                        byte_count=html_identity.size_bytes,
                        service_class="critical",
                    ),
                    acquire_timeout=DURABLE_RESOURCE_WAIT,
                ):
                    await pipeline.store_raw(
                        captured_html=page.html,
                        identity=html_identity,
                    )
            if not retain_html:
                # Raw storage is the last operation that needs the captured string.
                # Drop this function's reference before ingestion backpressure and
                # structural reads so large pages do not accumulate in crawl workers.
                result_page = page.model_copy(update={"html": None})
        elif page.artifact is not None:
            if resource_grants is None:
                await pipeline.store_artifact(
                    content=page.artifact.content,
                    identity=page.artifact.identity,
                )
            else:
                async with resource_permits(
                    resource_grants,
                    object_request(
                        f"raw-artifact:{crawl_id}",
                        direction="write",
                        byte_count=page.artifact.identity.size_bytes,
                        service_class="critical",
                    ),
                    acquire_timeout=DURABLE_RESOURCE_WAIT,
                ):
                    await pipeline.store_artifact(
                        content=page.artifact.content,
                        identity=page.artifact.identity,
                    )
            result_page = page.model_copy(update={"artifact": None})
        await pipeline.enqueue_stored(record)
        return result_page.model_copy(
            update={
                "crawl_id": crawl_id,
                "document_id": record.document_id,
                "artifact_id": record.artifact_id,
                "repository_snapshot": None,
                "repository_crawl_created": None,
                "crawl": crawl_payload,
            }
        )

    try:
        if repository_pipeline is not None:
            return await persist_with(repository_pipeline)
        async with RepositoryPipeline(repository_ingestor_from_env()) as owned_pipeline:
            return await persist_with(owned_pipeline)
    finally:
        if page.artifact is not None:
            page.artifact.close()


async def _repository_cached_page(
    repository_pipeline: RepositoryPipeline,
    *,
    session: Session,
    requested_url: str,
    normalized_url: str,
    config_hash: str,
    cache_block_rules: dict[str, Any] | None,
    progress_reporter: ProgressReporter | None,
    captured_after: datetime | None,
    captured_before: datetime | None = None,
    cache_status: str = "repository",
    include_html: bool = True,
    include_links: bool = True,
) -> CrawlPage | None:
    hit = await repository_pipeline.resolve_cached_page(
        normalized_url=normalized_url,
        config_hash=config_hash,
        cache_block_rules=cache_block_rules,
        captured_after=captured_after,
        captured_before=captured_before,
        include_html=include_html,
        include_links=include_links,
    )
    if hit is None:
        crawl_metrics.repository_cache(
            outcome="stale_miss" if cache_status == "stale_if_error" else "miss"
        )
        return None

    commit_checkpoint(session)

    cached_age_seconds = max(
        0.0, (_utc_now() - hit.crawl.captured_at).total_seconds()
    )
    crawl_metrics.repository_cache(
        outcome="stale_if_error" if cache_status == "stale_if_error" else "hit",
        age_seconds=cached_age_seconds,
    )

    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=normalized_url,
            phase="cache",
            status="succeeded",
            message=(
                "Using stale repository data after network acquisition failed."
                if cache_status == "stale_if_error"
                else "Rebuilt cached DOM from canonical HTML."
                if hit.projection_rebuilt
                else "Reused cached repository data."
            ),
            duration=0.0,
            metadata={
                "source": "repository",
                "projection_rebuilt": hit.projection_rebuilt,
                "cache_status": cache_status,
                "cached_age_seconds": cached_age_seconds,
            },
        ),
    )
    crawl_payload = _repository_crawl_payload(
        hit,
        normalized_url=normalized_url,
        cache_status=cache_status,
    )
    return _crawl_page_from_repository_hit(
        hit,
        normalized_url=normalized_url,
        cache_status=cache_status,
        duration_seconds=0.0,
        crawl_payload=crawl_payload,
    )


async def _repository_retry_page(
    repository_pipeline: RepositoryPipeline,
    *,
    session: Session,
    crawl_id: UUID,
    crawl_request_id: UUID,
    requested_url: str,
    normalized_url: str,
    config_hash: str,
    progress_reporter: ProgressReporter | None,
    include_html: bool,
    include_links: bool,
) -> CrawlPage | None:
    """Resume a crawl already committed by an earlier request delivery."""

    hit = await repository_pipeline.resolve_crawl(
        crawl_id,
        include_html=include_html,
        include_links=include_links,
    )
    if hit is None:
        return None
    if (
        hit.crawl.crawl_request_id != crawl_request_id
        or hit.crawl.normalized_url != normalized_url
        or hit.crawl.config_hash != config_hash
    ):
        raise RuntimeError(
            f"crawl identity {crawl_id} resolved to incompatible durable provenance"
        )

    commit_checkpoint(session)

    crawl_metrics.repository_cache(
        outcome="retry_resume",
        age_seconds=max(0.0, (_utc_now() - hit.crawl.captured_at).total_seconds()),
    )
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=normalized_url,
            phase="cache",
            status="succeeded",
            message="Resumed the acquisition committed by an earlier request delivery.",
            duration=0.0,
            metadata={
                "source": "repository",
                "cache_status": "retry_resume",
                "projection_rebuilt": hit.projection_rebuilt,
            },
        ),
    )
    return _crawl_page_from_repository_hit(
        hit,
        normalized_url=normalized_url,
        cache_status="retry_resume",
        duration_seconds=(hit.crawl.duration_ms or 0) / 1000.0,
    )


def _crawl_page_from_repository_hit(
    hit: RepositoryCacheHit,
    *,
    normalized_url: str,
    cache_status: str,
    duration_seconds: float,
    crawl_payload: dict[str, Any] | None = None,
) -> CrawlPage:
    error = _first_crawl_error(hit.crawl)
    success = (
        error is None
        and (hit.crawl.status_code is None or 200 <= hit.crawl.status_code <= 399)
    )
    payload = crawl_payload or _repository_crawl_payload(
        hit,
        normalized_url=normalized_url,
        cache_status=cache_status,
        success=success,
        error=error,
    )
    return CrawlPage(
        url=hit.crawl.final_url or normalized_url,
        success=success,
        status_code=hit.crawl.status_code,
        duration_seconds=duration_seconds,
        crawl_id=hit.crawl.crawl_id,
        document_id=(
            hit.document.document_id
            if hit.document is not None
            else hit.crawl.document_id
        ),
        artifact_id=(
            hit.artifact.artifact_id
            if hit.artifact is not None
            else hit.crawl.artifact_id
        ),
        repository_snapshot=hit.repository_snapshot,
        repository_crawl_created=False,
        html=hit.html,
        crawl=payload,
        error=error,
        failure_code=hit.crawl.failure_code,
        failure_stage=hit.crawl.failure_stage,
        failure_retryable=hit.crawl.failure_retryable,
    )


def _first_crawl_error(crawl: CrawlRecord) -> str | None:
    return crawl.failure_detail


def _repository_crawl_payload(
    hit: RepositoryCacheHit,
    *,
    normalized_url: str,
    cache_status: str,
    success: bool = True,
    error: str | None = None,
) -> dict[str, Any]:
    base_url = hit.crawl.final_url or normalized_url
    payload = {
        "url": base_url,
        "success": success,
        "status_code": hit.crawl.status_code,
        "redirected_url": (
            hit.crawl.final_url if hit.crawl.final_url != normalized_url else None
        ),
        "cache_status": (
            cache_status
            if cache_status == "stale_if_error"
            else "repository_rebuilt"
            if hit.projection_rebuilt
            else cache_status
        ),
        "error_message": error,
    }
    if hit.links is not None:
        payload["links"] = hit.links
    return payload


def _profile_from_policy(
    policy: CrawlPolicySnapshot,
) -> tuple[str, ProfileConfig]:
    return policy.profile.transport, policy.profile.parsed_config()


def _frozen_crawl_policy() -> CrawlPolicySnapshot:
    execution = current_graph_execution()
    if execution is None:
        raise RuntimeError("Crawl acquisition has no graph execution context")
    value = execution.effective_policy_snapshot_json
    return CrawlPolicySnapshot.model_validate(value)


async def _crawl_graph_request(
    *,
    url: str,
    progress_reporter: ProgressReporter | None = None,
    session: Session,
    crawl_request_id: UUID,
    repository_pipeline: RepositoryPipeline | None = None,
    crawler: AsyncWebCrawler | None = None,
    http_client: httpx.AsyncClient | None = None,
    resource_grants=None,
    cache: CacheOptions | dict[str, Any] | None = None,
    retain_html: bool = True,
    include_links: bool = True,
    persist_retryable_failure: bool = True,
) -> CrawlPage:
    cache_options = cache if isinstance(cache, CacheOptions) else CacheOptions.model_validate(cache or {})
    policy = _frozen_crawl_policy()
    profile, profile_config = _profile_from_policy(policy)
    cache_block_rules = profile_config.cache_block_rules
    commit_checkpoint(session)

    cache_policy = resolve_cache_policy(
        crawl_policy_config=profile_config.model_dump(mode="python"),
        request=cache_options,
    )
    if not cache_policy.stores_result:
        # Graph nodes always produce durable evidence. Preserve the policy's
        # cache bypass intent while changing no-store into a fresh durable load.
        cache_policy = cache_policy.model_copy(update={"mode": "refresh"})
    cache_now = _utc_now()
    fresh_after = cache_now - timedelta(seconds=cache_policy.max_age_seconds)
    if not cache_policy.reads_cache:
        crawl_metrics.repository_cache(outcome=cache_policy.mode)
    remote_domain = _remote_domain(url)
    acquisition_recorded = False
    acquisition_failure_duration = 0.0
    commit_checkpoint(session)

    normalized_url = normalize_url(url)
    repository_config_hash = _config_hash(
        _frozen_config(profile_config, cache_policy)
    )
    durable_crawl_id = _durable_crawl_id(crawl_request_id=crawl_request_id)

    async def stale_fallback(page: CrawlPage) -> CrawlPage:
        if (
            page.success
            or not cache_policy.reads_cache
            or cache_policy.stale_if_error_seconds is None
            or repository_pipeline is None
        ):
            return page

        stale_page = await _repository_cached_page(
            repository_pipeline,
            session=session,
            requested_url=url,
            normalized_url=normalized_url,
            config_hash=repository_config_hash,
            cache_block_rules=cache_block_rules,
            progress_reporter=progress_reporter,
            captured_after=cache_now
            - timedelta(seconds=cache_policy.stale_if_error_seconds),
            captured_before=fresh_after,
            cache_status="stale_if_error",
            include_html=retain_html,
            include_links=include_links,
        )
        if stale_page is None:
            return page
        return stale_page if retain_html else stale_page.model_copy(update={"html": None})

    if (
        repository_pipeline is not None
    ):
        resumed_page = await _repository_retry_page(
            repository_pipeline,
            session=session,
            crawl_id=durable_crawl_id,
            crawl_request_id=crawl_request_id,
            requested_url=url,
            normalized_url=normalized_url,
            config_hash=repository_config_hash,
            progress_reporter=progress_reporter,
            include_html=retain_html,
            include_links=include_links,
        )
        if resumed_page is not None:
            crawl_metrics.page_acquisition(
                page=resumed_page,
                duration_seconds=0.0,
                mode=profile,
                source="cache",
                remote_domain=remote_domain,
            )
            return await stale_fallback(resumed_page)

    if (
        repository_pipeline is not None
        and cache_policy.reads_cache
    ):
        commit_checkpoint(session)
        repository_page = await _repository_cached_page(
            repository_pipeline,
            session=session,
            requested_url=url,
            normalized_url=normalized_url,
            config_hash=repository_config_hash,
            cache_block_rules=cache_block_rules,
            progress_reporter=progress_reporter,
            captured_after=fresh_after,
            include_html=retain_html,
            include_links=include_links,
        )
        if repository_page is not None:
            crawl_metrics.page_acquisition(
                page=repository_page,
                duration_seconds=0.0,
                mode=profile,
                source="cache",
                remote_domain=remote_domain,
            )
            # A graph request always creates its own crawl observation and
            # provenance, even when immutable HTML is reused from the cache.
            persisted_page = await _persist_page(
                session,
                crawl_request_id=crawl_request_id,
                requested_url=url,
                page=repository_page,
                profile=profile,
                profile_config=profile_config,
                policy=policy,
                repository_pipeline=repository_pipeline,
                cache_policy=cache_policy,
                retain_html=retain_html,
                include_links=include_links,
            )
            return persisted_page

    async def load_with(
        active_crawler: AsyncWebCrawler | None,
        active_http_client: httpx.AsyncClient,
    ) -> tuple[CrawlPage, bool]:
        if profile == "browser" and active_crawler is None:
            raise RuntimeError("Browser acquisition requires a crawler")
        page = await _crawl_url(
            crawler=active_crawler,
            http_client=active_http_client,
            url=url,
            profile=profile,
            config=profile_config,
            progress_reporter=progress_reporter,
        )
        return page, False

    async def load_page() -> tuple[CrawlPage, bool]:
        if http_client is not None:
            if profile != "browser" or crawler is not None:
                return await load_with(crawler, http_client)
            browser_config = BrowserProfileConfig.model_validate(profile_config.model_dump())
            from crawl4ai import AsyncWebCrawler as OwnedWebCrawler

            async with OwnedWebCrawler(
                config=browser_config_for_mode(browser_config.mode)
            ) as owned_crawler:
                return await load_with(owned_crawler, http_client)
        async with httpx.AsyncClient() as owned_http_client:
            if profile != "browser" or crawler is not None:
                return await load_with(crawler, owned_http_client)
            browser_config = BrowserProfileConfig.model_validate(profile_config.model_dump())
            from crawl4ai import AsyncWebCrawler as OwnedWebCrawler

            async with OwnedWebCrawler(
                config=browser_config_for_mode(browser_config.mode)
            ) as owned_crawler:
                return await load_with(owned_crawler, owned_http_client)

    async def measured_load_page() -> tuple[CrawlPage, bool]:
        nonlocal acquisition_failure_duration, acquisition_recorded
        started_at = time.perf_counter()
        try:
            loaded_page, used_cache = await load_page()
        except asyncio.CancelledError:
            cancelled_page = CrawlPage(
                url=url,
                success=False,
                duration_seconds=time.perf_counter() - started_at,
                error="Page acquisition cancelled.",
            )
            crawl_metrics.page_acquisition(
                page=cancelled_page,
                duration_seconds=cancelled_page.duration_seconds,
                mode=profile,
                source="network",
                remote_domain=remote_domain,
                outcome="cancelled",
            )
            acquisition_recorded = True
            raise
        except Exception as exc:
            acquisition_failure_duration = time.perf_counter() - started_at
            failed_page = CrawlPage(
                url=url,
                success=False,
                duration_seconds=acquisition_failure_duration,
                error=str(exc),
            )
            crawl_metrics.page_acquisition(
                page=failed_page,
                duration_seconds=failed_page.duration_seconds,
                mode=profile,
                source="network",
                remote_domain=remote_domain,
            )
            acquisition_recorded = True
            raise
        crawl_metrics.page_acquisition(
            page=loaded_page,
            duration_seconds=time.perf_counter() - started_at,
            mode=profile,
            source="cache" if used_cache else "network",
            remote_domain=remote_domain,
        )
        acquisition_recorded = True
        return loaded_page, used_cache

    try:
        async with AsyncExitStack() as stack:
            if resource_grants is not None:
                await stack.enter_async_context(
                    resource_permits(
                        resource_grants,
                        remote_request(
                            str(crawl_request_id),
                            remote_domain=remote_domain,
                            concurrency=policy.max_concurrency,
                        ),
                        acquire_timeout=DURABLE_RESOURCE_WAIT,
                    )
                )
            if profile == "browser":
                await stack.enter_async_context(_browser_slot())
            page, reused_cache = await measured_load_page()
    except asyncio.CancelledError:
        raise
    except (ResourceCapacityUnavailable, ResourcePermitLost):
        raise
    except Exception as exc:
        failure_code, failure_stage, failure_retryable = _exception_failure(
            exc, profile
        )
        await emit_progress(
            progress_reporter,
            ProgressEvent(
                resource=url,
                phase="crawl",
                status="failed",
                message="Page load failed.",
                error=str(exc),
            ),
        )
        page = CrawlPage(
            url=url,
            success=False,
            duration_seconds=acquisition_failure_duration,
            error=str(exc),
            failure_code=failure_code,
            failure_stage=failure_stage,
            failure_retryable=failure_retryable,
        )
        reused_cache = False
        if not acquisition_recorded:
            crawl_metrics.crawl_failure(
                page=page, mode=profile, remote_domain=remote_domain
            )

    if (
        not page.success
        and page.failure_retryable is True
        and not persist_retryable_failure
    ):
        raise RetryableAcquisitionError(page)

    if (
        not reused_cache
        and cache_policy.stores_result
    ):
        page = await _persist_page(
            session,
            crawl_request_id=crawl_request_id,
            requested_url=url,
            page=page,
            profile=profile,
            profile_config=profile_config,
            policy=policy,
            repository_pipeline=repository_pipeline,
            cache_policy=cache_policy,
            retain_html=retain_html,
            include_links=include_links,
            resource_grants=resource_grants,
        )
        if page.repository_crawl_created:
            crawl_metrics.crawl_persisted(
                page=page,
                mode=profile,
                remote_domain=remote_domain,
            )
    elif include_links and page.success and page.html is not None:
        page = await _canonicalize_transient_links(page)

    return await stale_fallback(page)


async def crawl_graph_request(
    *,
    session: Session,
    url: str,
    context: GraphExecutionContext,
    progress_reporter: ProgressReporter | None = None,
    crawler: AsyncWebCrawler | None = None,
    http_client: httpx.AsyncClient | None = None,
    resource_grants=None,
    repository_pipeline: RepositoryPipeline | None = None,
    persist_retryable_failure: bool = True,
) -> CrawlPage:
    """Acquire and durably ingest one frozen graph crawl request.

    Workers pass process-owned browser and repository lifecycles. Direct callers may omit them for
    a bounded one-shot acquisition.
    """

    with graph_execution_scope(context):
        if repository_pipeline is not None:
            return await _crawl_graph_request(
                url=url,
                session=session,
                crawl_request_id=context.crawl_request_id,
                repository_pipeline=repository_pipeline,
                progress_reporter=progress_reporter,
                crawler=crawler,
                http_client=http_client,
                resource_grants=resource_grants,
                persist_retryable_failure=persist_retryable_failure,
            )
        async with RepositoryPipeline(repository_ingestor_from_env()) as pipeline:
            return await _crawl_graph_request(
                url=url,
                session=session,
                crawl_request_id=context.crawl_request_id,
                repository_pipeline=pipeline,
                progress_reporter=progress_reporter,
                crawler=crawler,
                http_client=http_client,
                resource_grants=resource_grants,
                persist_retryable_failure=persist_retryable_failure,
            )
