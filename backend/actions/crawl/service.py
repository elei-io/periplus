import asyncio
import hashlib
import json
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid5

from crawl4ai import AsyncWebCrawler
from crawl4ai.models import CrawlResult
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

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
from repository.ducklake import (
    CrawlRecord,
    RunCrawlUsageRecord,
    RunCrawlUsageRole,
    RunManifestRecord,
)
from dom import links_from_html
from crawl_policies.schemas import CrawlPolicySnapshot
from crawl_policies.permits import capacity_lease
from crawl_policies.service import find_crawl_policy_snapshot_for_url
from observability import crawl_metrics
from repository import (
    RepositoryCacheHit,
    RepositoryPipeline,
    identify_html,
    repository_ingestor_from_env,
    run_usage_ingestion_request_id,
)
from tasks.models import TaskRun
from tasks.context import commit_task_checkpoint
from urls.service import normalize_url

from .schemas import CrawlOutput, CrawlPage, CrawlStats


class _CrawlerPool:
    def __init__(
        self,
        *,
        session: Session | None = None,
        task_run_id: UUID | None = None,
        progress_reporter: ProgressReporter | None = None,
    ) -> None:
        self._session = session
        self._task_run_id = task_run_id
        self._progress_reporter = progress_reporter
        self._stack = AsyncExitStack()
        self._crawlers: dict[CrawlMode, AsyncWebCrawler] = {}
        self._locks: dict[CrawlMode, asyncio.Lock] = {}

    async def __aenter__(self) -> _CrawlerPool:
        await self._stack.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        await self._stack.__aexit__(exc_type, exc, traceback)

    async def get(self, mode: CrawlMode, *, resource_url: str = "") -> AsyncWebCrawler:
        crawler = self._crawlers.get(mode)
        if crawler is not None:
            return crawler
        lock = self._locks.setdefault(mode, asyncio.Lock())
        async with lock:
            crawler = self._crawlers.get(mode)
            if crawler is not None:
                return crawler
            if self._session is not None and self._task_run_id is not None:
                await self._stack.enter_async_context(
                    capacity_lease(
                        self._session,
                        task_run_id=self._task_run_id,
                        url=resource_url,
                        policy=None,
                        progress_reporter=self._progress_reporter,
                        include_policy=False,
                    )
                )
            crawler = await self._stack.enter_async_context(
                AsyncWebCrawler(config=browser_config_for_mode(mode))
            )
            self._crawlers[mode] = crawler
            return crawler


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
    """Keep only task-local acquisition state; durable structure lives in the repository."""

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
        crawl_metrics.navigation(page=page, mode=mode, domain_group=domain_group)
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
    crawl_metrics.navigation(page=page, mode=mode, domain_group=domain_group)
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
    task_run_id: UUID,
    index: int,
    normalized_url: str,
    input_hash: str,
) -> UUID:
    """Return the stable logical acquisition identity used across run retries."""

    return uuid5(task_run_id, f"{index}\0{normalized_url}\0{input_hash}")


@dataclass(frozen=True)
class _RunEnvelope:
    manifest: RunManifestRecord
    data_schema_id: UUID | None


def _run_envelope(
    session: Session,
    *,
    task_run_id: UUID,
) -> _RunEnvelope:
    """Freeze the Postgres-backed run data needed by repository ingestion."""

    run = session.get(TaskRun, task_run_id)
    if run is None:
        raise RuntimeError(f"Task run {task_run_id} does not exist")
    return _RunEnvelope(
        manifest=RunManifestRecord(
            run_id=run.id,
            task_id=run.task_id,
            task_revision=run.task_revision,
            primitive=run.primitive,
            input_json=_json_safe(run.input_json),
            queued_at=run.queued_at,
        ),
        data_schema_id=getattr(run, "data_schema_id", None),
    )


def _run_usage_record(
    run_manifest: RunManifestRecord,
    *,
    crawl: CrawlRecord,
    requested_url: str,
    normalized_url: str,
    source: str,
    role: RunCrawlUsageRole,
    ordinal: int,
    returned: bool,
) -> RunCrawlUsageRecord:
    return RunCrawlUsageRecord(
        usage_id=uuid5(
            run_manifest.run_id,
            f"usage\0{crawl.crawl_id}\0{normalized_url}\0{role}\0{ordinal}",
        ),
        run_id=run_manifest.run_id,
        crawl_id=crawl.crawl_id,
        document_id=crawl.document_id,
        requested_url=requested_url,
        normalized_url=normalized_url,
        source=source,
        role=role,
        ordinal=ordinal,
        returned=returned,
    )


