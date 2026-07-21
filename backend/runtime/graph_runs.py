"""Crawl-graph admission, lifecycle, readiness, and edge activation."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from config import get_float, get_int
from config.performance import (
    CRAWL_RUN_ACQUISITION_PENDING_LIMIT,
    GRAPH_ACK_WAIT_SECONDS,
)
from control.crawl_policies.schemas import EffectivePolicySnapshot
from control.crawl_policies.service import find_crawl_policy_for_url, policy_snapshot
from control.crawl_policies.variance import vary_content_policy
from control.domain_policies.service import (
    find_domain_policy_for_url,
    domain_policy_snapshot,
)
from control.crawl_graphs.schemas import (
    DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    EdgeDedupeMode,
)
from nats.js.errors import KeyWrongLastSequenceError

from .graph_queue import (
    AdmissionReservation,
    CrawlRequest,
    EdgeEvaluation,
    EdgeWork,
    FrozenGraphSnapshot,
    GraphRun,
    GraphRunFailureGroup,
    MAX_GRAPH_RUN_FAILURE_GROUPS,
    NavigationReadinessWork,
    PendingAdmission,
    admission_reservation_key,
    edge_evaluation_identity,
    edge_evaluation_key,
    get_admission_reservation,
    get_crawl_request,
    get_edge_evaluation,
    get_graph_run,
    list_admission_reservations,
    list_crawl_requests,
    new_graph_run,
    normalize_request_url,
    publish_crawl,
    publish_edge,
    request_identity,
    update_admission_reservation,
    update_crawl_request,
    update_edge_evaluation,
    update_graph_run,
)
from .graph_progress import (
    add_edge_output_progress,
    initialize_run_progress,
    mark_run_progress_settled,
    transition_edge_evaluation_progress,
    transition_node_progress,
)
from .edge_sql import edge_uses_catalogue

_REQUEST_NAMESPACE = UUID("869ee36c-76ad-46f0-a1b7-9b28f4b71386")
_TERMINAL_RUNS = {"completed", "completed_with_errors", "failed", "cancelled"}
_TERMINAL_REQUESTS = {"completed", "failed", "cancelled"}
_FAILURE_OVERFLOW_STAGE = "other"
_FAILURE_OVERFLOW_CODE = "other_failures"


async def _project(operation):
    """Progress is repairable projection state and must not abort domain work."""

    try:
        return await operation
    except Exception:
        return None


class GraphRunNotFoundError(Exception):
    pass


class GraphRunCeilingError(RuntimeError):
    pass


class GraphRunAdmissionDeferred(RuntimeError):
    """The run must settle acquisition work before admitting another page."""


class EdgeEvaluationBusy(RuntimeError):
    pass


class EdgeEvaluationFailed(RuntimeError):
    pass


class EdgeEvaluationDeferred(RuntimeError):
    pass


def deterministic_request_id(identity: str) -> UUID:
    return uuid5(_REQUEST_NAMESPACE, identity)


def _interleave_urls_by_hostname(urls: Iterable[str]) -> list[str]:
    """Preserve per-host order while round-robining across normalized hosts."""

    grouped: dict[str, deque[str]] = {}
    for url in urls:
        normalized = normalize_request_url(str(url))
        hostname = (urlsplit(normalized).hostname or "").lower()
        grouped.setdefault(hostname, deque()).append(normalized)
    ordered: list[str] = []
    active = deque(grouped)
    while active:
        hostname = active.popleft()
        queue = grouped[hostname]
        ordered.append(queue.popleft())
        if queue:
            active.append(hostname)
    return ordered


def resolve_policy_snapshot(session, url: str) -> dict:
    crawl = find_crawl_policy_for_url(session, url=url)
    domain = find_domain_policy_for_url(session, url=url)
    crawl_snapshot = policy_snapshot(crawl)
    varied_content, content_variance = vary_content_policy(crawl_snapshot.content)
    return EffectivePolicySnapshot(
        crawl=crawl_snapshot.model_copy(
            update={
                "content": varied_content,
                "content_variance": content_variance,
            }
        ),
        domain=domain_policy_snapshot(domain),
    ).model_dump(mode="json")


def _ceiling_error(run: GraphRun, now: datetime) -> str | None:
    max_seconds = get_int("ATLAS_GRAPH_MAX_RUN_SECONDS")
    if (now - run.created_at).total_seconds() >= max_seconds:
        return f"Graph run reached platform ceiling ATLAS_GRAPH_MAX_RUN_SECONDS={max_seconds}."
    return None


async def admit_request(
    *,
    runs,
    requests,
    progress,
    jetstream,
    run_id: UUID,
    node_id: UUID,
    url: str,
    policy_resolver: Callable[[str], dict],
    dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph,
    source_crawl_id: UUID | None = None,
    source_document_id: str | None = None,
    source_edge_id: UUID | None = None,
    parent_request_id: UUID | None = None,
    now: datetime | None = None,
) -> tuple[CrawlRequest | None, bool]:
    now = now or datetime.now(UTC)
    run = await get_graph_run(runs, run_id)
    if run is None:
        raise GraphRunNotFoundError(f"Graph run {run_id} was not found.")
    run = await expire_graph_run(
        runs=runs,
        requests=requests,
        progress=progress,
        run=run,
        now=now,
    )
    if run.status in _TERMINAL_RUNS:
        if run.status == "failed" and run.error and "platform ceiling" in run.error:
            raise GraphRunCeilingError(run.error)
        return None, False
    normalized = normalize_request_url(url)
    identity = request_identity(
        run_id,
        normalized,
        dedupe_mode=dedupe_mode,
        source_edge_id=source_edge_id,
        source_crawl_id=source_crawl_id,
        source_document_id=source_document_id,
    )
    graph_identity = request_identity(run_id, normalized)
    request_id = deterministic_request_id(identity)
    reservation = await get_admission_reservation(requests, identity)
    if reservation is not None:
        return await _resume_admission(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            reservation=reservation,
        )
    if run.acquisition_pending_count >= CRAWL_RUN_ACQUISITION_PENDING_LIMIT:
        raise GraphRunAdmissionDeferred(
            f"graph run {run_id} has "
            f"{run.acquisition_pending_count} acquisition requests pending"
        )
    policy_snapshot_json = policy_resolver(normalized)
    pending = PendingAdmission(
        request_id=request_id,
        identity=identity,
        node_id=node_id,
        url=normalized,
        effective_policy_snapshot_json=policy_snapshot_json,
        source_crawl_id=source_crawl_id,
        source_edge_id=source_edge_id,
        parent_request_id=parent_request_id,
        created_at=now,
    )
    reservation = AdmissionReservation(
        identity=identity,
        graph_run_id=run_id,
        request_id=request_id,
        pending=pending,
    )
    try:
        await requests.create(
            admission_reservation_key(identity),
            reservation.model_dump_json().encode(),
        )
    except KeyWrongLastSequenceError:
        existing = await get_admission_reservation(requests, identity)
        if existing is None:
            raise
        return await _resume_admission(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            reservation=existing,
        )
    if graph_identity != identity:
        alias = AdmissionReservation(
            identity=graph_identity,
            graph_run_id=run_id,
            delivered=True,
        )
        try:
            await requests.create(
                admission_reservation_key(graph_identity),
                alias.model_dump_json().encode(),
            )
        except KeyWrongLastSequenceError:
            pass
    return await _resume_admission(
        runs=runs,
        requests=requests,
        progress=progress,
        jetstream=jetstream,
        reservation=reservation,
    )


async def _resume_admission(
    *,
    runs,
    requests,
    progress,
    jetstream,
    reservation: AdmissionReservation,
) -> tuple[CrawlRequest | None, bool]:
    if reservation.request_id is None:
        return None, False
    existing = await get_crawl_request(requests, reservation.request_id)
    if reservation.delivered:
        return existing, False
    if reservation.pending is None:
        return existing, False
    counted_now = False
    deferred = False
    budget_exhausted = False

    def reserve(run: GraphRun) -> GraphRun:
        nonlocal counted_now, deferred, budget_exhausted
        counted_now = False
        deferred = False
        budget_exhausted = False
        if run.status in _TERMINAL_RUNS or run.cancel_requested_at is not None:
            return run
        if existing is not None or any(
            value.request_id == reservation.request_id
            for value in run.pending_admissions
        ):
            return run
        remaining_roots = max(
            0, len(run.trigger_urls) - run.root_admission_cursor
        )
        if (
            reservation.pending.parent_request_id is not None
            and run.request_count >= run.max_crawls - remaining_roots
            and run.request_count < run.max_crawls
        ):
            deferred = True
            return run
        if run.request_count >= run.max_crawls:
            budget_exhausted = True
            return run.model_copy(update={"crawl_limit_reached": True})
        if run.acquisition_pending_count >= CRAWL_RUN_ACQUISITION_PENDING_LIMIT:
            deferred = True
            return run
        counted_now = True
        now = reservation.pending.created_at
        return run.model_copy(
            update={
                "status": "running",
                "started_at": run.started_at or now,
                "last_progress_at": now,
                "pending_admissions": (
                    *run.pending_admissions,
                    reservation.pending,
                ),
                "request_count": run.request_count + 1,
                "pending_request_count": run.pending_request_count + 1,
                "acquisition_pending_count": run.acquisition_pending_count + 1,
            }
        )

    run = await update_graph_run(runs, reservation.graph_run_id, reserve)
    if run.status in _TERMINAL_RUNS:
        await update_admission_reservation(
            requests,
            reservation.identity,
            lambda value: value.model_copy(update={"delivered": True}),
        )
        return None, False
    if budget_exhausted:
        await update_admission_reservation(
            requests,
            reservation.identity,
            lambda value: value.model_copy(
                update={"pending": None, "delivered": True}
            ),
        )
        return None, False
    if deferred:
        raise GraphRunAdmissionDeferred(
            f"graph run {run.id} has "
            f"{run.acquisition_pending_count} acquisition requests pending"
        )
    if counted_now and not reservation.counted:
        reservation = await update_admission_reservation(
            requests,
            reservation.identity,
            lambda value: value.model_copy(update={"counted": True}),
        )
    request = await _deliver_pending_admission(
        runs=runs,
        requests=requests,
        progress=progress,
        jetstream=jetstream,
        run_id=reservation.graph_run_id,
        pending=reservation.pending,
    )
    return request, counted_now


async def _deliver_pending_admission(
    *, runs, requests, progress, jetstream, run_id: UUID, pending: PendingAdmission
) -> CrawlRequest:
    request = await get_crawl_request(requests, pending.request_id)
    if request is None:
        request = CrawlRequest(
            id=pending.request_id,
            graph_run_id=run_id,
            node_id=pending.node_id,
            url=pending.url,
            effective_policy_snapshot_json=pending.effective_policy_snapshot_json,
            source_crawl_id=pending.source_crawl_id,
            source_edge_id=pending.source_edge_id,
            parent_request_id=pending.parent_request_id,
            created_at=pending.created_at,
            updated_at=pending.created_at,
        )
        try:
            await requests.create(request.id.hex, request.model_dump_json().encode())
            await _project(
                transition_node_progress(progress, request, previous_status=None)
            )
        except Exception:
            existing = await get_crawl_request(requests, request.id)
            if existing is None:
                raise
            request = existing
    await publish_crawl(jetstream, request)

    def clear(value: GraphRun) -> GraphRun:
        remaining = tuple(
            item
            for item in value.pending_admissions
            if item.request_id != pending.request_id
        )
        return value.model_copy(update={"pending_admissions": remaining})

    await update_graph_run(runs, run_id, clear)
    await update_admission_reservation(
        requests,
        pending.identity,
        lambda value: value.model_copy(
            update={"pending": None, "counted": True, "delivered": True}
        ),
    )
    return request


async def reconcile_pending_admissions(
    *, runs, requests, progress, jetstream, run: GraphRun
) -> int:
    """Create and republish every admission durably reserved by a prior process."""

    reconciled = 0
    for pending in run.pending_admissions:
        await _deliver_pending_admission(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            run_id=run.id,
            pending=pending,
        )
        reconciled += 1
    return reconciled


async def reconcile_admission_reservations(
    *, runs, requests, progress, jetstream, run: GraphRun
) -> int:
    """Resume sharded reservations interrupted before they reached the run."""

    reconciled = 0
    reservations = await list_admission_reservations(
        requests, graph_run_id=run.id
    )
    for reservation in reservations:
        if reservation.delivered or reservation.pending is None:
            continue
        try:
            await _resume_admission(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                reservation=reservation,
            )
        except GraphRunAdmissionDeferred:
            continue
        reconciled += 1
    return reconciled


async def _settle_run_if_idle(
    runs,
    progress,
    run_id: UUID,
    *,
    now: datetime | None = None,
) -> GraphRun:
    """Settle a run only after both work and durable root input are exhausted."""

    now = now or datetime.now(UTC)
    settled = False

    def finish(run: GraphRun) -> GraphRun:
        nonlocal settled
        settled = False
        roots_exhausted = (
            run.root_admission_cursor >= len(run.trigger_urls)
            or run.crawl_limit_reached
        )
        if (
            run.status in _TERMINAL_RUNS
            or run.pending_request_count != 0
            or run.pending_admissions
            or not roots_exhausted
        ):
            return run
        settled = True
        final = "completed_with_errors" if run.failed_request_count else "completed"
        return run.model_copy(
            update={
                "status": final,
                "completed_at": now,
                "last_progress_at": now,
            }
        )

    run = await update_graph_run(runs, run_id, finish)
    if settled:
        await _project(mark_run_progress_settled(progress, run))
    return run


async def fill_root_admissions(
    *,
    runs,
    requests,
    progress,
    jetstream,
    run_id: UUID,
    policy_resolver: Callable[[str], dict],
) -> int:
    """Fill one run's bounded acquisition window from its durable root cursor."""

    admitted = 0
    ordered_urls: tuple[str, ...] | None = None
    while True:
        run = await get_graph_run(runs, run_id)
        if (
            run is None
            or run.status in _TERMINAL_RUNS
            or run.cancel_requested_at is not None
        ):
            return admitted
        if run.crawl_limit_reached:
            await _settle_run_if_idle(runs, progress, run.id)
            return admitted
        if (
            run.acquisition_pending_count
            >= CRAWL_RUN_ACQUISITION_PENDING_LIMIT
        ):
            return admitted
        if ordered_urls is None:
            ordered_urls = tuple(_interleave_urls_by_hostname(run.trigger_urls))
        index = run.root_admission_cursor
        if index >= len(ordered_urls):
            await _settle_run_if_idle(runs, progress, run.id)
            return admitted
        try:
            _request, newly_admitted = await admit_request(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                run_id=run.id,
                node_id=run.snapshot.root_node_id,
                url=ordered_urls[index],
                policy_resolver=policy_resolver,
            )
        except GraphRunAdmissionDeferred:
            return admitted

        await update_graph_run(
            runs,
            run.id,
            lambda current: (
                current
                if current.root_admission_cursor != index
                else current.model_copy(
                    update={"root_admission_cursor": index + 1}
                )
            ),
        )
        admitted += int(newly_admitted)


