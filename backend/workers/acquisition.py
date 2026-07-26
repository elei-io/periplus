"""Standard-CDP Atlas acquisition worker runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from nats.errors import TimeoutError as NatsTimeoutError
from playwright.async_api import Playwright, async_playwright
from pydantic import ValidationError

from acquisition.errors import (
    PlaywrightRuntimeLost,
    RetryableAcquisitionFailure,
)
from acquisition.models import AcquisitionResult
from acquisition.service import acquire_page
from config import get_float, get_int, get_optional
from config.performance import (
    CRAWL_ACQUISITION_LANES,
    CRAWL_DISPATCH_WINDOW,
    CRAWL_DOMAIN_PERMIT_RETRY_SECONDS,
    GRAPH_ACK_WAIT_SECONDS,
)
from control.crawl_policies.schemas import EffectivePolicySnapshot
from observability import crawl_metrics, navigation_metrics
from repository.ingestion.acquisition import AcquisitionPipeline
from repository.ingestion.health import HealthMonitor
from runtime.context import GraphExecutionContext
from runtime.domain_pacing import (
    DomainCapacityUnavailable,
    DomainPermitLost,
    domain_backoff_seconds,
    domain_permit,
    ensure_domain_pacing_storage,
)
from runtime.graph_navigation import run as run_graph_navigation
from runtime.graph_outbox import run_outbox_relay
from runtime.graph_queue import (
    CRAWL_CONSUMER,
    CRAWL_SUBJECT,
    GRAPH_STREAM,
    CrawlRequest,
    CrawlWork,
    NavigationReadinessWork,
    WorkerState,
    ensure_graph_storage,
    get_crawl_request,
    get_graph_run,
    list_graph_runs,
    update_crawl_request,
)
from runtime.graph_runs import (
    DatabasePolicySnapshotResolver,
    expire_graph_run,
    fill_root_admissions,
    settle_request,
)
from runtime.nats_client import connect_nats
from runtime.navigation import (
    build_navigation_package,
    navigation_event_id,
    navigation_object_name,
    put_navigation_package,
)
from runtime.navigation_contract import NavigationPackage
from workers.lifecycle import (
    WorkerEndpointConfig,
    WorkerEndpoints,
    install_signal_handlers,
)

logger = logging.getLogger(__name__)


class AcquisitionClaimLost(RuntimeError):
    """The delivery or crawl-request claim was lost before work could settle."""


async def _watch_playwright_driver(playwright_context) -> None:
    """Fail the worker when Playwright's local Node driver transport exits."""

    try:
        await asyncio.shield(playwright_context._connection._transport.on_error_future)
    except asyncio.CancelledError:
        raise
    except BaseException as exc:
        raise PlaywrightRuntimeLost("local Playwright driver process exited") from exc
    raise PlaywrightRuntimeLost("local Playwright driver process exited")


async def _fill_root_window(
    *, runs, requests, progress, jetstream, run_id: UUID
) -> int:
    run = await get_graph_run(runs, run_id)
    if run is None:
        return 0
    resolver = DatabasePolicySnapshotResolver()
    await asyncio.to_thread(resolver.prepare, run.trigger_urls)
    return await fill_root_admissions(
        runs=runs,
        requests=requests,
        progress=progress,
        jetstream=jetstream,
        run_id=run_id,
        policy_resolver=resolver,
    )


@dataclass(slots=True)
class _BufferedCrawl:
    message: Any
    request: CrawlRequest
    hostname: str
    domain_concurrency: int
    attempt_number: int
    domain_policy_valid: bool = True


