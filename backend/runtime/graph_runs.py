"""Crawl-graph admission, lifecycle, readiness, and edge activation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5

from config import get_float, get_int
from config.performance import GRAPH_ACK_WAIT_SECONDS
from control.crawl_policies.schemas import EffectivePolicySnapshot
from control.crawl_policies.service import find_crawl_policy_for_url, policy_snapshot
from control.domain_policies.service import find_domain_policy_for_url, domain_policy_snapshot
from control.crawl_graphs.schemas import EdgeDedupeMode
from nats.js.errors import KeyWrongLastSequenceError

from .graph_queue import CrawlRequest, EdgeEvaluation, EdgeWork, FrozenGraphSnapshot, GraphRun, PendingAdmission, ReadinessWork, edge_evaluation_identity, edge_evaluation_key, get_crawl_request, get_edge_evaluation, get_graph_run, list_crawl_requests, new_graph_run, normalize_request_url, publish_crawl, publish_edge, request_identity, update_crawl_request, update_edge_evaluation, update_graph_run
from .graph_progress import add_edge_output_progress, initialize_run_progress, mark_run_progress_settled, transition_edge_evaluation_progress, transition_node_progress

_REQUEST_NAMESPACE = UUID("869ee36c-76ad-46f0-a1b7-9b28f4b71386")
_TERMINAL_RUNS = {"completed", "completed_with_errors", "failed", "cancelled"}
_TERMINAL_REQUESTS = {"completed", "failed", "cancelled"}


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


class EdgeEvaluationBusy(RuntimeError):
    pass


class EdgeEvaluationFailed(RuntimeError):
    pass


def deterministic_request_id(identity: str) -> UUID:
    return uuid5(_REQUEST_NAMESPACE, identity)


def resolve_policy_snapshot(session, url: str) -> dict:
    crawl = find_crawl_policy_for_url(session, url=url)
    domain = find_domain_policy_for_url(session, url=url)
    return EffectivePolicySnapshot(
        crawl=policy_snapshot(crawl),
        domain=domain_policy_snapshot(domain),
    ).model_dump(mode="json")


def _ceiling_error(run: GraphRun, now: datetime) -> str | None:
    max_requests = get_int("ATLAS_GRAPH_MAX_REQUESTS_PER_RUN")
    if run.request_count >= max_requests:
        return f"Graph run reached platform ceiling ATLAS_GRAPH_MAX_REQUESTS_PER_RUN={max_requests}."
    max_seconds = get_int("ATLAS_GRAPH_MAX_RUN_SECONDS")
    if (now - run.created_at).total_seconds() >= max_seconds:
        return f"Graph run reached platform ceiling ATLAS_GRAPH_MAX_RUN_SECONDS={max_seconds}."
    return None


async def admit_request(*, runs, requests, progress, jetstream, run_id: UUID, node_id: UUID, url: str, policy_resolver: Callable[[str], dict], dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph, source_crawl_id: UUID | None = None, source_document_id: str | None = None, source_edge_id: UUID | None = None, parent_request_id: UUID | None = None, now: datetime | None = None) -> tuple[CrawlRequest | None, bool]:
    now = now or datetime.now(UTC)
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
    admitted = False

    def reserve(run: GraphRun) -> GraphRun:
        nonlocal admitted
        admitted = False
        dedupe_identity = graph_identity if dedupe_mode == EdgeDedupeMode.graph else identity
        if run.status in _TERMINAL_RUNS or run.cancel_requested_at is not None or dedupe_identity in run.seen_request_identities:
            return run
        error = _ceiling_error(run, now)
        if error:
            return run.model_copy(update={
                "status": "failed",
                "completed_at": now,
                "error": error,
                "error_count": run.error_count + 1,
            })
        admitted = True
        identities = (identity,)
        if graph_identity != identity and graph_identity not in run.seen_request_identities:
            identities = (*identities, graph_identity)
        return run.model_copy(update={"status": "running", "started_at": run.started_at or now, "last_progress_at": now, "seen_request_identities": (*run.seen_request_identities, *identities), "pending_admissions": (*run.pending_admissions, pending), "request_count": run.request_count + 1, "pending_request_count": run.pending_request_count + 1})

    run = await update_graph_run(runs, run_id, reserve)
    if not admitted:
        if run.status == "failed" and run.error and "platform ceiling" in run.error:
            raise GraphRunCeilingError(run.error)
        reserved = next(
            (value for value in run.pending_admissions if value.request_id == request_id),
            None,
        )
        if reserved is not None:
            existing = await _deliver_pending_admission(
                runs=runs,
                requests=requests,
                progress=progress,
                jetstream=jetstream,
                run_id=run_id,
                pending=reserved,
            )
            return existing, False
        existing = await get_crawl_request(requests, request_id)
        return existing, False
    request = await _deliver_pending_admission(
        runs=runs,
        requests=requests,
        progress=progress,
        jetstream=jetstream,
        run_id=run_id,
        pending=pending,
    )
    return request, True


async def _deliver_pending_admission(*, runs, requests, progress, jetstream, run_id: UUID, pending: PendingAdmission) -> CrawlRequest:
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
            await _project(transition_node_progress(progress, request, previous_status=None))
        except Exception:
            existing = await get_crawl_request(requests, request.id)
            if existing is None:
                raise
            request = existing
    await publish_crawl(jetstream, request)

    def clear(value: GraphRun) -> GraphRun:
        remaining = tuple(
            item for item in value.pending_admissions
            if item.request_id != pending.request_id
        )
        return value.model_copy(update={"pending_admissions": remaining})

    await update_graph_run(runs, run_id, clear)
    return request


async def reconcile_pending_admissions(*, runs, requests, progress, jetstream, run: GraphRun) -> int:
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
    now: datetime | None = None,
) -> GraphRun:
    run = new_graph_run(
        snapshot,
        urls,
        trigger_kind=trigger_kind,  # type: ignore[arg-type]
        now=now,
        run_id=run_id,
        trigger_schedule_id=trigger_schedule_id,
    )
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


async def expire_graph_run(*, runs, requests, progress, run: GraphRun, now: datetime | None = None) -> GraphRun:
    """Enforce the deployment wall clock even when no later admission occurs."""

    now = now or datetime.now(UTC)
    max_seconds = get_int("ATLAS_GRAPH_MAX_RUN_SECONDS")
    if run.status in _TERMINAL_RUNS or (now - run.created_at).total_seconds() < max_seconds:
        return run
    error = f"Graph run reached platform ceiling ATLAS_GRAPH_MAX_RUN_SECONDS={max_seconds}."

    def fail(value: GraphRun) -> GraphRun:
        if value.status in _TERMINAL_RUNS:
            return value
        return value.model_copy(update={
            "status": "failed",
            "completed_at": now,
            "error": error,
            "error_count": value.error_count + 1,
        })

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


async def settle_request(*, runs, requests, progress, request_id: UUID, status: str, error: str | None = None, now: datetime | None = None, expected_claim_token: UUID | None = None, failure_stage: str | None = None) -> CrawlRequest:
    now = now or datetime.now(UTC)
    became_terminal = False
    def settle(request: CrawlRequest) -> CrawlRequest:
        nonlocal became_terminal
        if request.status in _TERMINAL_REQUESTS:
            return request
        if expected_claim_token is not None and request.claim_token != expected_claim_token:
            return request
        became_terminal = True
        return request.model_copy(update={
            "status": status,
            "error": error,
            "failure_stage": failure_stage if status == "failed" else None,
            "claim_token": None,
            "claim_expires_at": None,
            "updated_at": now,
        })
    previous_status: str | None = None
    def tracked_settle(request: CrawlRequest) -> CrawlRequest:
        nonlocal previous_status
        previous_status = request.status
        return settle(request)
    request = await update_crawl_request(requests, request_id, tracked_settle)
    if became_terminal:
        await _project(transition_node_progress(progress, request, previous_status=previous_status))
        def account(run: GraphRun) -> GraphRun:
            pending = max(0, run.pending_request_count - 1)
            failures = run.failed_request_count + (1 if status == "failed" else 0)
            errors = run.error_count + (1 if status == "failed" else 0)
            update = {
                "pending_request_count": pending,
                "failed_request_count": failures,
                "error_count": errors,
                "last_progress_at": now,
            }
            if pending == 0 and run.status not in _TERMINAL_RUNS:
                final = "completed_with_errors" if failures else "completed"
                return run.model_copy(
                    update={**update, "status": final, "completed_at": now}
                )
            return run.model_copy(update=update)
        run = await update_graph_run(runs, request.graph_run_id, account)
        if run.status in _TERMINAL_RUNS:
            await _project(mark_run_progress_settled(progress, run))
    return request


async def handle_readiness(*, runs, requests, progress, jetstream, event: ReadinessWork) -> None:
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
    outgoing = [edge for edge in run.snapshot.edges if edge.source_node_id == request.node_id]
    if event.navigation is None or not outgoing:
        await settle_request(runs=runs, requests=requests, progress=progress, request_id=request.id, status="completed")
        return
    previous_status: str | None = None
    def evaluate(current: CrawlRequest) -> CrawlRequest:
        nonlocal previous_status
        previous_status = current.status
        return current if current.status == "evaluating_edges" else current.model_copy(update={"status": "evaluating_edges", "updated_at": datetime.now(UTC)})
    request = await update_crawl_request(requests, request.id, evaluate)
    if previous_status != request.status:
        await _project(transition_node_progress(progress, request, previous_status=previous_status))
    for edge in outgoing:
        assert event.navigation is not None
        work = EdgeWork(
            graph_run_id=run.id,
            crawl_request_id=request.id,
            crawl_id=event.crawl_id,
            edge_id=edge.id,
            navigation=event.navigation,
        )
        identity = edge_evaluation_identity(run.id, request.id, event.crawl_id, edge.id)
        evaluation = EdgeEvaluation(identity=identity, graph_run_id=run.id, crawl_request_id=request.id, crawl_id=event.crawl_id, edge_id=edge.id, created_at=datetime.now(UTC), updated_at=datetime.now(UTC))
        try:
            await requests.create(edge_evaluation_key(identity), evaluation.model_dump_json().encode())
            await _project(transition_edge_evaluation_progress(progress, evaluation, previous_status=None))
        except Exception:
            existing = await get_edge_evaluation(requests, identity)
            if existing is None:
                raise
        await publish_edge(jetstream, work)


async def evaluate_edge(*, runs, requests, progress, jetstream, work: EdgeWork, execute_urls: Callable[[str, Mapping[str, object]], Iterable[str]], policy_resolver: Callable[[str], dict], claim_token: UUID | None = None) -> int:
    claim_token = claim_token or uuid4()
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
            await _project(transition_edge_evaluation_progress(progress, evaluation, previous_status=None))
        except Exception:
            evaluation = await get_edge_evaluation(requests, identity)
            if evaluation is None:
                raise
    previous_evaluation_status: str | None = None
    claimed = False
    def start_evaluation(value: EdgeEvaluation) -> EdgeEvaluation:
        nonlocal previous_evaluation_status, claimed
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
        return value.model_copy(update={
            "status": "running",
            "claim_token": claim_token,
            "claim_expires_at": now + timedelta(
                seconds=GRAPH_ACK_WAIT_SECONDS * 2
            ),
            "updated_at": now,
        })
    evaluation = await update_edge_evaluation(requests, identity, start_evaluation)
    if not claimed:
        raise EdgeEvaluationBusy(f"edge evaluation {identity} is already claimed")
    if previous_evaluation_status != evaluation.status:
        await _project(transition_edge_evaluation_progress(progress, evaluation, previous_status=previous_evaluation_status))
    count = evaluation.output_count
    seen = 0
    batch_selected = 0
    batch_admitted = 0
    try:
        if "$crawl_id" not in edge.sql:
            raise ValueError("Edge SQL must contain $crawl_id.")
        query = asyncio.create_task(asyncio.to_thread(
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
        ))
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
        for url in urls:
            seen += 1
            if seen <= count:
                continue
            count += 1
            batch_selected += 1
            _target, newly_admitted = await admit_request(runs=runs, requests=requests, progress=progress, jetstream=jetstream, run_id=run.id, node_id=edge.target_node_id, url=str(url), policy_resolver=policy_resolver, dedupe_mode=edge.dedupe_mode, source_crawl_id=work.crawl_id, source_document_id=request.document_id, source_edge_id=edge.id, parent_request_id=request.id)
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
                await _project(add_edge_output_progress(
                    progress,
                    run.id,
                    edge.id,
                    selected=batch_selected,
                    admitted=batch_admitted,
                    deduplicated=batch_selected - batch_admitted,
                ))
                batch_selected = 0
                batch_admitted = 0
    except Exception as exc:
        previous_evaluation_status = evaluation.status
        evaluation = await update_edge_evaluation(requests, identity, lambda value: value if value.claim_token != claim_token else value.model_copy(update={"status": "failed", "error": str(exc), "claim_token": None, "claim_expires_at": None, "updated_at": datetime.now(UTC)}))
        if evaluation.status != "failed":
            raise EdgeEvaluationBusy(
                f"edge evaluation {identity} was reclaimed while failing"
            ) from exc
        await _project(transition_edge_evaluation_progress(progress, evaluation, previous_status=previous_evaluation_status))
        await settle_request(runs=runs, requests=requests, progress=progress, request_id=request.id, status="failed", error=f"Edge {edge.name} failed: {exc}", failure_stage="edge")
        raise EdgeEvaluationFailed(str(exc)) from exc
    previous_evaluation_status = evaluation.status
    evaluation = await update_edge_evaluation(requests, identity, lambda value: value if value.claim_token != claim_token else value.model_copy(update={"status": "completed", "output_count": count, "error": None, "claim_token": None, "claim_expires_at": None, "updated_at": datetime.now(UTC)}))
    if evaluation.status != "completed":
        raise EdgeEvaluationBusy(
            f"edge evaluation {identity} was reclaimed before completion"
        )
    if batch_selected:
        await _project(add_edge_output_progress(
            progress,
            run.id,
            edge.id,
            selected=batch_selected,
            admitted=batch_admitted,
            deduplicated=batch_selected - batch_admitted,
        ))
    await _project(transition_edge_evaluation_progress(progress, evaluation, previous_status=previous_evaluation_status))
    outgoing = [value for value in run.snapshot.edges if value.source_node_id == request.node_id]
    evaluations = [await get_edge_evaluation(requests, edge_evaluation_identity(run.id, request.id, work.crawl_id, value.id)) for value in outgoing]
    if evaluations and all(value is not None and value.status == "completed" for value in evaluations):
        await settle_request(runs=runs, requests=requests, progress=progress, request_id=request.id, status="completed")
    return count