async def create_graph_run(
    *,
    runs,
    requests,
    progress,
    jetstream,
    snapshot: FrozenGraphSnapshot,
    urls: list[str],
    policy_resolver: Callable[[str], dict],
    catalogue_snapshot_resolver: Callable[[], Awaitable[int | None]] | None = None,
    trigger_kind: str = "manual",
    run_id: UUID | None = None,
    trigger_schedule_id: UUID | None = None,
    max_crawls: int = DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    now: datetime | None = None,
) -> GraphRun:
    existing = await get_graph_run(runs, run_id) if run_id is not None else None
    catalogue_snapshot_id = (
        existing.catalogue_snapshot_id if existing is not None else None
    )
    if existing is None and any(
        edge_uses_catalogue(edge.sql) for edge in snapshot.edges
    ):
        if catalogue_snapshot_resolver is None:
            raise RuntimeError(
                "historical edge SQL requires a catalogue snapshot resolver"
            )
        catalogue_snapshot_id = await catalogue_snapshot_resolver()
        if catalogue_snapshot_id is None:
            raise RuntimeError(
                "historical edge SQL requires an initialized catalogue snapshot"
            )
    run = new_graph_run(
        snapshot,
        urls,
        trigger_kind=trigger_kind,  # type: ignore[arg-type]
        now=now,
        run_id=run_id,
        trigger_schedule_id=trigger_schedule_id,
        catalogue_snapshot_id=catalogue_snapshot_id,
        max_crawls=max_crawls,
    )
    if existing is None:
        existing = await get_graph_run(runs, run.id)
    if existing is None:
        try:
            await runs.create(run.id.hex, run.model_dump_json().encode())
        except KeyWrongLastSequenceError:
            existing = await get_graph_run(runs, run.id)
            if existing is None:
                raise
    if existing is not None:
        if (
            existing.graph_id != run.graph_id
            or existing.snapshot != run.snapshot
            or existing.trigger_urls != run.trigger_urls
            or existing.trigger_kind != run.trigger_kind
            or existing.trigger_schedule_id != run.trigger_schedule_id
            or existing.catalogue_snapshot_id != run.catalogue_snapshot_id
            or existing.max_crawls != run.max_crawls
        ):
            raise ValueError(
                f"Graph run identity {run.id} is already used by another trigger."
            )
        run = existing
        await _project(initialize_run_progress(progress, run))
        await reconcile_pending_admissions(
            runs=runs,
            requests=requests,
            progress=progress,
            jetstream=jetstream,
            run=run,
        )
    else:
        await _project(initialize_run_progress(progress, run))
    await fill_root_admissions(
        runs=runs,
        requests=requests,
        progress=progress,
        jetstream=jetstream,
        run_id=run.id,
        policy_resolver=policy_resolver,
    )
    return await get_graph_run(runs, run.id) or run