async def record_existing_crawl_usage(
    *,
    session: Session,
    task_run_id: UUID,
    crawl: CrawlRecord,
    requested_url: str,
    role: RunCrawlUsageRole,
    ordinal: int,
    returned: bool,
    repository_pipeline: RepositoryPipeline,
    source: str = "repository",
) -> None:
    """Append a purpose-specific usage for an already committed crawl."""

    normalized_url = normalize_url(requested_url)
    manifest = _run_envelope(session, task_run_id=task_run_id).manifest
    usage = _run_usage_record(
        manifest,
        crawl=crawl,
        requested_url=requested_url,
        normalized_url=normalized_url,
        source=source,
        role=role,
        ordinal=ordinal,
        returned=returned,
    )
    commit_task_checkpoint(session)
    await repository_pipeline.submit_stored(
        crawl,
        request_id=run_usage_ingestion_request_id(usage.usage_id),
        run_manifest=manifest,
        run_usage=usage,
    )


async def _persist_page(
    session: Session,
    *,
    task_run_id: UUID,
    index: int,
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
    usage_role: RunCrawlUsageRole = "primitive_result",
    usage_ordinal: int | None = None,
    usage_returned: bool | None = None,
) -> CrawlPage:
    usage_ordinal = index if usage_ordinal is None else usage_ordinal
    normalized_url = normalize_url(requested_url)
    input_hash = _input_hash(normalized_url, mode, wait, run_config_overrides)
    crawl_payload = page.crawl or {}
    finished_at = _utc_now()
    duration_ms = int(page.duration_seconds * 1000)
    crawl_id = _durable_crawl_id(
        task_run_id=task_run_id,
        index=index,
        normalized_url=normalized_url,
        input_hash=input_hash,
    )

    async def persist_with(pipeline: RepositoryPipeline) -> CrawlPage:
        resumed = await _repository_retry_page(
            pipeline,
            session=session,
            crawl_id=crawl_id,
            task_run_id=task_run_id,
            requested_url=requested_url,
            normalized_url=normalized_url,
            input_hash=input_hash,
            progress_reporter=None,
            include_html=retain_html,
            include_links=include_links,
        )
        if resumed is not None:
            return resumed

        run_envelope = _run_envelope(session, task_run_id=task_run_id)
        run_manifest = run_envelope.manifest
        commit_task_checkpoint(session)

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
            run_id=task_run_id,
            task_id=run_manifest.task_id,
            task_revision=run_manifest.task_revision,
            primitive=run_manifest.primitive,
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
            data_schema_id=run_envelope.data_schema_id,
            query_schema_id=None,
            warnings_json=[_json_safe(warning) for warning in page.quality_warnings],
            errors_json=[error] if error else [],
        )
        if page.html is not None:
            await pipeline.store_raw(captured_html=page.html, identity=identity)
            if not retain_html:
                # Raw storage is the last operation that needs the captured string.
                # Drop this function's reference before ingestion backpressure and
                # structural reads so large pages do not accumulate in crawl workers.
                page = page.model_copy(update={"html": None})
        result_page = page
        run_usage = _run_usage_record(
            run_manifest,
            crawl=record,
            requested_url=requested_url,
            normalized_url=normalized_url,
            source="network",
            role=usage_role,
            ordinal=usage_ordinal,
            returned=page.success if usage_returned is None else usage_returned,
        )
        repository_result = await pipeline.submit_stored(
            record,
            run_manifest=run_manifest,
            run_usage=run_usage,
        )
        links = (
            await pipeline.projected_links(
                repository_result.document_id,
                page_url=crawl_payload.get("redirected_url") or page.url,
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
    task_run_id: UUID,
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
    usage_role: RunCrawlUsageRole = "primitive_result",
    usage_ordinal: int = 0,
    usage_returned: bool | None = None,
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

    run_manifest = _run_envelope(session, task_run_id=task_run_id).manifest
    run_usage = _run_usage_record(
        run_manifest,
        crawl=hit.crawl,
        requested_url=requested_url,
        normalized_url=normalized_url,
        source=cache_status,
        role=usage_role,
        ordinal=usage_ordinal,
        returned=hit.crawl.document_id is not None if usage_returned is None else usage_returned,
    )
    commit_task_checkpoint(session)
    usage_result = await repository_pipeline.submit_stored(
        hit.crawl,
        request_id=run_usage_ingestion_request_id(run_usage.usage_id),
        run_manifest=run_manifest,
        run_usage=run_usage,
    )

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
    ).model_copy(update={"repository_snapshot": usage_result.repository_snapshot})


async def _repository_retry_page(
    repository_pipeline: RepositoryPipeline,
    *,
    session: Session,
    crawl_id: UUID,
    task_run_id: UUID,
    requested_url: str,
    normalized_url: str,
    input_hash: str,
    progress_reporter: ProgressReporter | None,
    include_html: bool,
    include_links: bool,
    usage_role: RunCrawlUsageRole = "primitive_result",
    usage_ordinal: int = 0,
    usage_returned: bool | None = None,
) -> CrawlPage | None:
    """Resume a crawl already committed by an earlier attempt of this run."""

    hit = await repository_pipeline.resolve_crawl(
        crawl_id,
        include_html=include_html,
        include_links=include_links,
    )
    if hit is None:
        return None
    if (
        hit.crawl.run_id != task_run_id
        or hit.crawl.normalized_url != normalized_url
        or hit.crawl.input_hash != input_hash
    ):
        raise RuntimeError(
            f"crawl identity {crawl_id} resolved to incompatible durable provenance"
        )

    run_manifest = _run_envelope(session, task_run_id=task_run_id).manifest
    run_usage = _run_usage_record(
        run_manifest,
        crawl=hit.crawl,
        requested_url=requested_url,
        normalized_url=normalized_url,
        # A retry resumes the same network usage committed by this run; changing
        # the durable source would make the retry-stable usage identity conflict.
        source="network",
        role=usage_role,
        ordinal=usage_ordinal,
        returned=(
            hit.crawl.document_id is not None and not hit.crawl.errors_json
            if usage_returned is None
            else usage_returned
        ),
    )
    commit_task_checkpoint(session)
    usage_result = await repository_pipeline.submit_stored(
        hit.crawl,
        request_id=run_usage_ingestion_request_id(run_usage.usage_id),
        run_manifest=run_manifest,
        run_usage=run_usage,
    )

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
            message="Resumed the acquisition committed by an earlier task attempt.",
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
    ).model_copy(update={"repository_snapshot": usage_result.repository_snapshot})


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