class _HostnameDispatchBuffer:
    """Bounded run-fair look-ahead with per-host FIFO ordering."""

    def __init__(self, maximum_size: int) -> None:
        if maximum_size < 1:
            raise ValueError("dispatch buffer size must be positive")
        self.maximum_size = maximum_size
        self._queues: dict[UUID, dict[str, deque[_BufferedCrawl]]] = {}
        self._run_rotation: deque[UUID] = deque()
        self._host_rotations: dict[UUID, deque[str]] = {}
        self._retry_after: dict[str, float] = {}
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def add(self, item: _BufferedCrawl) -> None:
        if self._size >= self.maximum_size:
            raise RuntimeError("acquisition dispatch buffer is full")
        run_id = item.request.graph_run_id
        run_queues = self._queues.get(run_id)
        if run_queues is None:
            run_queues = {}
            self._queues[run_id] = run_queues
            self._host_rotations[run_id] = deque()
            self._run_rotation.append(run_id)
        queue = run_queues.get(item.hostname)
        if queue is None:
            queue = deque()
            run_queues[item.hostname] = queue
            self._host_rotations[run_id].append(item.hostname)
        queue.append(item)
        self._size += 1

    def defer_hostname(self, hostname: str, *, retry_at: float) -> None:
        if any(hostname in queues for queues in self._queues.values()):
            self._retry_after[hostname] = max(
                retry_at,
                self._retry_after.get(hostname, retry_at),
            )

    def pop(
        self,
        *,
        excluded_hostnames: set[str] | None = None,
        now: float | None = None,
    ) -> _BufferedCrawl | None:
        excluded = excluded_hostnames or set()
        current_time = time.monotonic() if now is None else now
        for _ in range(len(self._run_rotation)):
            run_id = self._run_rotation.popleft()
            run_queues = self._queues[run_id]
            host_rotation = self._host_rotations[run_id]
            for _ in range(len(host_rotation)):
                hostname = host_rotation.popleft()
                retry_at = self._retry_after.get(hostname)
                if hostname in excluded or (
                    retry_at is not None and retry_at > current_time
                ):
                    host_rotation.append(hostname)
                    continue
                self._retry_after.pop(hostname, None)
                queue = run_queues[hostname]
                item = queue.popleft()
                self._size -= 1
                if queue:
                    host_rotation.append(hostname)
                else:
                    del run_queues[hostname]
                    if not any(hostname in queues for queues in self._queues.values()):
                        self._retry_after.pop(hostname, None)
                if run_queues:
                    self._run_rotation.append(run_id)
                else:
                    del self._queues[run_id]
                    del self._host_rotations[run_id]
                return item
            self._run_rotation.append(run_id)
        return None

    def messages(self) -> list[Any]:
        return [
            item.message
            for run_id in self._run_rotation
            for hostname in self._host_rotations[run_id]
            for item in self._queues[run_id][hostname]
        ]

    def drain(self) -> list[_BufferedCrawl]:
        items: list[_BufferedCrawl] = []
        self._retry_after.clear()
        while (item := self.pop()) is not None:
            items.append(item)
        return items


class _PreAcquiredDomainPermit:
    """Adopt an already-entered permit context at the acquisition boundary."""

    def __init__(self, context, guard) -> None:
        self._context = context
        self._guard = guard
        self._entered = False
        self._released = False

    async def __aenter__(self):
        if self._entered or self._released:
            raise RuntimeError("domain permit can only be consumed once")
        self._entered = True
        return self._guard

    async def __aexit__(self, exc_type, exc, traceback):
        self._released = True
        return await self._context.__aexit__(exc_type, exc, traceback)

    async def release_if_unused(self) -> None:
        if self._released:
            return
        self._released = True
        await self._context.__aexit__(None, None, None)


async def _classify_crawl_message(message, requests) -> _BufferedCrawl | None:
    try:
        work = CrawlWork.model_validate_json(message.data)
    except ValidationError:
        logger.exception("discarding invalid acquisition work")
        await message.term()
        return None
    request = await get_crawl_request(requests, work.crawl_request_id)
    if request is None or request.status in {"completed", "failed", "cancelled"}:
        await message.ack()
        return None
    if request.generation != work.generation:
        await message.ack()
        return None
    try:
        effective = EffectivePolicySnapshot.model_validate(
            request.effective_policy_snapshot_json
        )
        hostname = (urlsplit(request.url).hostname or "unknown").lower()
    except ValidationError, ValueError:
        # Let the authoritative processor record malformed frozen work through
        # its existing retry and terminal-failure path.
        hostname = f"invalid-{request.id.hex}"
        concurrency = 1
        domain_policy_valid = False
    else:
        concurrency = effective.domain.maximum_concurrency
        domain_policy_valid = True
    return _BufferedCrawl(
        message=message,
        request=request,
        hostname=hostname,
        domain_concurrency=concurrency,
        attempt_number=len(request.acquisition_attempts_json) + 1,
        domain_policy_valid=domain_policy_valid,
    )


async def _try_domain_permit(domain_pacing, item: _BufferedCrawl):
    if domain_pacing is None or not item.domain_policy_valid:
        return None
    context = domain_permit(
        domain_pacing,
        domain=item.hostname,
        concurrency=item.domain_concurrency,
        acquire_timeout=0,
    )
    guard = await context.__aenter__()
    return _PreAcquiredDomainPermit(context, guard)