async def request_cancellation(
    runs, requests, run_id: UUID, *, progress, now: datetime | None = None
) -> GraphRun:
    now = now or datetime.now(UTC)

    def cancel(run: GraphRun) -> GraphRun:
        if run.status in _TERMINAL_RUNS:
            return run
        return run.model_copy(
            update={
                "status": "cancelled",
                "cancel_requested_at": now,
                "completed_at": now,
                "error": "Graph run cancelled.",
            }
        )

    try:
        run = await update_graph_run(runs, run_id, cancel)
        if run.status == "cancelled":
            for request in await list_crawl_requests(requests, graph_run_id=run_id):
                if request.status not in _TERMINAL_REQUESTS:
                    await settle_request(
                        runs=runs,
                        requests=requests,
                        progress=progress,
                        request_id=request.id,
                        status="cancelled",
                        error="Graph run cancelled.",
                        now=now,
                    )
            await _project(mark_run_progress_settled(progress, run))
        return run
    except KeyError as exc:
        raise GraphRunNotFoundError(f"Graph run {run_id} was not found.") from exc


async def expire_graph_run(
    *, runs, requests, progress, run: GraphRun, now: datetime | None = None
) -> GraphRun:
    """Enforce the deployment wall clock even when no later admission occurs."""

    now = now or datetime.now(UTC)
    max_seconds = get_int("ATLAS_GRAPH_MAX_RUN_SECONDS")
    if (
        run.status in _TERMINAL_RUNS
        or (now - run.created_at).total_seconds() < max_seconds
    ):
        return run
    error = (
        f"Graph run reached platform ceiling ATLAS_GRAPH_MAX_RUN_SECONDS={max_seconds}."
    )

    def fail(value: GraphRun) -> GraphRun:
        if value.status in _TERMINAL_RUNS:
            return value
        return value.model_copy(
            update={
                "status": "failed",
                "completed_at": now,
                "error": error,
                "error_count": value.error_count + 1,
            }
        )

    run = await update_graph_run(runs, run.id, fail)
    for request in await list_crawl_requests(requests, graph_run_id=run.id):
        if request.status not in _TERMINAL_REQUESTS:
            await settle_request(
                runs=runs,
                requests=requests,
                progress=progress,
                request_id=request.id,
                status="cancelled",
                error=error,
                now=now,
            )
    await _project(mark_run_progress_settled(progress, run))
    return await get_graph_run(runs, run.id) or run


