import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from crawl4ai import AsyncWebCrawler
from crawl4ai.models import CrawlResult
from pydantic import ValidationError
from sqlalchemy.orm import Session

from actions.shared.crawl import (
    CrawlMode,
    CrawlWait,
    browser_config_for_mode,
    crawl_single_url,
    run_config_for_mode,
)
from actions.shared.cache import CacheOptions, ResolvedCachePolicy, resolve_cache_policy
from actions.shared.progress import ProgressReporter, ProgressEvent, emit_progress
from actions.shared.quality.schemas import QualityWarning
from actions.shared.quality.service import run_quality_checks
from repository.catalogue import CrawlRecord
from dom import links_from_html
from control.crawl_policies.schemas import CrawlPolicySnapshot
from runtime.crawl_capacity import capacity_lease
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
from control.url_matching import normalize_url

from .schemas import CrawlPage


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _input_hash(
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    run_config_overrides: dict[str, Any] | None = None,
) -> str:
    payload = {"url": url, "mode": mode, "wait": wait, "run_config_overrides": run_config_overrides or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
    crawler: AsyncWebCrawler,
    url: str,
    mode: CrawlMode,
    wait: CrawlWait,
    progress_reporter: ProgressReporter | None,
    run_config_overrides: dict[str, Any] | None = None,
    domain_group: str = "unclassified",
) -> CrawlPage:
    await emit_progress(
        progress_reporter,
        ProgressEvent(resource=url, phase="crawl", status="started", message="Loading page."),
    )
    start_time = time.perf_counter()

    try:
        result = await crawl_single_url(
            crawler=crawler,
            url=url,
            run_config=run_config_for_mode(mode=mode, wait=wait, **(run_config_overrides or {})),
            mode=mode,
            wait=wait,
        )
        html = result.html or ""
        crawl = _crawl_payload(result)
        quality_warnings = run_quality_checks(url=result.url, html=html, crawl=crawl)
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
        page = CrawlPage(
            url=url,
            success=False,
            duration_seconds=duration,
            error=str(exc),
        )
        return page

    duration = time.perf_counter() - start_time
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            resource=url,
            phase="crawl",
            status="succeeded" if result.success else "failed",
            message=(
                f"Page loaded with HTTP {result.status_code}."
                if result.success and result.status_code
                else "Page loaded."
                if result.success
                else "Page load failed."
            ),
            metadata={"status_code": result.status_code} if result.status_code else {},
            duration=duration,
            error=result.error_message,
        ),
    )
    page = CrawlPage(
        url=result.url,
        success=result.success,
        status_code=result.status_code,
        duration_seconds=duration,
        html=html,
        crawl=crawl,
        quality_warnings=quality_warnings,
        error=result.error_message,
    )
    return page


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


def _errors(page: CrawlPage, crawl: dict[str, Any] | None) -> dict[str, Any]:
    error = page.error or (crawl or {}).get("error_message")
    return {"message": error} if error else {}


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
        source_crawl_id=execution.source_crawl_id,
        source_edge_id=execution.source_edge_id,
    )