def _default_max_concurrency_for_mode(mode: CrawlMode) -> int:
    return 5 if mode == "app" else 10


def _crawl_concurrency_per_run() -> int:
    try:
        return max(1, int(os.getenv("ATLAS_CRAWL_CONCURRENCY_PER_RUN", "3")))
    except ValueError:
        return 3


def _max_concurrency_from_config(config: dict[str, Any], mode: CrawlMode) -> int:
    raw_max_concurrency = config.get("max_concurrency")
    if isinstance(raw_max_concurrency, int) and raw_max_concurrency > 0:
        return raw_max_concurrency
    if isinstance(raw_max_concurrency, str) and raw_max_concurrency.isdigit():
        return max(1, int(raw_max_concurrency))
    return _default_max_concurrency_for_mode(mode)


def _cache_block_rules_from_config(config: dict[str, Any]) -> dict[str, Any]:
    cache_block_rules = config.get("cache_block_rules")
    return cache_block_rules if isinstance(cache_block_rules, dict) else {}


def _transport_from_policy(
    policy: CrawlPolicySnapshot,
) -> tuple[CrawlMode, CrawlWait, dict[str, Any], int, dict[str, Any]]:
    config = policy.config or {}
    mode = config.get("mode") or "static"
    wait = config.get("wait") or "none"
    run_config_overrides = config.get("run_config_overrides") or {}
    return (
        mode,
        wait,
        run_config_overrides,
        _max_concurrency_from_config(config, mode),
        _cache_block_rules_from_config(config),
    )