async def settle_request(
    *,
    runs,
    requests,
    progress,
    request_id: UUID,
    status: str,
    error: str | None = None,
    now: datetime | None = None,
    expected_claim_token: UUID | None = None,
    failure_stage: str | None = None,
    failure_code: str | None = None,
    status_code: int | None = None,
) -> CrawlRequest:
    now = now or datetime.now(UTC)
    became_terminal = False
    previous_status: str | None = None

    def settle(request: CrawlRequest) -> CrawlRequest:
        nonlocal became_terminal, previous_status
        became_terminal = False
        previous_status = request.status
        if request.status in _TERMINAL_REQUESTS:
            return request
        if (
            expected_claim_token is not None
            and request.claim_token != expected_claim_token
        ):
            return request
        became_terminal = True
        return request.model_copy(
            update={
                "status": status,
                "error": error,
                "failure_stage": failure_stage if status == "failed" else None,
                "claim_token": None,
                "claim_expires_at": None,
                "updated_at": now,
            }
        )

    request = await update_crawl_request(requests, request_id, settle)
    if became_terminal:
        await _project(
            transition_node_progress(progress, request, previous_status=previous_status)
        )

        def account(run: GraphRun) -> GraphRun:
            pending = max(0, run.pending_request_count - 1)
            acquisition_pending = max(
                0,
                run.acquisition_pending_count
                - (1 if previous_status in {"queued", "crawling"} else 0),
            )
            failures = run.failed_request_count + (1 if status == "failed" else 0)
            errors = run.error_count + (1 if status == "failed" else 0)
            update = {
                "pending_request_count": pending,
                "acquisition_pending_count": acquisition_pending,
                "failed_request_count": failures,
                "error_count": errors,
                "last_progress_at": now,
            }
            if status == "failed":
                update["failure_groups"] = _updated_failure_groups(
                    run,
                    request=request,
                    failure_stage=failure_stage,
                    failure_code=failure_code,
                    status_code=status_code,
                    detail=error,
                    occurred_at=now,
                )
            return run.model_copy(update=update)

        run = await update_graph_run(runs, request.graph_run_id, account)
        if run.status in _TERMINAL_RUNS:
            await _project(mark_run_progress_settled(progress, run))
        elif run.pending_request_count == 0:
            await _settle_run_if_idle(
                runs,
                progress,
                run.id,
                now=now,
            )
    return request


