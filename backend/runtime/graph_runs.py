"""Crawl-graph admission, lifecycle, readiness, and edge activation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid5

from config import get_int
from control.crawl_policies.schemas import CrawlPolicySnapshot
from control.crawl_policies.service import find_crawl_policy_for_url

from .graph_queue import CrawlRequest, EdgeEvaluation, EdgeWork, FrozenGraphSnapshot, GraphRun, ReadinessWork, edge_evaluation_identity, edge_evaluation_key, get_crawl_request, get_edge_evaluation, get_graph_run, new_graph_run, normalize_request_url, publish_crawl, publish_edge, request_identity, update_crawl_request, update_edge_evaluation, update_graph_run
from .graph_progress import add_edge_output_progress, initialize_run_progress, mark_run_progress_settled, transition_edge_evaluation_progress, transition_node_progress

_REQUEST_NAMESPACE = UUID("869ee36c-76ad-46f0-a1b7-9b28f4b71386")
_TERMINAL_RUNS = {"completed", "completed_with_errors", "failed", "cancelled"}
_TERMINAL_REQUESTS = {"completed", "failed", "cancelled"}


class GraphRunNotFoundError(Exception):
    pass


class GraphRunCeilingError(RuntimeError):
    pass


def deterministic_request_id(identity: str) -> UUID:
    return uuid5(_REQUEST_NAMESPACE, identity)


def resolve_policy_snapshot(session, url: str) -> dict | None:
    policy = find_crawl_policy_for_url(session, url=url)
    if policy is None:
        return None
    matcher = policy.url_match
    if matcher is None:
        return None
    snapshot = CrawlPolicySnapshot(
        id=policy.id,
        revision=policy.revision,
        metric_slug=policy.metric_slug,
        domain_group=policy.domain_group,
        match=policy.match,
        config=policy.config or {},
        matcher={
            "scheme": matcher.scheme,
            "host": matcher.host,
            "path_pattern": matcher.path_pattern,
            "match_type": matcher.match_type,
            "priority": matcher.priority,
        },
    )
    return snapshot.model_dump(mode="json")


def _ceiling_error(run: GraphRun, now: datetime) -> str | None:
    max_requests = get_int("ATLAS_GRAPH_MAX_REQUESTS_PER_RUN")
    if run.request_count >= max_requests:
        return f"Graph run reached platform ceiling ATLAS_GRAPH_MAX_REQUESTS_PER_RUN={max_requests}."
    max_seconds = get_int("ATLAS_GRAPH_MAX_RUN_SECONDS")
    if (now - run.created_at).total_seconds() >= max_seconds:
        return f"Graph run reached platform ceiling ATLAS_GRAPH_MAX_RUN_SECONDS={max_seconds}."
    return None


async def admit_request(*, runs, requests, progress, jetstream, run_id: UUID, node_id: UUID, url: str, policy_resolver: Callable[[str], dict | None], source_crawl_id: UUID | None = None, source_edge_id: UUID | None = None, parent_request_id: UUID | None = None, now: datetime | None = None) -> tuple[CrawlRequest | None, bool]:
    now = now or datetime.now(UTC)
    normalized = normalize_request_url(url)
    identity = request_identity(run_id, node_id, normalized)
    request_id = deterministic_request_id(identity)
    admitted = False

    def reserve(run: GraphRun) -> GraphRun:
        nonlocal admitted
        admitted = False
        if run.status in _TERMINAL_RUNS or run.cancel_requested_at is not None or identity in run.seen_request_identities:
            return run
        error = _ceiling_error(run, now)
        if error:
            return run.model_copy(update={"status": "failed", "completed_at": now, "error": error})
        admitted = True
        return run.model_copy(update={"status": "running", "started_at": run.started_at or now, "seen_request_identities": (*run.seen_request_identities, identity), "request_count": run.request_count + 1, "pending_request_count": run.pending_request_count + 1})

    run = await update_graph_run(runs, run_id, reserve)
    if not admitted:
        if run.status == "failed" and run.error and "platform ceiling" in run.error:
            raise GraphRunCeilingError(run.error)
        existing = await get_crawl_request(requests, request_id)
        return existing, False
    request = CrawlRequest(id=request_id, graph_run_id=run_id, node_id=node_id, url=normalized, effective_policy_snapshot_json=policy_resolver(normalized), source_crawl_id=source_crawl_id, source_edge_id=source_edge_id, parent_request_id=parent_request_id, created_at=now, updated_at=now)
    try:
        await requests.create(request.id.hex, request.model_dump_json().encode())
        await transition_node_progress(progress, request, previous_status=None)
        await publish_crawl(jetstream, request.id)
    except BaseException:
        # State remains recoverable: request identity is reserved and request KV, if created,
        # deterministically identifies the missing publication for reconciliation.
        raise
    return request, True


async def create_graph_run(*, runs, requests, progress, jetstream, snapshot: FrozenGraphSnapshot, urls: list[str], policy_resolver: Callable[[str], dict | None], trigger_kind: str = "manual") -> GraphRun:
    run = new_graph_run(snapshot, urls, trigger_kind=trigger_kind)  # type: ignore[arg-type]
    await runs.create(run.id.hex, run.model_dump_json().encode())
    await initialize_run_progress(progress, run)
    for url in run.trigger_urls:
        await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=snapshot.root_node_id, url=url, policy_resolver=policy_resolver)
    return await get_graph_run(runs, run.id) or run


async def request_cancellation(runs, requests, run_id: UUID, *, progress, now: datetime | None = None) -> GraphRun:
    now = now or datetime.now(UTC)
    def cancel(run: GraphRun) -> GraphRun:
        if run.status in _TERMINAL_RUNS:
            return run
        return run.model_copy(update={"status": "cancelled", "cancel_requested_at": now, "completed_at": now, "error": "Graph run cancelled."})
    try:
        run = await update_graph_run(runs, run_id, cancel)
        if run.status == "cancelled":
            await mark_run_progress_settled(progress, run)
        return run
    except KeyError as exc:
        raise GraphRunNotFoundError(f"Graph run {run_id} was not found.") from exc


async def settle_request(*, runs, requests, progress, request_id: UUID, status: str, error: str | None = None, now: datetime | None = None) -> CrawlRequest:
    now = now or datetime.now(UTC)
    became_terminal = False
    def settle(request: CrawlRequest) -> CrawlRequest:
        nonlocal became_terminal
        if request.status in _TERMINAL_REQUESTS:
            return request
        became_terminal = True
        return request.model_copy(update={"status": status, "error": error, "updated_at": now})
    previous_status: str | None = None
    def tracked_settle(request: CrawlRequest) -> CrawlRequest:
        nonlocal previous_status
        previous_status = request.status
        return settle(request)
    request = await update_crawl_request(requests, request_id, tracked_settle)
    if became_terminal:
        await transition_node_progress(progress, request, previous_status=previous_status)
        def account(run: GraphRun) -> GraphRun:
            pending = max(0, run.pending_request_count - 1)
            failures = run.failed_request_count + (1 if status == "failed" else 0)
            if pending == 0 and run.status not in _TERMINAL_RUNS:
                final = "completed_with_errors" if failures else "completed"
                return run.model_copy(update={"pending_request_count": pending, "failed_request_count": failures, "status": final, "completed_at": now})
            return run.model_copy(update={"pending_request_count": pending, "failed_request_count": failures})
        run = await update_graph_run(runs, request.graph_run_id, account)
        if run.status in _TERMINAL_RUNS:
            await mark_run_progress_settled(progress, run)
    return request


async def handle_readiness(*, runs, requests, progress, jetstream, event: ReadinessWork) -> None:
    request = await get_crawl_request(requests, event.crawl_request_id)
    if request is None or request.status in _TERMINAL_REQUESTS:
        return
    if event.status == "failed":
        ids = ", ".join(str(value) for value in event.failed_materialization_ids)
        await settle_request(runs=runs, requests=requests, progress=progress, request_id=request.id, status="failed", error=f"Crawl enrichment failed: {ids or 'unknown materialization'}")
        return
    run = await get_graph_run(runs, event.graph_run_id)
    if run is None or run.status in _TERMINAL_RUNS:
        return
    outgoing = [edge for edge in run.snapshot.edges if edge.source_node_id == request.node_id]
    if not outgoing:
        await settle_request(runs=runs, requests=requests, progress=progress, request_id=request.id, status="completed")
        return
    previous_status: str | None = None
    def evaluate(current: CrawlRequest) -> CrawlRequest:
        nonlocal previous_status
        previous_status = current.status
        return current if current.status == "evaluating_edges" else current.model_copy(update={"status": "evaluating_edges", "updated_at": datetime.now(UTC)})
    request = await update_crawl_request(requests, request.id, evaluate)
    if previous_status != request.status:
        await transition_node_progress(progress, request, previous_status=previous_status)
    for edge in outgoing:
        work = EdgeWork(graph_run_id=run.id, crawl_request_id=request.id, crawl_id=event.crawl_id, edge_id=edge.id)
        identity = edge_evaluation_identity(run.id, request.id, event.crawl_id, edge.id)
        evaluation = EdgeEvaluation(identity=identity, graph_run_id=run.id, crawl_request_id=request.id, crawl_id=event.crawl_id, edge_id=edge.id, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        try:
            await requests.create(edge_evaluation_key(identity), evaluation.model_dump_json().encode())
            await transition_edge_evaluation_progress(progress, evaluation, previous_status=None)
        except Exception:
            existing = await get_edge_evaluation(requests, identity)
            if existing is None:
                raise
        await publish_edge(jetstream, work)


async def evaluate_edge(*, runs, requests, progress, jetstream, work: EdgeWork, execute_urls: Callable[[str, Mapping[str, object]], Iterable[str]], policy_resolver: Callable[[str], dict | None]) -> int:
    identity = edge_evaluation_identity(work.graph_run_id, work.crawl_request_id, work.crawl_id, work.edge_id)
    existing = await get_edge_evaluation(requests, identity)
    if existing is not None and existing.status == "completed":
        return existing.output_count
    run = await get_graph_run(runs, work.graph_run_id)
    request = await get_crawl_request(requests, work.crawl_request_id)
    if run is None or request is None or request.status in _TERMINAL_REQUESTS or run.status in _TERMINAL_RUNS:
        return 0
    edge = next((value for value in run.snapshot.edges if value.id == work.edge_id and value.source_node_id == request.node_id), None)
    if edge is None:
        raise ValueError(f"Frozen edge {work.edge_id} is not outgoing from node {request.node_id}.")
    evaluation = await get_edge_evaluation(requests, identity)
    if evaluation is not None and evaluation.status == "completed":
        return evaluation.output_count
    if evaluation is None:
        now = datetime.now(UTC)
        evaluation = EdgeEvaluation(identity=identity, graph_run_id=run.id, crawl_request_id=request.id, crawl_id=work.crawl_id, edge_id=edge.id, created_at=now, updated_at=now)
        try:
            await requests.create(edge_evaluation_key(identity), evaluation.model_dump_json().encode())
            await transition_edge_evaluation_progress(progress, evaluation, previous_status=None)
        except Exception:
            evaluation = await get_edge_evaluation(requests, identity)
            if evaluation is None:
                raise
    previous_evaluation_status: str | None = None
    def start_evaluation(value: EdgeEvaluation) -> EdgeEvaluation:
        nonlocal previous_evaluation_status
        previous_evaluation_status = value.status
        return value if value.status == "running" else value.model_copy(update={"status": "running", "updated_at": datetime.now(UTC)})
    evaluation = await update_edge_evaluation(requests, identity, start_evaluation)
    if previous_evaluation_status != evaluation.status:
        await transition_edge_evaluation_progress(progress, evaluation, previous_status=previous_evaluation_status)
    count = evaluation.output_count
    seen = 0
    batch_selected = 0
    batch_admitted = 0
    try:
        if "$crawl_id" not in edge.sql:
            raise ValueError("Edge SQL must contain $crawl_id.")
        for url in execute_urls(edge.sql, {"crawl_id": work.crawl_id}):
            seen += 1
            if seen <= count:
                continue
            count += 1
            batch_selected += 1
            _target, newly_admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=edge.target_node_id, url=str(url), policy_resolver=policy_resolver, source_crawl_id=work.crawl_id, source_edge_id=edge.id, parent_request_id=request.id)
            batch_admitted += int(newly_admitted)
            if batch_selected == 10:
                progress_count = count
                await update_edge_evaluation(
                    requests,
                    identity,
                    lambda value: value.model_copy(
                        update={"output_count": progress_count, "updated_at": datetime.now(UTC)}
                    ),
                )
                await add_edge_output_progress(
                    progress,
                    run.id,
                    edge.id,
                    selected=batch_selected,
                    admitted=batch_admitted,
                    deduplicated=batch_selected - batch_admitted,
                )
                batch_selected = 0
                batch_admitted = 0
    except Exception as exc:
        previous_evaluation_status = evaluation.status
        evaluation = await update_edge_evaluation(requests, identity, lambda value: value.model_copy(update={"status": "failed", "error": str(exc), "updated_at": datetime.now(UTC)}))
        await transition_edge_evaluation_progress(progress, evaluation, previous_status=previous_evaluation_status)
        await settle_request(runs=runs, requests=requests, progress=progress, request_id=request.id, status="failed", error=f"Edge {edge.name} failed: {exc}")
        raise
    previous_evaluation_status = evaluation.status
    evaluation = await update_edge_evaluation(requests, identity, lambda value: value.model_copy(update={"status": "completed", "output_count": count, "error": None, "updated_at": datetime.now(UTC)}))
    if batch_selected:
        await add_edge_output_progress(
            progress,
            run.id,
            edge.id,
            selected=batch_selected,
            admitted=batch_admitted,
            deduplicated=batch_selected - batch_admitted,
        )
    await transition_edge_evaluation_progress(progress, evaluation, previous_status=previous_evaluation_status)
    outgoing = [value for value in run.snapshot.edges if value.source_node_id == request.node_id]
    evaluations = [await get_edge_evaluation(requests, edge_evaluation_identity(run.id, request.id, work.crawl_id, value.id)) for value in outgoing]
    if evaluations and all(value is not None and value.status == "completed" for value in evaluations):
        await settle_request(runs=runs, requests=requests, progress=progress, request_id=request.id, status="completed")
    return count