def _frozen_crawl_policy_for_url(
    session: Session,
    *,
    task_run_id: UUID,
    url: str,
) -> CrawlPolicySnapshot | None:
    run = session.get(TaskRun, task_run_id)
    if run is None:
        raise RuntimeError(f"Task run {task_run_id} does not exist")
    return find_crawl_policy_snapshot_for_url(
        run.crawl_policy_snapshots_json,
        url=url,
    )


async def crawl_one_for_task(
    *,
    url: str,
    mode: CrawlMode | None = None,
    wait: CrawlWait | None = None,
    index: int,
    progress_reporter: ProgressReporter | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
    run_config_overrides: dict[str, Any] | None = None,
    crawler_pool: _CrawlerPool | None = None,
    repository_pipeline: RepositoryPipeline | None = None,
    cache: CacheOptions | dict[str, Any] | None = None,
    retain_html: bool = True,
    include_links: bool = True,
    usage_role: RunCrawlUsageRole = "primitive_result",
    usage_ordinal: int | None = None,
    usage_returned: bool | None = None,
) -> CrawlPage:
    usage_ordinal = index if usage_ordinal is None else usage_ordinal
    cache_options = cache if isinstance(cache, CacheOptions) else CacheOptions.model_validate(cache or {})
    cache_block_rules: dict[str, Any] | None = None
    policy = (
        _frozen_crawl_policy_for_url(session, task_run_id=task_run_id, url=url)
        if session is not None and task_run_id is not None
        else None
    )
    if mode is None or wait is None:
        if session is None or task_run_id is None:
            raise RuntimeError("policy-driven crawl requires task-run execution context")
        if policy is None:
            mode, wait, run_config_overrides = "static", "none", {}
            cache_block_rules = {}
        else:
            (
                mode,
                wait,
                run_config_overrides,
                _,
                cache_block_rules,
            ) = _transport_from_policy(policy)
        commit_task_checkpoint(session)

    cache_policy = resolve_cache_policy(
        crawl_policy_config=policy.config if policy is not None else None,
        request=cache_options,
    )
    cache_now = _utc_now()
    fresh_after = cache_now - timedelta(seconds=cache_policy.max_age_seconds)
    if not cache_policy.reads_cache:
        crawl_metrics.repository_cache(outcome=cache_policy.mode)
    domain_group = policy.domain_group if policy is not None else "unclassified"
    acquisition_recorded = False
    acquisition_failure_duration = 0.0
    if session is not None and task_run_id is not None:
        commit_task_checkpoint(session)

    normalized_url = normalize_url(url)
    repository_input_hash = _input_hash(
        normalized_url,
        mode,
        wait,
        run_config_overrides,
    )
    durable_crawl_id = (
        _durable_crawl_id(
            task_run_id=task_run_id,
            index=index,
            normalized_url=normalized_url,
            input_hash=repository_input_hash,
        )
        if task_run_id is not None
        else None
    )

    async def stale_fallback(page: CrawlPage) -> CrawlPage:
        if (
            page.success
            or not cache_policy.reads_cache
            or cache_policy.stale_if_error_seconds is None
            or repository_pipeline is None
            or session is None
            or task_run_id is None
        ):
            return page

        stale_page = await _repository_cached_page(
            repository_pipeline,
            session=session,
            task_run_id=task_run_id,
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
            usage_role=usage_role,
            usage_ordinal=usage_ordinal,
            usage_returned=usage_returned,
        )
        if stale_page is None:
            return page
        return stale_page if retain_html else stale_page.model_copy(update={"html": None})

    if (
        session is not None
        and task_run_id is not None
        and repository_pipeline is not None
        and durable_crawl_id is not None
    ):
        resumed_page = await _repository_retry_page(
            repository_pipeline,
            session=session,
            crawl_id=durable_crawl_id,
            task_run_id=task_run_id,
            requested_url=url,
            normalized_url=normalized_url,
            input_hash=repository_input_hash,
            progress_reporter=progress_reporter,
            include_html=retain_html,
            include_links=include_links,
            usage_role=usage_role,
            usage_ordinal=usage_ordinal,
            usage_returned=usage_returned,
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
        session is not None
        and task_run_id is not None
        and repository_pipeline is not None
        and cache_policy.reads_cache
    ):
        commit_task_checkpoint(session)
        repository_page = await _repository_cached_page(
            repository_pipeline,
            session=session,
            task_run_id=task_run_id,
            requested_url=url,
            normalized_url=normalized_url,
            input_hash=repository_input_hash,
            cache_block_rules=cache_block_rules,
            progress_reporter=progress_reporter,
            captured_after=fresh_after,
            include_html=retain_html,
            include_links=include_links,
            usage_role=usage_role,
            usage_ordinal=usage_ordinal,
            usage_returned=usage_returned,
        )
        if repository_page is not None:
            crawl_metrics.page_acquisition(
                page=repository_page,
                duration_seconds=0.0,
                mode=mode,
                source="cache",
                domain_group=domain_group,
            )
            return (
                repository_page
                if retain_html
                else repository_page.model_copy(update={"html": None})
            )

    shared_crawler = (
        await crawler_pool.get(mode, resource_url=url)
        if crawler_pool is not None
        else None
    )

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
        if shared_crawler is not None:
            return await load_with(shared_crawler)
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

    if session is not None and task_run_id is not None:
        try:
            async with capacity_lease(
                session,
                task_run_id=task_run_id,
                url=url,
                policy=policy,
                progress_reporter=progress_reporter,
                include_browser=shared_crawler is None,
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
    else:
        page, reused_cache = await measured_load_page()

    if (
        session is not None
        and task_run_id is not None
        and not reused_cache
        and cache_policy.stores_result
    ):
        page = await _persist_page(
            session,
            task_run_id=task_run_id,
            index=index,
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
            usage_role=usage_role,
            usage_ordinal=usage_ordinal,
            usage_returned=usage_returned,
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


async def _emit_crawl_batch_checkpoint(
    *,
    progress_reporter: ProgressReporter | None,
    operation_id: str,
    pages_by_index: dict[int, CrawlPage],
    total: int,
    latest_page: CrawlPage,
) -> None:
    completed = len(pages_by_index)
    if completed >= total:
        return

    step = 1 if total <= 20 else 5 if total <= 100 else 10
    if completed > 3 and completed % step != 0:
        return

    succeeded = sum(1 for page in pages_by_index.values() if page.success)
    failed = completed - succeeded
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            operation_id=operation_id,
            phase="crawl_batch",
            status="started",
            resource=latest_page.url,
            current=completed,
            total=total,
            message=f"Crawled {completed} of {total} pages.",
            metadata={"succeeded": succeeded, "failed": failed},
        ),
    )