async def _process_dispatched_crawl(
    item: _BufferedCrawl,
    runs,
    requests,
    progress,
    repository_pipeline,
    domain_pacing,
    jetstream,
    *,
    playwright: Playwright,
    domain_permit: _PreAcquiredDomainPermit | None,
) -> None:
    try:
        await _process_crawl(
            item.message,
            runs,
            requests,
            progress,
            repository_pipeline,
            domain_pacing,
            jetstream,
            playwright=playwright,
            domain_permit=domain_permit,
        )
    finally:
        if domain_permit is not None:
            try:
                await domain_permit.release_if_unused()
            except DomainPermitLost:
                pass


async def _dispatch_buffered_crawls(
    buffer: _HostnameDispatchBuffer,
    active: set[asyncio.Task],
    *,
    capacity: int,
    runs,
    requests,
    progress,
    repository_pipeline,
    domain_pacing,
    jetstream,
    playwright: Playwright,
) -> bool:
    launched = False
    blocked_hostnames: set[str] = set()
    while len(active) < capacity and len(buffer) > 0:
        item = buffer.pop(excluded_hostnames=blocked_hostnames)
        if item is None:
            break
        try:
            backoff = await domain_backoff_seconds(
                domain_pacing,
                domain=item.hostname,
            )
            if backoff > 0:
                buffer.add(item)
                buffer.defer_hostname(
                    item.hostname,
                    retry_at=time.monotonic() + backoff,
                )
                blocked_hostnames.add(item.hostname)
                continue
            acquired_domain_permit = await _try_domain_permit(domain_pacing, item)
        except DomainCapacityUnavailable:
            buffer.add(item)
            buffer.defer_hostname(
                item.hostname,
                retry_at=time.monotonic() + CRAWL_DOMAIN_PERMIT_RETRY_SECONDS,
            )
            blocked_hostnames.add(item.hostname)
            continue
        except Exception:
            buffer.add(item)
            buffer.defer_hostname(
                item.hostname,
                retry_at=time.monotonic() + CRAWL_DOMAIN_PERMIT_RETRY_SECONDS,
            )
            blocked_hostnames.add(item.hostname)
            logger.exception(
                "domain admission failed before acquisition dispatch",
                extra={
                    "crawl_request_id": str(item.request.id),
                    "hostname": item.hostname,
                },
            )
            continue
        task = asyncio.create_task(
            _process_dispatched_crawl(
                item,
                runs,
                requests,
                progress,
                repository_pipeline,
                domain_pacing,
                jetstream,
                playwright=playwright,
                domain_permit=acquired_domain_permit,
            )
        )
        active.add(task)
        launched = True
    return launched


async def _keep_buffered_deliveries_alive(
    buffer: _HostnameDispatchBuffer,
) -> None:
    interval = max(1.0, GRAPH_ACK_WAIT_SECONDS / 3)
    while True:
        await asyncio.sleep(interval)
        messages = buffer.messages()
        if not messages:
            continue
        results = await asyncio.gather(
            *(message.in_progress() for message in messages),
            return_exceptions=True,
        )
        failures = sum(isinstance(result, BaseException) for result in results)
        if failures:
            logger.warning(
                "failed to heartbeat %d buffered acquisition deliveries", failures
            )


async def _release_buffered_deliveries(buffer: _HostnameDispatchBuffer) -> None:
    items = buffer.drain()
    if not items:
        return
    await asyncio.gather(
        *(item.message.nak() for item in items),
        return_exceptions=True,
    )


async def _monitor_event_loop(monitor: HealthMonitor) -> None:
    """Keep liveness independent from NATS presence and recovery work."""

    while True:
        monitor.heartbeat()
        await asyncio.sleep(1)


async def _wait_until_stopped(stop: asyncio.Event, delay: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=delay)
    except TimeoutError:
        pass


async def _run_presence_until_stopped(
    *,
    stop: asyncio.Event,
    monitor: HealthMonitor,
    iteration: Callable[[], Awaitable[None]],
    interval_seconds: float = 5.0,
    timeout_seconds: float = 15.0,
    retry_initial_seconds: float = 1.0,
) -> None:
    """Retry transient presence failures without silently losing capacity."""

    retry_delay = retry_initial_seconds
    while not stop.is_set():
        try:
            async with asyncio.timeout(timeout_seconds):
                await iteration()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            monitor.subsystem_unavailable("presence", str(exc) or type(exc).__name__)
            logger.exception(
                "acquisition presence unavailable; retrying in %.1fs",
                retry_delay,
            )
            await _wait_until_stopped(stop, retry_delay)
            retry_delay = min(30.0, max(1.0, retry_delay * 2))
        else:
            monitor.subsystem_ready("presence")
            retry_delay = retry_initial_seconds
            await _wait_until_stopped(stop, interval_seconds)


