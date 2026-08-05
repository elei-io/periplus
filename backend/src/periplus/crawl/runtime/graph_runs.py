"""Crawl-graph admission, lifecycle, readiness, and edge activation."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4, uuid5

from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from periplus.platform.config import get_float, get_int
from periplus.platform.config.performance import (
    CRAWL_RUN_ACQUISITION_PENDING_LIMIT,
    GRAPH_ACK_WAIT_SECONDS,
)
from periplus.crawl.control.content_policies.schemas import EffectivePolicySnapshot
from periplus.crawl.control.content_policies.service import (
    content_policy_snapshot,
    find_content_policies_for_urls,
)
from periplus.crawl.control.content_policies.variance import vary_content_policy
from periplus.crawl.control.domain_policies.service import (
    find_domain_policies_for_urls,
    domain_policy_snapshot,
)
from periplus.platform.postgres.session import session_scope
from periplus.crawl.control.crawl_graphs.schemas import DEFAULT_GRAPH_RUN_MAX_CRAWLS
from .graph_queue import (
    CrawlRequest,
    EdgeEvaluation,
    EdgeWork,
    FrozenGraphSnapshot,
    GraphRun,
    GraphRunFailureGroup,
    MAX_GRAPH_RUN_FAILURE_GROUPS,
    NavigationReadinessWork,
    edge_evaluation_identity,
    get_crawl_request,
    get_edge_evaluation,
    get_graph_run,
    new_graph_run,
    normalize_request_url,
    update_crawl_request,
    update_edge_evaluation,
    update_graph_run,
)

_REQUEST_NAMESPACE = UUID("869ee36c-76ad-46f0-a1b7-9b28f4b71386")
_TERMINAL_RUNS = {"completed", "completed_with_errors", "failed", "cancelled"}
_TERMINAL_REQUESTS = {"completed", "failed", "cancelled"}
_FAILURE_OVERFLOW_STAGE = "other"
_FAILURE_OVERFLOW_CODE = "other_failures"


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


class EdgeEvaluationRetryable(RuntimeError):
    """Transient infrastructure failure that must preserve edge work."""


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
    return resolve_policy_snapshots(session, [url])[url]


def resolve_policy_snapshots(session, urls: list[str]) -> dict[str, dict]:
    content_policies = find_content_policies_for_urls(session, urls=urls)
    domains = find_domain_policies_for_urls(session, urls=urls)
    resolved = {}
    for url in urls:
        content_snapshot = content_policy_snapshot(content_policies[url])
        varied_content, content_variance = vary_content_policy(
            content_snapshot.content
        )
        resolved[url] = EffectivePolicySnapshot(
            content=content_snapshot.model_copy(
                update={
                    "content": varied_content,
                    "content_variance": content_variance,
                }
            ),
            domain=domain_policy_snapshot(domains[url]),
        ).model_dump(mode="json")
    return resolved


class DatabasePolicySnapshotResolver:
    """Batch policy reads into memory, then releases Postgres before remote work."""

    def __init__(self) -> None:
        self._snapshots: dict[str, dict] = {}

    def prepare(self, urls: Iterable[str]) -> None:
        normalized = list(
            dict.fromkeys(normalize_request_url(str(url)) for url in urls)
        )
        if not normalized:
            return
        with session_scope() as session:
            self._snapshots.update(
                resolve_policy_snapshots(session, normalized)
            )

    def __call__(self, url: str) -> dict:
        return self._snapshots[normalize_request_url(url)]


def _ceiling_error(run: GraphRun, now: datetime) -> str | None:
    deadline = run.deadline_at or (
        run.created_at
        + timedelta(seconds=get_int("PERIPLUS_GRAPH_MAX_RUN_SECONDS"))
    )
    if now >= deadline:
        return (
            "Graph run reached its execution deadline "
            f"{deadline.isoformat()}."
        )
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
    source_crawl_id: UUID | None = None,
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
        if (
            run.status == "failed"
            and run.error
            and "execution deadline" in run.error
        ):
            raise GraphRunCeilingError(run.error)
        return None, False
    normalized = normalize_request_url(url)
    from .graph_store import (
        GraphRunAdmissionDeferred as StoreAdmissionDeferred,
        GraphRunNotRunnableError,
    )

    try:
        result = await runs.admit_request(
            run_id=run_id,
            node_id=node_id,
            url=normalized,
            effective_policy_snapshot=policy_resolver(normalized),
            source_crawl_id=source_crawl_id,
            source_edge_id=source_edge_id,
            parent_request_id=parent_request_id,
            now=now,
        )
    except StoreAdmissionDeferred as exc:
        raise GraphRunAdmissionDeferred(str(exc)) from exc
    except GraphRunNotRunnableError:
        return None, False
    return result.request, result.created


async def _settle_run_if_idle(
    runs,
    progress,
    run_id: UUID,
    *,
    now: datetime | None = None,
) -> GraphRun:
    """Settle a run transactionally after its frontier is exhausted."""

    return await runs.settle_run_if_idle(run_id)


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
    trigger_kind: str = "manual",
    run_id: UUID | None = None,
    trigger_schedule_id: UUID | None = None,
    max_crawls: int = DEFAULT_GRAPH_RUN_MAX_CRAWLS,
    max_run_seconds: int | None = None,
    now: datetime | None = None,
) -> GraphRun:
    existing = await get_graph_run(runs, run_id) if run_id is not None else None
    if existing is not None:
        snapshot = existing.snapshot
    run = new_graph_run(
        snapshot,
        urls,
        trigger_kind=trigger_kind,  # type: ignore[arg-type]
        now=now,
        run_id=run_id,
        trigger_schedule_id=trigger_schedule_id,
        max_crawls=max_crawls,
        max_run_seconds=(
            max_run_seconds
            if max_run_seconds is not None
            else get_int("PERIPLUS_GRAPH_MAX_RUN_SECONDS")
        ),
    )
    run = await runs.create_run(run)
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
    try:
        return await runs.cancel_run(run_id)
    except KeyError as exc:
        raise GraphRunNotFoundError(f"Graph run {run_id} was not found.") from exc


async def pause_graph_run(runs, run_id: UUID) -> GraphRun:
    try:
        return await runs.pause_run(run_id)
    except KeyError as exc:
        raise GraphRunNotFoundError(
            f"Graph run {run_id} was not found."
        ) from exc


async def resume_graph_run(runs, run_id: UUID) -> GraphRun:
    try:
        return await runs.resume_run(run_id)
    except KeyError as exc:
        raise GraphRunNotFoundError(
            f"Graph run {run_id} was not found."
        ) from exc


async def expire_graph_run(
    *, runs, requests, progress, run: GraphRun, now: datetime | None = None
) -> GraphRun:
    """Enforce the deployment wall clock even when no later admission occurs."""

    now = now or datetime.now(UTC)
    deadline = run.deadline_at or (
        run.created_at
        + timedelta(seconds=get_int("PERIPLUS_GRAPH_MAX_RUN_SECONDS"))
    )
    if (
        run.status in _TERMINAL_RUNS
        or now < deadline
    ):
        return run
    error = (
        f"Graph run reached its execution deadline {deadline.isoformat()}."
    )

    return await runs.fail_run(run.id, error=error)


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
    return await runs.settle_request(
        request_id=request_id,
        status=status,
        error=error,
        expected_claim_token=expected_claim_token,
        failure_stage=failure_stage,
        failure_code=failure_code,
        status_code=status_code,
        now=now,
    )


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
    activated: list[tuple[EdgeEvaluation, EdgeWork]] = []
    if event.navigation is not None:
        for edge in outgoing:
            work = EdgeWork(
                graph_run_id=run.id,
                crawl_request_id=request.id,
                crawl_id=event.crawl_id,
                edge_id=edge.id,
                generation=event.generation,
                navigation=event.navigation,
            )
            identity = edge_evaluation_identity(
                run.id, request.id, event.crawl_id, edge.id
            )
            activated.append(
                (
                    EdgeEvaluation(
                        identity=identity,
                        graph_run_id=run.id,
                        crawl_request_id=request.id,
                        crawl_id=event.crawl_id,
                        edge_id=edge.id,
                        created_at=datetime.now(UTC),
                        updated_at=datetime.now(UTC),
                    ),
                    work,
                )
            )
    result = await runs.activate_navigation(
        event=event, edges=tuple(activated)
    )
    if result == "paused":
        raise GraphRunAdmissionDeferred(
            f"graph run {run.id} is paused"
        )
    return


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
        evaluation, _created = await requests.create_edge_evaluation(
            evaluation
        )
    claimed = False

    def start_evaluation(value: EdgeEvaluation) -> EdgeEvaluation:
        nonlocal claimed
        claimed = False
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
    count = evaluation.output_count
    seen = 0
    batch_selected = 0
    try:
        query = asyncio.create_task(
            asyncio.to_thread(
                lambda: list(
                    execute_urls(
                        edge.sql,
                        {
                            "crawl_id": work.crawl_id,
                            "_page_url": request.url,
                            "_content_sha256": request.content_sha256,
                        },
                    )
                )
            )
        )
        try:
            urls = await asyncio.wait_for(
                asyncio.shield(query),
                timeout=get_float("PERIPLUS_EDGE_QUERY_TIMEOUT_SECONDS"),
            )
        except TimeoutError as exc:
            interrupt = getattr(execute_urls, "interrupt", None)
            if interrupt is not None:
                interrupt()
            await asyncio.gather(query, return_exceptions=True)
            raise ValueError("Edge SQL exceeded its execution-time limit.") from exc
        ordered_urls = _interleave_urls_by_hostname(urls)
        prepare_policies = getattr(policy_resolver, "prepare", None)
        if prepare_policies is not None:
            await asyncio.to_thread(prepare_policies, ordered_urls)
        for url in ordered_urls:
            seen += 1
            if seen <= count:
                continue
            try:
                _target, _newly_admitted = await admit_request(
                    runs=runs,
                    requests=requests,
                    progress=progress,
                    jetstream=jetstream,
                    run_id=run.id,
                    node_id=edge.target_node_id,
                    url=str(url),
                    policy_resolver=policy_resolver,
                    source_crawl_id=work.crawl_id,
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
                await update_edge_evaluation(
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
                raise EdgeEvaluationDeferred(
                    f"edge evaluation {identity} is waiting for run capacity"
                )
            count += 1
            batch_selected += 1
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
                batch_selected = 0
    except EdgeEvaluationDeferred:
        raise
    except SQLAlchemyTimeoutError as exc:
        retry_count = count
        await update_edge_evaluation(
            requests,
            identity,
            lambda value: (
                value
                if value.claim_token != claim_token
                else value.model_copy(
                    update={
                        "status": "pending",
                        "output_count": retry_count,
                        "claim_token": None,
                        "claim_expires_at": None,
                        "updated_at": datetime.now(UTC),
                    }
                )
            ),
        )
        raise EdgeEvaluationRetryable(str(exc)) from exc
    except Exception as exc:
        error_message = str(exc)
        evaluation, _request_settled = await requests.finish_edge(
            identity=identity,
            claim_token=claim_token,
            status="failed",
            output_count=count,
            error=error_message,
            request_error=f"Edge {edge.name} failed: {exc}",
        )
        if evaluation.status != "failed":
            raise EdgeEvaluationBusy(
                f"edge evaluation {identity} was reclaimed while failing"
            ) from exc
        raise EdgeEvaluationFailed(str(exc)) from exc
    evaluation, _request_settled = await requests.finish_edge(
        identity=identity,
        claim_token=claim_token,
        status="completed",
        output_count=count,
    )
    if evaluation.status != "completed":
        raise EdgeEvaluationBusy(
            f"edge evaluation {identity} was reclaimed before completion"
        )
    return count