async def _persist_page(
    session: Session,
    *,
    crawl_request_id: UUID,
    requested_url: str,
    page: CrawlPage,
    mode: CrawlMode,
    wait: CrawlWait,
    run_config_overrides: dict[str, Any] | None = None,
    policy: CrawlPolicySnapshot | None = None,
    repository_pipeline: RepositoryPipeline | None = None,
    cache_policy: ResolvedCachePolicy,
    retain_html: bool,
    include_links: bool,
) -> CrawlPage:
    normalized_url = normalize_url(requested_url)
    input_hash = _input_hash(normalized_url, mode, wait, run_config_overrides)
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
            input_hash=input_hash,
            progress_reporter=None,
            include_html=retain_html,
            include_links=include_links,
        )
        if resumed is not None:
            return resumed

        run_envelope = _run_envelope(crawl_request_id=crawl_request_id)
        commit_checkpoint(session)

        identity = (
            await asyncio.to_thread(identify_html, page.html)
            if page.html is not None
            else None
        )
        error = _errors(page, crawl_payload)
        if identity is None and not error:
            error = {"message": "Acquisition produced no captured HTML."}
        record = CrawlRecord(
            crawl_id=crawl_id,
            document_id=identity.document_id if identity is not None else None,
            graph_id=run_envelope.graph_id,
            graph_run_id=run_envelope.graph_run_id,
            graph_node_id=run_envelope.graph_node_id,
            crawl_request_id=run_envelope.crawl_request_id,
            source_crawl_id=run_envelope.source_crawl_id,
            source_edge_id=run_envelope.source_edge_id,
            requested_url=requested_url,
            normalized_url=normalized_url,
            final_url=crawl_payload.get("redirected_url") or page.url,
            captured_at=finished_at,
            status_code=page.status_code,
            duration_ms=duration_ms,
            input_json={
                "acquisition": {
                    "url": requested_url,
                    "mode": mode,
                    "wait": wait,
                    "run_config_overrides": _json_safe(run_config_overrides or {}),
                    "cache": cache_policy.model_dump(mode="json"),
                },
                "crawl_policy": (
                    {
                        "id": str(policy.id),
                        "revision": policy.revision,
                        "match": policy.match,
                        "domain_group": policy.domain_group,
                        "config": _json_safe(policy.config or {}),
                    }
                    if policy is not None
                    else None
                ),
            },
            input_hash=input_hash,
            crawl_policy_id=policy.id if policy is not None else None,
            crawl_policy_revision=(policy.revision if policy is not None else None),
            data_schema_id=None,
            query_schema_id=None,
            warnings_json=[_json_safe(warning) for warning in page.quality_warnings],
            errors_json=[error] if error else [],
        )
        result_page = page
        if page.html is not None:
            await pipeline.store_raw(captured_html=page.html, identity=identity)
            if not retain_html:
                # Raw storage is the last operation that needs the captured string.
                # Drop this function's reference before ingestion backpressure and
                # structural reads so large pages do not accumulate in crawl workers.
                result_page = page.model_copy(update={"html": None})
        repository_result = await pipeline.submit_stored(record)
        links = (
            await pipeline.projected_links(
                repository_result.document_id,
                page_url=crawl_payload.get("redirected_url") or result_page.url,
            )
            if include_links and repository_result.document_id is not None
            else None
        )
        durable_payload = (
            {**crawl_payload, "links": links} if links is not None else crawl_payload
        )
        return result_page.model_copy(
            update={
                "crawl_id": crawl_id,
                "document_id": repository_result.document_id,
                "repository_snapshot": repository_result.repository_snapshot,
                "repository_crawl_created": repository_result.crawl_created,
                "crawl": durable_payload,
            }
        )

    if repository_pipeline is not None:
        return await persist_with(repository_pipeline)
    async with RepositoryPipeline(repository_ingestor_from_env()) as owned_pipeline:
        return await persist_with(owned_pipeline)


async def _repository_cached_page(
    repository_pipeline: RepositoryPipeline,
    *,
    session: Session,
    requested_url: str,
    normalized_url: str,
    input_hash: str,
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
        input_hash=input_hash,
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
    input_hash: str,
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
        or hit.crawl.input_hash != input_hash
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
        document_id=(hit.document.document_id if hit.document is not None else None),
        repository_snapshot=hit.repository_snapshot,
        repository_crawl_created=False,
        html=hit.html,
        crawl=payload,
        quality_warnings=_quality_warnings_from_crawl(hit.crawl),
        error=error,
    )


def _first_crawl_error(crawl: CrawlRecord) -> str | None:
    for value in crawl.errors_json:
        if isinstance(value, dict) and value.get("message"):
            return str(value["message"])
        if isinstance(value, str) and value:
            return value
    return None


def _quality_warnings_from_crawl(crawl: CrawlRecord) -> list[QualityWarning]:
    warnings: list[QualityWarning] = []
    for value in crawl.warnings_json:
        try:
            warnings.append(QualityWarning.model_validate(value))
        except ValidationError:
            continue
    return warnings


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


def _cache_block_rules_from_config(config: dict[str, Any]) -> dict[str, Any]:
    cache_block_rules = config.get("cache_block_rules")
    return cache_block_rules if isinstance(cache_block_rules, dict) else {}


def _transport_from_policy(
    policy: CrawlPolicySnapshot,
) -> tuple[CrawlMode, CrawlWait, dict[str, Any], dict[str, Any]]:
    config = policy.config or {}
    mode = config.get("mode") or "static"
    wait = config.get("wait") or "none"
    run_config_overrides = config.get("run_config_overrides") or {}
    return (
        mode,
        wait,
        run_config_overrides,
        _cache_block_rules_from_config(config),
    )


def _frozen_crawl_policy() -> CrawlPolicySnapshot | None:
    execution = current_graph_execution()
    if execution is None:
        raise RuntimeError("Crawl acquisition has no graph execution context")
    value = execution.effective_policy_snapshot_json
    return CrawlPolicySnapshot.model_validate(value) if value is not None else None