def _raise_background_failure(tasks: tuple[asyncio.Task, ...]) -> None:
    for task in tasks:
        if not task.done():
            continue
        name = task.get_name()
        if task.cancelled():
            raise RuntimeError(f"{name} was cancelled unexpectedly")
        error = task.exception()
        if error is None:
            raise RuntimeError(f"{name} exited unexpectedly")
        if isinstance(error, PlaywrightRuntimeLost):
            raise error
        raise RuntimeError(f"{name} failed") from error


async def _derive_navigation_package(
    page: AcquisitionResult,
    *,
    graph_run_id: UUID,
    crawl_request_id: UUID,
    repository_pipeline: AcquisitionPipeline,
) -> NavigationPackage:
    if page.content_sha256 is None or page.html is None:
        raise ValueError(
            "navigation package requires retained HTML identity and content"
        )
    generation_started = time.perf_counter()
    try:
        payload, row_count = await asyncio.to_thread(
            build_navigation_package,
            page.html,
            content_sha256=page.content_sha256,
            page_url=page.url,
        )
    except BaseException:
        navigation_metrics.package(
            phase="generation",
            outcome="failed",
            duration_seconds=time.perf_counter() - generation_started,
        )
        raise
    navigation_metrics.package(
        phase="generation",
        outcome="succeeded",
        duration_seconds=time.perf_counter() - generation_started,
        rows=row_count,
        byte_count=len(payload),
    )

    write_started = time.perf_counter()
    try:
        object_name = navigation_object_name(
            graph_run_id,
            page.content_sha256,
            page.url,
        )

        def write_package() -> NavigationPackage:
            return put_navigation_package(
                repository_pipeline.html_repository.store,
                name=object_name,
                payload=payload,
                row_count=row_count,
            )

        package = await asyncio.to_thread(write_package)
    except BaseException:
        navigation_metrics.package(
            phase="write",
            outcome="failed",
            duration_seconds=time.perf_counter() - write_started,
        )
        raise
    navigation_metrics.package(
        phase="write",
        outcome="succeeded",
        duration_seconds=time.perf_counter() - write_started,
    )
    return package


async def _keep_alive(message, requests, request_id: UUID, claim_token: UUID) -> None:
    interval = max(1.0, GRAPH_ACK_WAIT_SECONDS / 3)
    while True:
        await asyncio.sleep(interval)
        await message.in_progress()
        expires_at = datetime.now(UTC) + timedelta(seconds=GRAPH_ACK_WAIT_SECONDS * 2)

        def refresh(
            current: CrawlRequest,
            claim_expires_at: datetime = expires_at,
        ) -> CrawlRequest:
            if current.status != "crawling" or current.claim_token != claim_token:
                return current
            return current.model_copy(update={"claim_expires_at": claim_expires_at})

        current = await update_crawl_request(requests, request_id, refresh)
        if current.claim_token != claim_token:
            return


async def _release_crawl_for_redelivery(
    *,
    message,
    requests,
    progress,
    request_id: UUID,
    claim_token: UUID,
    delay: float,
) -> None:
    released = False

    def release_claim(current: CrawlRequest) -> CrawlRequest:
        nonlocal released
        released = False
        if current.status != "crawling" or current.claim_token != claim_token:
            return current
        released = True
        return current.model_copy(
            update={
                "status": "queued",
                "claim_token": None,
                "claim_expires_at": None,
                "updated_at": datetime.now(UTC),
            }
        )

    await update_crawl_request(requests, request_id, release_claim)
    await message.nak(delay=delay)