def _updated_failure_groups(
    run: GraphRun,
    *,
    request: CrawlRequest,
    failure_stage: str | None,
    failure_code: str | None,
    status_code: int | None,
    detail: str | None,
    occurred_at: datetime,
) -> tuple[GraphRunFailureGroup, ...]:
    stage = failure_stage or "lifecycle"
    code = failure_code or f"{stage}_failed"
    groups = list(run.failure_groups)
    matching = next(
        (
            index
            for index, group in enumerate(groups)
            if (
                group.failure_stage == stage
                and group.failure_code == code
                and group.status_code == status_code
            )
        ),
        None,
    )
    if matching is None and len(groups) >= MAX_GRAPH_RUN_FAILURE_GROUPS - 1:
        stage = _FAILURE_OVERFLOW_STAGE
        code = _FAILURE_OVERFLOW_CODE
        status_code = None
        matching = next(
            (
                index
                for index, group in enumerate(groups)
                if (
                    group.failure_stage == stage
                    and group.failure_code == code
                )
            ),
            None,
        )
        if matching is None and len(groups) >= MAX_GRAPH_RUN_FAILURE_GROUPS:
            matching = len(groups) - 1
            displaced = groups[matching]
            groups[matching] = displaced.model_copy(
                update={
                    "failure_stage": stage,
                    "failure_code": code,
                    "status_code": None,
                }
            )
    example = {
        "example_url": request.url,
        "example_detail": detail[:2_000] if detail else None,
        "last_occurred_at": occurred_at,
    }
    if matching is not None:
        current = groups[matching]
        groups[matching] = current.model_copy(
            update={"count": current.count + 1, **example}
        )
    else:
        groups.append(
            GraphRunFailureGroup(
                failure_stage=stage,
                failure_code=code,
                status_code=status_code,
                count=1,
                **example,
            )
        )
    return tuple(groups)