async def _crawl_graph_request(
    *,
    url: str,
    mode: CrawlMode | None = None,
    wait: CrawlWait | None = None,
    progress_reporter: ProgressReporter | None = None,
    session: Session,
    crawl_request_id: UUID,
    run_config_overrides: dict[str, Any] | None = None,
    repository_pipeline: RepositoryPipeline | None = None,
    cache: CacheOptions | dict[str, Any] | None = None,
    retain_html: bool = True,
    include_links: bool = True,
) -> CrawlPage:
    cache_options = cache if isinstance(cache, CacheOptions) else CacheOptions.model_validate(cache or {})
    cache_block_rules: dict[str, Any] | None = None
    policy = _frozen_crawl_policy()
    if mode is None or wait is None:
        if policy is None:
            mode, wait, run_config_overrides = "static", "none", {}
            cache_block_rules = {}
        else:
            (
                mode,
                wait,
                run_config_overrides,
                cache_block_rules,
            ) = _transport_from_policy(policy)
        commit_checkpoint(session)

    cache_policy = resolve_cache_policy(
        crawl_policy_config=policy.config if policy is not None else None,
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
    domain_group = policy.domain_group if policy is not None else "unclassified"
    acquisition_recorded = False
    acquisition_failure_duration = 0.0
    commit_checkpoint(session)

    normalized_url = normalize_url(url)
    repository_input_hash = _input_hash(
        normalized_url,
        mode,
        wait,
        run_config_overrides,
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
            input_hash=repository_input_hash,
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
            input_hash=repository_input_hash,
            progress_reporter=progress_reporter,
            include_html=retain_html,
            include_links=include_links,
        )
        if resumed_page is not None:
            crawl_metrics.page_acquisition(
                page=resumed_page,
                duration_seconds=0.0,
                mode=mode,
                source="cache",
                domain_group=domain_group,
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
            input_hash=repository_input_hash,
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
                mode=mode,
                source="cache",
                domain_group=domain_group,
            )
            # A graph request always creates its own crawl observation and
            # provenance, even when immutable HTML is reused from the cache.
            persisted_page = await _persist_page(
                session,
                crawl_request_id=crawl_request_id,
                requested_url=url,
                page=repository_page,
                mode=mode,
                wait=wait,
                run_config_overrides=run_config_overrides,
                policy=policy,
                repository_pipeline=repository_pipeline,
                cache_policy=cache_policy,
                retain_html=retain_html,
                include_links=include_links,
            )
            return persisted_page

    async def load_with(crawler: AsyncWebCrawler) -> tuple[CrawlPage, bool]:
        page = await _crawl_url(
            crawler=crawler,
            url=url,
            mode=mode,
            wait=wait,
            progress_reporter=progress_reporter,
            run_config_overrides=run_config_overrides,
            domain_group=domain_group,
        )
        return page, False

    async def load_page() -> tuple[CrawlPage, bool]:
        async with AsyncWebCrawler(config=browser_config_for_mode(mode)) as owned_crawler:
            return await load_with(owned_crawler)

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
                mode=mode,
                source="network",
                domain_group=domain_group,
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
                mode=mode,
                source="network",
                domain_group=domain_group,
            )
            acquisition_recorded = True
            raise
        crawl_metrics.page_acquisition(
            page=loaded_page,
            duration_seconds=time.perf_counter() - started_at,
            mode=mode,
            source="cache" if used_cache else "network",
            domain_group=domain_group,
        )
        acquisition_recorded = True
        return loaded_page, used_cache

    try:
        async with capacity_lease(
            session,
            url=url,
            policy=policy,
            progress_reporter=progress_reporter,
            include_browser=True,
        ):
            page, reused_cache = await measured_load_page()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
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
        )
        reused_cache = False
        if not acquisition_recorded:
            crawl_metrics.crawl_failure(page=page, mode=mode, domain_group=domain_group)

    if (
        not reused_cache
        and cache_policy.stores_result
    ):
        page = await _persist_page(
            session,
            crawl_request_id=crawl_request_id,
            requested_url=url,
            page=page,
            mode=mode,
            wait=wait,
            run_config_overrides=run_config_overrides,
            policy=policy,
            repository_pipeline=repository_pipeline,
            cache_policy=cache_policy,
            retain_html=retain_html,
            include_links=include_links,
        )
        if page.repository_crawl_created:
            crawl_metrics.crawl_persisted(
                page=page,
                mode=mode,
                domain_group=domain_group,
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
) -> CrawlPage:
    """Acquire and durably ingest one frozen graph crawl request."""

    with graph_execution_scope(context):
        async with RepositoryPipeline(repository_ingestor_from_env()) as pipeline:
            return await _crawl_graph_request(
                url=url,
                session=session,
                crawl_request_id=context.crawl_request_id,
                repository_pipeline=pipeline,
                progress_reporter=progress_reporter,
            )