async def _process_crawl(
    message,
    runs,
    requests,
    progress,
    repository_pipeline,
    domain_pacing=None,
    jetstream=None,
    *,
    playwright: Playwright,
    domain_permit=None,
) -> None:
    try:
        work = CrawlWork.model_validate_json(message.data)
    except ValidationError:
        logger.exception("discarding invalid acquisition work")
        await message.term()
        return
    request = await get_crawl_request(requests, work.crawl_request_id)
    if request is None or request.status in {"completed", "failed", "cancelled"}:
        await message.ack()
        return
    if request.generation != work.generation:
        await message.ack()
        return
    claimed = False
    now = datetime.now(UTC)
    claim_token = uuid4()
    previous_status: str | None = None

    def claim(current: CrawlRequest) -> CrawlRequest:
        nonlocal claimed, previous_status
        claimed = False
        previous_status = current.status
        reclaimable = (
            current.status == "crawling"
            and current.claim_expires_at is not None
            and current.claim_expires_at <= now
        )
        if current.status != "queued" and not reclaimable:
            return current
        claimed = True
        return current.model_copy(
            update={
                "status": "crawling",
                "claim_token": claim_token,
                "claim_expires_at": now + timedelta(seconds=GRAPH_ACK_WAIT_SECONDS * 2),
                "updated_at": now,
            }
        )

    request = await update_crawl_request(requests, request.id, claim)
    if not claimed:
        if request.status == "crawling":
            await message.nak(delay=1)
        else:
            await message.ack()
        return
    run = await get_graph_run(runs, request.graph_run_id)
    if run is None:
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="cancelled",
            error="Graph run is no longer active.",
            expected_claim_token=claim_token,
        )
        await message.ack()
        return
    if run.status in {"failed", "cancelled", "completed", "completed_with_errors"}:
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="cancelled",
            error="Graph run is no longer active.",
            expected_claim_token=claim_token,
        )
        await message.ack()
        return
    if run.status == "paused":
        await _release_crawl_for_redelivery(
            message=message,
            requests=requests,
            progress=progress,
            request_id=request.id,
            claim_token=claim_token,
            delay=30,
        )
        return

    heartbeat = asyncio.create_task(
        _keep_alive(message, requests, request.id, claim_token)
    )
    acquisition = None
    max_deliver = get_int("ATLAS_CRAWL_MAX_DELIVER")
    try:
        context = GraphExecutionContext(
            graph_id=run.graph_id,
            graph_run_id=run.id,
            graph_node_id=request.node_id,
            crawl_request_id=request.id,
            admitted_at=request.created_at,
            effective_policy_snapshot_json=request.effective_policy_snapshot_json,
            prior_attempts_json=request.acquisition_attempts_json,
            source_crawl_id=request.source_crawl_id,
            source_edge_id=request.source_edge_id,
        )
        has_outgoing_edges = any(
            edge.source_node_id == request.node_id for edge in run.snapshot.edges
        )
        acquisition = asyncio.create_task(
            acquire_page(
                url=request.url,
                context=context,
                playwright=playwright,
                domain_pacing=domain_pacing,
                domain_permit=domain_permit,
                repository_pipeline=repository_pipeline,
                persist_retryable_failure=(
                    request.processing_failure_count + 1 >= max_deliver
                ),
                include_html=has_outgoing_edges,
            )
        )
        done, _pending = await asyncio.wait(
            (acquisition, heartbeat), return_when=asyncio.FIRST_COMPLETED
        )
        if heartbeat in done:
            acquisition.cancel()
            await asyncio.gather(acquisition, return_exceptions=True)
            if heartbeat.cancelled():
                raise AcquisitionClaimLost("acquisition claim heartbeat was cancelled")
            heartbeat_error = heartbeat.exception()
            if heartbeat_error is not None:
                raise AcquisitionClaimLost(
                    "acquisition claim heartbeat failed"
                ) from heartbeat_error
            raise AcquisitionClaimLost("acquisition claim ownership was lost")
        page = await acquisition
        if page.visit_id != request.id:
            raise RuntimeError(
                f"crawl request {request.id} returned unexpected visit id {page.visit_id}"
            )
        if not page.success:
            await settle_request(
                runs=runs,
                requests=requests,
                progress=progress,
                request_id=request.id,
                status="failed",
                error=page.error or "Page acquisition failed.",
                expected_claim_token=claim_token,
                failure_stage=page.failure_stage or "acquisition",
                failure_code=page.failure_code or "acquisition_failed",
                status_code=page.status_code,
            )
            await message.ack()
            return

        navigation = (
            await _derive_navigation_package(
                page,
                graph_run_id=run.id,
                crawl_request_id=request.id,
                repository_pipeline=repository_pipeline,
            )
            if has_outgoing_edges and page.content_sha256 is not None
            else None
        )
        identity = navigation.sha256 if navigation is not None else "contentless"
        readiness = NavigationReadinessWork(
            event_id=navigation_event_id(request.id, identity),
            crawl_id=request.id,
            graph_run_id=run.id,
            crawl_request_id=request.id,
            generation=request.generation,
            navigation=navigation,
            occurred_at=datetime.now(UTC),
        )
        request, transitioned_to_navigation = await runs.complete_acquisition(
            request_id=request.id,
            generation=request.generation,
            claim_token=claim_token,
            content_sha256=page.content_sha256,
            acquisition_attempts=request.acquisition_attempts_json,
            readiness=readiness,
        )
        if transitioned_to_navigation:
            try:
                await _fill_root_window(
                    runs=runs,
                    requests=requests,
                    progress=progress,
                    jetstream=jetstream,
                    run_id=run.id,
                )
            except Exception:
                logger.warning(
                    "root admission refill failed for graph run %s",
                    run.id,
                    exc_info=True,
                )
        await message.ack()
        return
    except asyncio.CancelledError:
        try:
            await _release_crawl_for_redelivery(
                message=message,
                requests=requests,
                progress=progress,
                request_id=request.id,
                claim_token=claim_token,
                delay=1,
            )
        except Exception:
            logger.exception(
                "failed to release cancelled crawl acquisition for redelivery",
                extra={
                    "crawl_request_id": str(request.id),
                    "graph_run_id": str(request.graph_run_id),
                    "url": request.url,
                },
            )
        raise
    except Exception as exc:
        worker_fatal = isinstance(exc, PlaywrightRuntimeLost)
        infrastructure_failure = isinstance(
            exc,
            (
                AcquisitionClaimLost,
                NatsTimeoutError,
                OSError,
                PlaywrightRuntimeLost,
                DomainCapacityUnavailable,
                DomainPermitLost,
            ),
        )
        failure_count = request.processing_failure_count
        logger.exception(
            "crawl acquisition attempt failed",
            extra={
                "crawl_request_id": str(request.id),
                "graph_run_id": str(request.graph_run_id),
                "url": request.url,
                "infrastructure_failure": infrastructure_failure,
                "processing_failure_count": failure_count,
            },
        )
        if not infrastructure_failure:
            failure = exc

            def record_failure(current: CrawlRequest) -> CrawlRequest:
                if current.status != "crawling" or current.claim_token != claim_token:
                    return current
                attempts = current.acquisition_attempts_json
                if (
                    isinstance(failure, RetryableAcquisitionFailure)
                    and failure.result.attempt_evidence is not None
                ):
                    attempts = (
                        *attempts,
                        failure.result.attempt_evidence.model_dump(mode="json"),
                    )
                return current.model_copy(
                    update={
                        "processing_failure_count": current.processing_failure_count
                        + 1,
                        "acquisition_attempts_json": attempts,
                    }
                )

            request = await update_crawl_request(requests, request.id, record_failure)
            failure_count = request.processing_failure_count

        if infrastructure_failure or failure_count < max_deliver:
            retry_after = (
                exc.retry_after_seconds
                if isinstance(exc, RetryableAcquisitionFailure)
                else None
            )
            delay = (
                retry_after
                if retry_after is not None
                else min(30, 2 ** max(0, failure_count - 1))
            )
            await _release_crawl_for_redelivery(
                message=message,
                requests=requests,
                progress=progress,
                request_id=request.id,
                claim_token=claim_token,
                delay=delay,
            )
            if worker_fatal:
                raise
            return
        failure_page = (
            exc.result if isinstance(exc, RetryableAcquisitionFailure) else None
        )
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="failed",
            error=str(exc),
            expected_claim_token=claim_token,
            failure_stage=(
                failure_page.failure_stage
                if failure_page is not None and failure_page.failure_stage
                else "acquisition"
            ),
            failure_code=(
                failure_page.failure_code
                if failure_page is not None and failure_page.failure_code
                else "acquisition_processing_failed"
            ),
            status_code=(
                failure_page.status_code if failure_page is not None else None
            ),
        )
        await message.ack()
    finally:
        if acquisition is not None and not acquisition.done():
            acquisition.cancel()
            await asyncio.gather(acquisition, return_exceptions=True)
        heartbeat.cancel()
        await asyncio.gather(heartbeat, return_exceptions=True)