async def release_acquisition_slot(
    runs,
    run_id: UUID,
    *,
    now: datetime | None = None,
) -> GraphRun:
    """Release one run acquisition window slot after readiness is durable."""

    now = now or datetime.now(UTC)
    return await update_graph_run(
        runs,
        run_id,
        lambda run: run.model_copy(
            update={
                "acquisition_pending_count": max(
                    0, run.acquisition_pending_count - 1
                ),
                "last_progress_at": now,
            }
        ),
    )


async def reconcile_acquisition_pending_count(
    runs,
    requests,
    run_id: UUID,
) -> GraphRun:
    """Repair the bounded acquisition-window projection after an interrupted update."""

    crawl_requests = await list_crawl_requests(requests, graph_run_id=run_id)
    acquisition_pending = sum(
        request.status in {"queued", "crawling"} for request in crawl_requests
    )
    return await update_graph_run(
        runs,
        run_id,
        lambda run: run.model_copy(
            update={"acquisition_pending_count": acquisition_pending}
        ),
    )


async def handle_navigation_readiness(
    *, runs, requests, progress, jetstream, event: NavigationReadinessWork
) -> None:
    request = await get_crawl_request(requests, event.crawl_request_id)
    if request is None:
        return
    if request.status in _TERMINAL_REQUESTS:
        return
    run = await get_graph_run(runs, event.graph_run_id)
    if run is None:
        return
    if run.status in _TERMINAL_RUNS:
        return
    outgoing = [
        edge for edge in run.snapshot.edges if edge.source_node_id == request.node_id
    ]
    if event.navigation is None or not outgoing:
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="completed",
        )
        return
    previous_status: str | None = None

    def evaluate(current: CrawlRequest) -> CrawlRequest:
        nonlocal previous_status
        previous_status = current.status
        if current.status == "evaluating_edges":
            return current
        return current.model_copy(
            update={
                "status": "evaluating_edges",
                "claim_token": None,
                "claim_expires_at": None,
                "updated_at": datetime.now(UTC),
            }
        )

    request = await update_crawl_request(requests, request.id, evaluate)
    if previous_status != request.status:
        await _project(
            transition_node_progress(progress, request, previous_status=previous_status)
        )
    for edge in outgoing:
        assert event.navigation is not None
        work = EdgeWork(
            graph_run_id=run.id,
            crawl_request_id=request.id,
            crawl_id=event.crawl_id,
            edge_id=edge.id,
            navigation=event.navigation,
            catalogue_snapshot_id=(
                run.catalogue_snapshot_id if edge_uses_catalogue(edge.sql) else None
            ),
        )
        identity = edge_evaluation_identity(run.id, request.id, event.crawl_id, edge.id)
        evaluation = EdgeEvaluation(
            identity=identity,
            graph_run_id=run.id,
            crawl_request_id=request.id,
            crawl_id=event.crawl_id,
            edge_id=edge.id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        try:
            await requests.create(
                edge_evaluation_key(identity), evaluation.model_dump_json().encode()
            )
            await _project(
                transition_edge_evaluation_progress(
                    progress, evaluation, previous_status=None
                )
            )
        except Exception:
            existing = await get_edge_evaluation(requests, identity)
            if existing is None:
                raise
        await publish_edge(jetstream, work)


async def evaluate_edge(
    *,
    runs,
    requests,
    progress,
    jetstream,
    work: EdgeWork,
    execute_urls: Callable[[str, Mapping[str, object]], Iterable[str]],
    policy_resolver: Callable[[str], dict],
    claim_token: UUID | None = None,
) -> int:
    claim_token = claim_token or uuid4()
    identity = edge_evaluation_identity(
        work.graph_run_id, work.crawl_request_id, work.crawl_id, work.edge_id
    )
    existing = await get_edge_evaluation(requests, identity)
    if existing is not None and existing.status == "completed":
        return existing.output_count
    run = await get_graph_run(runs, work.graph_run_id)
    request = await get_crawl_request(requests, work.crawl_request_id)
    if (
        run is None
        or request is None
        or request.status in _TERMINAL_REQUESTS
        or run.status in _TERMINAL_RUNS
    ):
        return 0
    edge = next(
        (
            value
            for value in run.snapshot.edges
            if value.id == work.edge_id and value.source_node_id == request.node_id
        ),
        None,
    )
    if edge is None:
        raise ValueError(
            f"Frozen edge {work.edge_id} is not outgoing from node {request.node_id}."
        )
    evaluation = await get_edge_evaluation(requests, identity)
    if evaluation is not None and evaluation.status == "completed":
        return evaluation.output_count
    if evaluation is None:
        now = datetime.now(UTC)
        evaluation = EdgeEvaluation(
            identity=identity,
            graph_run_id=run.id,
            crawl_request_id=request.id,
            crawl_id=work.crawl_id,
            edge_id=edge.id,
            created_at=now,
            updated_at=now,
        )
        try:
            await requests.create(
                edge_evaluation_key(identity), evaluation.model_dump_json().encode()
            )
            await _project(
                transition_edge_evaluation_progress(
                    progress, evaluation, previous_status=None
                )
            )
        except Exception:
            evaluation = await get_edge_evaluation(requests, identity)
            if evaluation is None:
                raise
    previous_evaluation_status: str | None = None
    claimed = False

    def start_evaluation(value: EdgeEvaluation) -> EdgeEvaluation:
        nonlocal previous_evaluation_status, claimed
        claimed = False
        previous_evaluation_status = value.status
        now = datetime.now(UTC)
        reclaimable = (
            value.status == "running"
            and value.claim_expires_at is not None
            and value.claim_expires_at <= now
        )
        if value.status == "running" and not reclaimable:
            return value
        claimed = True
        return value.model_copy(
            update={
                "status": "running",
                "claim_token": claim_token,
                "claim_expires_at": now + timedelta(seconds=GRAPH_ACK_WAIT_SECONDS * 2),
                "updated_at": now,
            }
        )

    evaluation = await update_edge_evaluation(requests, identity, start_evaluation)
    if not claimed:
        raise EdgeEvaluationBusy(f"edge evaluation {identity} is already claimed")
    if previous_evaluation_status != evaluation.status:
        await _project(
            transition_edge_evaluation_progress(
                progress, evaluation, previous_status=previous_evaluation_status
            )
        )
    count = evaluation.output_count
    seen = 0
    batch_selected = 0
    batch_admitted = 0
    try:
        if "$crawl_id" not in edge.sql:
            raise ValueError("Edge SQL must contain $crawl_id.")
        query = asyncio.create_task(
            asyncio.to_thread(
                lambda: list(
                    execute_urls(
                        edge.sql,
                        {
                            "crawl_id": work.crawl_id,
                            "_page_url": request.url,
                            "_document_id": request.document_id,
                        },
                    )
                )
            )
        )
        try:
            urls = await asyncio.wait_for(
                asyncio.shield(query),
                timeout=get_float("ATLAS_EDGE_QUERY_TIMEOUT_SECONDS"),
            )
        except TimeoutError as exc:
            interrupt = getattr(execute_urls, "interrupt", None)
            if interrupt is not None:
                interrupt()
            await asyncio.gather(query, return_exceptions=True)
            raise ValueError("Edge SQL exceeded its execution-time limit.") from exc
        for url in _interleave_urls_by_hostname(urls):
            seen += 1
            if seen <= count:
                continue
            try:
                _target, newly_admitted = await admit_request(
                    runs=runs,
                    requests=requests,
                    progress=progress,
                    jetstream=jetstream,
                    run_id=run.id,
                    node_id=edge.target_node_id,
                    url=str(url),
                    policy_resolver=policy_resolver,
                    dedupe_mode=edge.dedupe_mode,
                    source_crawl_id=work.crawl_id,
                    source_document_id=request.document_id,
                    source_edge_id=edge.id,
                    parent_request_id=request.id,
                )
            except GraphRunAdmissionDeferred:
                if batch_selected:
                    progress_count = count
                    await update_edge_evaluation(
                        requests,
                        identity,
                        lambda value: value.model_copy(
                            update={
                                "output_count": progress_count,
                                "updated_at": datetime.now(UTC),
                            }
                        ),
                    )
                    await _project(
                        add_edge_output_progress(
                            progress,
                            run.id,
                            edge.id,
                            selected=batch_selected,
                            admitted=batch_admitted,
                            deduplicated=batch_selected - batch_admitted,
                        )
                    )
                deferred_evaluation = await update_edge_evaluation(
                    requests,
                    identity,
                    lambda value: (
                        value
                        if value.claim_token != claim_token
                        else value.model_copy(
                            update={
                                "status": "pending",
                                "output_count": count,
                                "claim_token": None,
                                "claim_expires_at": None,
                                "updated_at": datetime.now(UTC),
                            }
                        )
                    ),
                )
                if deferred_evaluation.status == "pending":
                    await _project(
                        transition_edge_evaluation_progress(
                            progress,
                            deferred_evaluation,
                            previous_status="running",
                        )
                    )
                raise EdgeEvaluationDeferred(
                    f"edge evaluation {identity} is waiting for run capacity"
                )
            count += 1
            batch_selected += 1
            batch_admitted += int(newly_admitted)
            if batch_selected == 10:
                progress_count = count
                await update_edge_evaluation(
                    requests,
                    identity,
                    lambda value: value.model_copy(
                        update={
                            "output_count": progress_count,
                            "updated_at": datetime.now(UTC),
                        }
                    ),
                )
                await _project(
                    add_edge_output_progress(
                        progress,
                        run.id,
                        edge.id,
                        selected=batch_selected,
                        admitted=batch_admitted,
                        deduplicated=batch_selected - batch_admitted,
                    )
                )
                batch_selected = 0
                batch_admitted = 0
    except EdgeEvaluationDeferred:
        raise
    except Exception as exc:
        previous_evaluation_status = evaluation.status
        error_message = str(exc)
        evaluation = await update_edge_evaluation(
            requests,
            identity,
            lambda value: (
                value
                if value.claim_token != claim_token
                else value.model_copy(
                    update={
                        "status": "failed",
                        "error": error_message,
                        "claim_token": None,
                        "claim_expires_at": None,
                        "updated_at": datetime.now(UTC),
                    }
                )
            ),
        )
        if evaluation.status != "failed":
            raise EdgeEvaluationBusy(
                f"edge evaluation {identity} was reclaimed while failing"
            ) from exc
        await _project(
            transition_edge_evaluation_progress(
                progress, evaluation, previous_status=previous_evaluation_status
            )
        )
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="failed",
            error=f"Edge {edge.name} failed: {exc}",
            failure_stage="edge",
            failure_code="edge_evaluation_failed",
        )
        raise EdgeEvaluationFailed(str(exc)) from exc
    previous_evaluation_status = evaluation.status
    evaluation = await update_edge_evaluation(
        requests,
        identity,
        lambda value: (
            value
            if value.claim_token != claim_token
            else value.model_copy(
                update={
                    "status": "completed",
                    "output_count": count,
                    "error": None,
                    "claim_token": None,
                    "claim_expires_at": None,
                    "updated_at": datetime.now(UTC),
                }
            )
        ),
    )
    if evaluation.status != "completed":
        raise EdgeEvaluationBusy(
            f"edge evaluation {identity} was reclaimed before completion"
        )
    if batch_selected:
        await _project(
            add_edge_output_progress(
                progress,
                run.id,
                edge.id,
                selected=batch_selected,
                admitted=batch_admitted,
                deduplicated=batch_selected - batch_admitted,
            )
        )
    await _project(
        transition_edge_evaluation_progress(
            progress, evaluation, previous_status=previous_evaluation_status
        )
    )
    outgoing = [
        value for value in run.snapshot.edges if value.source_node_id == request.node_id
    ]
    evaluations = [
        await get_edge_evaluation(
            requests,
            edge_evaluation_identity(run.id, request.id, work.crawl_id, value.id),
        )
        for value in outgoing
    ]
    if evaluations and all(
        value is not None and value.status == "completed" for value in evaluations
    ):
        await settle_request(
            runs=runs,
            requests=requests,
            progress=progress,
            request_id=request.id,
            status="completed",
        )
    return count