def repository_required_for_urls(
    *,
    urls: list[str],
    cache: CacheOptions,
    session: Session | None,
    task_run_id: UUID | None,
) -> bool:
    """Return whether any URL may read or write durable repository state."""

    if cache.mode == "no_store":
        return False
    if session is None or task_run_id is None:
        return True
    return any(
        resolve_cache_policy(
            crawl_policy_config=(
                snapshot.config
                if (
                    snapshot := _frozen_crawl_policy_for_url(
                        session, task_run_id=task_run_id, url=url
                    )
                ) is not None
                else None
            ),
            request=cache,
        ).mode
        != "no_store"
        for url in urls
    )


async def crawl(
    urls: list[str],
    mode: CrawlMode | None = None,
    wait: CrawlWait | None = None,
    progress_reporter: ProgressReporter | None = None,
    session: Session | None = None,
    task_run_id: UUID | None = None,
    cache: CacheOptions | dict[str, Any] | None = None,
    page_consumer: Callable[[int, str, CrawlPage], Awaitable[None]] | None = None,
    retain_pages: bool = True,
    include_links: bool = True,
    repository_pipeline: RepositoryPipeline | None = None,
    usage_role: RunCrawlUsageRole = "primitive_result",
    usage_ordinals: list[int] | None = None,
    usage_returned: bool | None = None,
) -> CrawlOutput:
    if (mode is None) != (wait is None):
        raise ValueError("mode and wait must either both be provided or both be policy-driven")
    if usage_ordinals is not None and len(usage_ordinals) != len(urls):
        raise ValueError("usage_ordinals must contain one ordinal per URL")

    start_time = time.perf_counter()
    batch_operation_id = f"{task_run_id or 'crawl'}:batch"
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            operation_id=batch_operation_id,
            phase="crawl_batch",
            status="started",
            current=0,
            total=len(urls),
            message=f"Crawling {len(urls)} page{'s' if len(urls) != 1 else ''}.",
        ),
    )
    pages_by_index: dict[int, CrawlPage] = {}
    work: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
    for item in enumerate(urls):
        work.put_nowait(item)

    if session is not None and task_run_id is not None:
        commit_task_checkpoint(session)
        worker_session_factory = sessionmaker(
            bind=session.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        )
    else:
        worker_session_factory = None

    async def crawl_worker(
        crawler_pool: _CrawlerPool,
        repository_pipeline: RepositoryPipeline | None,
    ) -> None:
        worker_session = worker_session_factory() if worker_session_factory is not None else None
        try:
            while True:
                try:
                    index, url = work.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    page = await crawl_one_for_task(
                        url=url,
                        mode=mode,
                        wait=wait,
                        index=index,
                        progress_reporter=progress_reporter,
                        session=worker_session,
                        task_run_id=task_run_id,
                        crawler_pool=crawler_pool,
                        repository_pipeline=repository_pipeline,
                        cache=cache,
                        retain_html=retain_pages,
                        include_links=include_links,
                        usage_role=usage_role,
                        usage_ordinal=(usage_ordinals[index] if usage_ordinals is not None else index),
                        usage_returned=usage_returned,
                    )
                    if page_consumer is not None:
                        await page_consumer(index, url, page)
                    pages_by_index[index] = (
                        page
                        if retain_pages
                        else page.model_copy(update={"html": None, "crawl": None})
                    )
                    await _emit_crawl_batch_checkpoint(
                        progress_reporter=progress_reporter,
                        operation_id=batch_operation_id,
                        pages_by_index=pages_by_index,
                        total=len(urls),
                        latest_page=page,
                    )
                finally:
                    work.task_done()
        finally:
            if worker_session is not None:
                worker_session.close()

    worker_count = min(len(urls), _crawl_concurrency_per_run())
    explicit_cache = (
        cache if isinstance(cache, CacheOptions) else CacheOptions.model_validate(cache or {})
    )
    repository_required = repository_required_for_urls(
        urls=urls,
        cache=explicit_cache,
        session=session,
        task_run_id=task_run_id,
    )
    repository_ingestor = (
        repository_ingestor_from_env()
        if session is not None
        and task_run_id is not None
        and repository_pipeline is None
        and repository_required
        else None
    )
    async with AsyncExitStack() as stack:
        active_repository_pipeline = (
            await stack.enter_async_context(RepositoryPipeline(repository_ingestor))
            if repository_ingestor is not None
            else repository_pipeline
        )
        crawler_pool = await stack.enter_async_context(_CrawlerPool(
            session=session if task_run_id is not None else None,
            task_run_id=task_run_id,
            progress_reporter=progress_reporter,
        ))
        workers = [
            asyncio.create_task(crawl_worker(crawler_pool, active_repository_pipeline))
            for _ in range(worker_count)
        ]
        try:
            await asyncio.gather(*workers)
        except BaseException:
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise

    pages = [pages_by_index[index] for index in range(len(urls))]
    succeeded = sum(1 for page in pages if page.success)
    failed = len(pages) - succeeded
    await emit_progress(
        progress_reporter,
        ProgressEvent(
            operation_id=batch_operation_id,
            phase="crawl_batch",
            status="succeeded" if failed == 0 else "failed",
            current=len(pages),
            total=len(urls),
            message=f"Crawled {len(pages)} pages: {succeeded} succeeded, {failed} failed.",
            duration=time.perf_counter() - start_time,
            metadata={"succeeded": succeeded, "failed": failed},
            error=f"{failed} pages failed." if failed else None,
        ),
    )
    return CrawlOutput(
        stats=CrawlStats(
            requested_urls=len(urls),
            succeeded=succeeded,
            failed=failed,
            duration_seconds=time.perf_counter() - start_time,
        ),
        pages=pages,
    )


def crawl_sync(
    urls: list[str],
    mode: CrawlMode | None = None,
    wait: CrawlWait | None = None,
    progress_reporter: ProgressReporter | None = None,
    cache: CacheOptions | dict[str, Any] | None = None,
) -> CrawlOutput:
    return asyncio.run(
        crawl(
            urls=urls,
            mode=mode,
            wait=wait,
            progress_reporter=progress_reporter,
            cache=cache,
        )
    )