async def run() -> None:
    stop = asyncio.Event()
    install_signal_handlers(stop)

    worker_id = get_optional("ATLAS_ACQUISITION_WORKER_ID") or (
        f"acquisition:{os.uname().nodename}:{os.getpid()}"
    )
    capacity = CRAWL_ACQUISITION_LANES
    endpoints = WorkerEndpoints(WorkerEndpointConfig.from_env("acquisition"))
    endpoints.start_metrics()

    client = await connect_nats()
    jetstream = client.jetstream()
    runs, requests, workers = await ensure_graph_storage(jetstream)
    progress = None
    domain_pacing = await ensure_domain_pacing_storage(jetstream)
    for active_run in await list_graph_runs(runs):
        if active_run.status in {"queued", "running"}:
            await _fill_root_window(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                run_id=active_run.id,
            )
    crawl_subscription = await jetstream.pull_subscribe(
        CRAWL_SUBJECT,
        durable=CRAWL_CONSUMER,
        stream=GRAPH_STREAM,
    )
    active: set[asyncio.Task] = set()
    dispatch_buffer = _HostnameDispatchBuffer(CRAWL_DISPATCH_WINDOW)
    buffered_heartbeat_task = asyncio.create_task(
        _keep_buffered_deliveries_alive(dispatch_buffer),
        name="acquisition-buffer-heartbeats",
    )
    started = datetime.now(UTC)
    monitor = HealthMonitor(
        heartbeat_timeout_seconds=get_float(
            "ATLAS_ACQUISITION_WORKER_HEALTH_HEARTBEAT_TIMEOUT_SECONDS"
        )
    )
    monitor.dependencies_ready()
    monitor.subsystem_ready("acquisition")
    monitor.subsystem_unavailable("presence", "starting")
    endpoints.start_health(monitor)

    event_loop_heartbeat_task = asyncio.create_task(
        _monitor_event_loop(monitor),
        name="acquisition-event-loop-heartbeat",
    )
    graph_navigation_task = asyncio.create_task(
        _run_graph_navigation_until_stopped(monitor),
        name="acquisition-graph-navigation",
    )
    graph_outbox_task = asyncio.create_task(
        run_outbox_relay(runs, jetstream, stop=stop),
        name="acquisition-graph-outbox",
    )

    async def release_dispatch_buffer() -> None:
        await _release_buffered_deliveries(dispatch_buffer)
        buffered_heartbeat_task.cancel()
        await asyncio.gather(buffered_heartbeat_task, return_exceptions=True)

    pending_since: float | None = None

    async def publish_presence() -> None:
        nonlocal pending_since
        now = datetime.now(UTC)
        observed_at = time.monotonic()
        state = WorkerState(
            worker_id=worker_id,
            started_at=started,
            last_seen_at=now,
            capacity=capacity,
            active_request_count=len(active),
            stopping=False,
        )
        await workers.put(worker_id.replace(":", "-"), state.model_dump_json().encode())
        consumer = await jetstream.consumer_info(GRAPH_STREAM, CRAWL_CONSUMER)
        pending = max(0, consumer.num_pending) + max(0, consumer.num_ack_pending)
        if pending > 0:
            pending_since = pending_since or observed_at
        else:
            pending_since = None
        marker = (
            consumer.ack_floor.stream_seq,
            consumer.ack_floor.consumer_seq,
            consumer.num_pending,
            consumer.num_ack_pending,
        )
        monitor.queue_observed(
            "crawl_acquisition",
            pending=pending,
            progress_marker=marker,
            stalled_after_seconds=get_float("ATLAS_WORKER_QUEUE_STALL_SECONDS"),
        )
        crawl_metrics.queue_state(
            pending=pending,
            oldest_age_seconds=(
                max(0.0, observed_at - pending_since)
                if pending_since is not None
                else 0.0
            ),
        )
        for active_run in await list_graph_runs(runs):
            if active_run.status in {"queued", "running", "paused"}:
                active_run = await expire_graph_run(
                    runs=runs,
                    requests=requests,
                    progress=progress,
                    run=active_run,
                )
                if active_run.status in {"queued", "running"}:
                    await _fill_root_window(
                        runs=runs,
                        requests=requests,
                        progress=progress,
                        jetstream=jetstream,
                        run_id=active_run.id,
                    )

    presence_task = asyncio.create_task(
        _run_presence_until_stopped(
            stop=stop,
            monitor=monitor,
            iteration=publish_presence,
            timeout_seconds=max(
                1.0,
                get_float("ATLAS_ACQUISITION_WORKER_PRESENCE_TTL_SECONDS") / 2,
            ),
        ),
        name="acquisition-presence",
    )
    background_tasks = (
        buffered_heartbeat_task,
        event_loop_heartbeat_task,
        graph_navigation_task,
        graph_outbox_task,
        presence_task,
    )
    playwright_context = async_playwright()
    crawl_fetch_task: asyncio.Task | None = None
    try:
        async with (
            playwright_context as playwright,
            AcquisitionPipeline(maximum_concurrency=capacity) as repository_pipeline,
        ):
            playwright_driver_task = asyncio.create_task(
                _watch_playwright_driver(playwright_context),
                name="acquisition-playwright-driver",
            )
            runtime_tasks = (*background_tasks, playwright_driver_task)
            try:
                while not stop.is_set():
                    _raise_background_failure(runtime_tasks)
                    completed = {task for task in active if task.done()}
                    fatal_error = None
                    for task in completed:
                        if task.cancelled():
                            continue
                        error = task.exception()
                        if isinstance(error, PlaywrightRuntimeLost):
                            fatal_error = error
                            continue
                        if error is not None:
                            logger.error(
                                "acquisition task exited unexpectedly",
                                exc_info=(type(error), error, error.__traceback__),
                            )
                    active.difference_update(completed)
                    if fatal_error is not None:
                        raise fatal_error
                    messages = []
                    if crawl_fetch_task is not None and crawl_fetch_task.done():
                        try:
                            messages = crawl_fetch_task.result()
                        except TimeoutError, NatsTimeoutError:
                            messages = []
                        finally:
                            crawl_fetch_task = None
                        classified = await asyncio.gather(
                            *(
                                _classify_crawl_message(message, requests)
                                for message in messages
                            ),
                            return_exceptions=True,
                        )
                        for item in classified:
                            if isinstance(item, BaseException):
                                logger.error(
                                    "acquisition delivery classification failed",
                                    exc_info=(
                                        type(item),
                                        item,
                                        item.__traceback__,
                                    ),
                                )
                            elif item is not None:
                                dispatch_buffer.add(item)

                    launched = await _dispatch_buffered_crawls(
                        dispatch_buffer,
                        active,
                        capacity=capacity,
                        runs=runs,
                        requests=requests,
                        progress=progress,
                        repository_pipeline=repository_pipeline,
                        domain_pacing=domain_pacing,
                        jetstream=jetstream,
                        playwright=playwright,
                    )
                    delivery_room = (
                        CRAWL_DISPATCH_WINDOW - len(dispatch_buffer) - len(active)
                    )
                    if crawl_fetch_task is None and delivery_room > 0:
                        crawl_fetch_task = asyncio.create_task(
                            crawl_subscription.fetch(
                                batch=delivery_room,
                                timeout=60,
                            ),
                            name="acquisition-crawl-pull",
                        )
                    if not messages and not launched:
                        waiters = set(active)
                        if crawl_fetch_task is not None:
                            waiters.add(crawl_fetch_task)
                        if waiters:
                            await asyncio.wait(
                                waiters,
                                timeout=0.05,
                                return_when=asyncio.FIRST_COMPLETED,
                            )
                        else:
                            await asyncio.sleep(0.05)
            except PlaywrightRuntimeLost as exc:
                monitor.subsystem_unavailable(
                    "acquisition", str(exc) or type(exc).__name__
                )
                logger.critical(
                    "local Playwright runtime was lost; exiting acquisition worker",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
                for task in active:
                    task.cancel()
                raise
            finally:
                stop.set()
                if crawl_fetch_task is not None:
                    crawl_fetch_task.cancel()
                await release_dispatch_buffer()
                playwright_driver_task.cancel()
                presence_task.cancel()
                event_loop_heartbeat_task.cancel()
                graph_navigation_task.cancel()
                await asyncio.gather(
                    playwright_driver_task,
                    presence_task,
                    event_loop_heartbeat_task,
                    graph_navigation_task,
                    *((crawl_fetch_task,) if crawl_fetch_task is not None else ()),
                    *active,
                    return_exceptions=True,
                )
    finally:
        stop.set()
        if crawl_fetch_task is not None:
            crawl_fetch_task.cancel()
        await release_dispatch_buffer()
        presence_task.cancel()
        event_loop_heartbeat_task.cancel()
        graph_navigation_task.cancel()
        await asyncio.gather(
            presence_task,
            event_loop_heartbeat_task,
            graph_navigation_task,
            *((crawl_fetch_task,) if crawl_fetch_task is not None else ()),
            *active,
            return_exceptions=True,
        )
        await client.drain()
        await endpoints.close()


async def _run_graph_navigation_until_stopped(monitor: HealthMonitor) -> None:
    delay = 1.0
    while True:
        try:
            await run_graph_navigation(monitor=monitor)
            raise RuntimeError("graph navigation runtime exited unexpectedly")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            monitor.subsystem_unavailable("navigation", str(exc) or type(exc).__name__)
            logger.exception(
                "graph navigation runtime unavailable; retrying in %.1fs", delay
            )
            await asyncio.sleep(delay)
            delay = min(30.0, delay * 2)
