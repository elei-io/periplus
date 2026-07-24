"""Short-transaction repository for authoritative graph execution state."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable
from uuid import UUID, uuid4

from sqlalchemy import Select, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config.performance import CRAWL_RUN_ACQUISITION_PENDING_LIMIT
from control.crawl_graphs.schemas import EdgeDedupeMode
from db.session import SessionLocal
from runtime.graph_models import (
    CrawlRequestRecord,
    EdgeEvaluationRecord,
    GraphAdmissionRecord,
    GraphOutboxRecord,
    GraphRunRecord,
)
from runtime.graph_queue import (
    CRAWL_SUBJECT,
    EDGE_SUBJECT,
    NAVIGATION_READINESS_SUBJECT,
    CrawlRequest,
    CrawlWork,
    EdgeEvaluation,
    EdgeWork,
    GraphRun,
    GraphRunFailureGroup,
    NavigationReadinessWork,
    request_identity,
)


class GraphRunNotFoundError(LookupError):
    pass


class GraphRunNotRunnableError(RuntimeError):
    pass


class GraphRunAdmissionDeferred(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    request: CrawlRequest | None
    created: bool
    limit_reached: bool = False


@dataclass(frozen=True, slots=True)
class OutboxDelivery:
    id: UUID
    message_id: str
    subject: str
    payload: dict
    claim_token: UUID


class GraphRuntimeStore:
    """All methods own one short transaction and never perform remote I/O."""

    def __init__(
        self,
        session_factory: Callable[[], Session] = SessionLocal,
    ) -> None:
        self._session_factory = session_factory

    def create_run(self, run: GraphRun) -> GraphRun:
        try:
            with self._session_factory() as session, session.begin():
                existing = session.get(GraphRunRecord, run.id)
                if existing is not None:
                    return _same_run_or_raise(_graph_run(existing), run)
                session.add(_graph_run_record(run))
                session.flush()
            return run
        except IntegrityError:
            existing = self.get_run(run.id)
            if existing is None:
                raise
            return _same_run_or_raise(existing, run)

    def get_run(self, run_id: UUID) -> GraphRun | None:
        with self._session_factory() as session:
            record = session.get(GraphRunRecord, run_id)
            return _graph_run(record) if record is not None else None

    def list_runs(self) -> list[GraphRun]:
        with self._session_factory() as session:
            records = session.scalars(
                select(GraphRunRecord).order_by(GraphRunRecord.created_at.desc())
            )
            return [_graph_run(record) for record in records]

    def delete_terminal_runs_before(
        self, cutoff: datetime, *, limit: int = 1000
    ) -> int:
        terminal = (
            "completed",
            "completed_with_errors",
            "failed",
            "cancelled",
        )
        with self._session_factory() as session, session.begin():
            run_ids = list(
                session.scalars(
                    select(GraphRunRecord.id)
                    .where(
                        GraphRunRecord.status.in_(terminal),
                        GraphRunRecord.completed_at.is_not(None),
                        GraphRunRecord.completed_at <= cutoff,
                    )
                    .order_by(GraphRunRecord.completed_at)
                    .limit(limit)
                )
            )
            if run_ids:
                session.execute(
                    delete(GraphRunRecord).where(
                        GraphRunRecord.id.in_(run_ids)
                    )
                )
            return len(run_ids)

    def get_request(self, request_id: UUID) -> CrawlRequest | None:
        with self._session_factory() as session:
            record = session.get(CrawlRequestRecord, request_id)
            return _crawl_request(record) if record is not None else None

    def update_run(self, run_id: UUID, mutate) -> GraphRun:
        with self._session_factory() as session, session.begin():
            record = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == run_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError(run_id)
            current = _graph_run(record)
            updated = mutate(current)
            if updated != current:
                _apply_graph_run(record, updated)
            return updated

    def pause_run(
        self, run_id: UUID, *, now: datetime | None = None
    ) -> GraphRun:
        now = now or datetime.now(UTC)

        def pause(run: GraphRun) -> GraphRun:
            if run.status in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
                "paused",
            }:
                return run
            return run.model_copy(
                update={"status": "paused", "paused_at": now}
            )

        return self.update_run(run_id, pause)

    def resume_run(
        self, run_id: UUID, *, now: datetime | None = None
    ) -> GraphRun:
        now = now or datetime.now(UTC)

        def resume(run: GraphRun) -> GraphRun:
            if run.status != "paused":
                return run
            return run.model_copy(
                update={
                    "status": "running",
                    "paused_at": None,
                    "last_progress_at": now,
                }
            )

        return self.update_run(run_id, resume)

    def cancel_run(
        self,
        run_id: UUID,
        *,
        error: str = "Graph run cancelled.",
        now: datetime | None = None,
    ) -> GraphRun:
        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            record = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == run_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError(run_id)
            if record.status in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }:
                return _graph_run(record)
            record.status = "cancelled"
            record.generation += 1
            record.cancel_requested_at = now
            record.completed_at = now
            record.last_progress_at = now
            record.error = error
            session.execute(
                update(CrawlRequestRecord)
                .where(
                    CrawlRequestRecord.graph_run_id == run_id,
                    CrawlRequestRecord.status.not_in(
                        ("completed", "failed", "cancelled")
                    ),
                )
                .values(
                    status="cancelled",
                    generation=CrawlRequestRecord.generation + 1,
                    claim_token=None,
                    claim_expires_at=None,
                    error=error,
                    updated_at=now,
                )
            )
            record.pending_request_count = 0
            record.acquisition_pending_count = 0
            return _graph_run(record)

    def fail_run(
        self,
        run_id: UUID,
        *,
        error: str,
        now: datetime | None = None,
    ) -> GraphRun:
        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            record = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == run_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError(run_id)
            if record.status in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }:
                return _graph_run(record)
            record.status = "failed"
            record.generation += 1
            record.completed_at = now
            record.last_progress_at = now
            record.error = error
            record.error_count += 1
            session.execute(
                update(CrawlRequestRecord)
                .where(
                    CrawlRequestRecord.graph_run_id == run_id,
                    CrawlRequestRecord.status.not_in(
                        ("completed", "failed", "cancelled")
                    ),
                )
                .values(
                    status="cancelled",
                    generation=CrawlRequestRecord.generation + 1,
                    claim_token=None,
                    claim_expires_at=None,
                    error=error,
                    updated_at=now,
                )
            )
            record.pending_request_count = 0
            record.acquisition_pending_count = 0
            return _graph_run(record)

    def settle_run_if_idle(
        self, run_id: UUID, *, now: datetime | None = None
    ) -> GraphRun:
        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            record = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == run_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError(run_id)
            roots_exhausted = (
                record.root_admission_cursor >= len(record.trigger_urls)
                or record.crawl_limit_reached
            )
            if (
                record.status in {"queued", "running"}
                and record.pending_request_count == 0
                and roots_exhausted
            ):
                record.status = (
                    "completed_with_errors"
                    if record.failed_request_count
                    else "completed"
                )
                record.completed_at = now
                record.last_progress_at = now
            return _graph_run(record)

    def update_request(self, request_id: UUID, mutate) -> CrawlRequest:
        with self._session_factory() as session, session.begin():
            record = session.scalar(
                select(CrawlRequestRecord)
                .where(CrawlRequestRecord.id == request_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError(request_id)
            current = _crawl_request(record)
            updated = mutate(current)
            if updated != current:
                _apply_crawl_request(record, updated)
            return updated

    def get_edge_evaluation(
        self, identity: str
    ) -> EdgeEvaluation | None:
        with self._session_factory() as session:
            record = session.get(EdgeEvaluationRecord, identity)
            return (
                _edge_evaluation(record) if record is not None else None
            )

    def create_edge_evaluation(
        self, evaluation: EdgeEvaluation
    ) -> tuple[EdgeEvaluation, bool]:
        try:
            with self._session_factory() as session, session.begin():
                session.add(
                    EdgeEvaluationRecord(
                        identity=evaluation.identity,
                        graph_run_id=evaluation.graph_run_id,
                        crawl_request_id=evaluation.crawl_request_id,
                        crawl_id=evaluation.crawl_id,
                        edge_id=evaluation.edge_id,
                        generation=1,
                        status=evaluation.status,
                        claim_token=evaluation.claim_token,
                        claim_expires_at=evaluation.claim_expires_at,
                        created_at=evaluation.created_at,
                        updated_at=evaluation.updated_at,
                        output_count=evaluation.output_count,
                        selection=(
                            evaluation.selection.model_dump(mode="json")
                            if evaluation.selection is not None
                            else None
                        ),
                        error=evaluation.error,
                    )
                )
            return evaluation, True
        except IntegrityError:
            existing = self.get_edge_evaluation(evaluation.identity)
            if existing is None:
                raise
            return existing, False

    def update_edge_evaluation(
        self, identity: str, mutate
    ) -> EdgeEvaluation:
        with self._session_factory() as session, session.begin():
            record = session.scalar(
                select(EdgeEvaluationRecord)
                .where(EdgeEvaluationRecord.identity == identity)
                .with_for_update()
            )
            if record is None:
                raise KeyError(identity)
            current = _edge_evaluation(record)
            updated = mutate(current)
            if updated != current:
                record.status = updated.status
                record.claim_token = updated.claim_token
                record.claim_expires_at = updated.claim_expires_at
                record.updated_at = updated.updated_at
                record.output_count = updated.output_count
                record.selection = (
                    updated.selection.model_dump(mode="json")
                    if updated.selection is not None
                    else None
                )
                record.error = updated.error
            return updated

    def list_requests(
        self, *, graph_run_id: UUID | None = None
    ) -> list[CrawlRequest]:
        with self._session_factory() as session:
            statement: Select[tuple[CrawlRequestRecord]] = select(
                CrawlRequestRecord
            ).order_by(CrawlRequestRecord.created_at)
            if graph_run_id is not None:
                statement = statement.where(
                    CrawlRequestRecord.graph_run_id == graph_run_id
                )
            return [
                _crawl_request(record)
                for record in session.scalars(statement)
            ]

    def admit_request(
        self,
        *,
        run_id: UUID,
        node_id: UUID,
        url: str,
        effective_policy_snapshot: dict,
        dedupe_mode: EdgeDedupeMode = EdgeDedupeMode.graph,
        source_crawl_id: UUID | None = None,
        source_document_id: str | None = None,
        source_edge_id: UUID | None = None,
        parent_request_id: UUID | None = None,
        priority: int = 0,
        not_before: datetime | None = None,
        now: datetime | None = None,
    ) -> AdmissionResult:
        """Reserve budget, deduplicate, create state, and enqueue work atomically."""

        from runtime.graph_runs import deterministic_request_id

        now = now or datetime.now(UTC)
        identity = request_identity(
            run_id,
            url,
            dedupe_mode=dedupe_mode,
            source_edge_id=source_edge_id,
            source_crawl_id=source_crawl_id,
            source_document_id=source_document_id,
        )
        graph_identity = request_identity(run_id, url)
        request_id = deterministic_request_id(identity)

        try:
            with self._session_factory() as session, session.begin():
                run = session.scalar(
                    select(GraphRunRecord)
                    .where(GraphRunRecord.id == run_id)
                    .with_for_update()
                )
                if run is None:
                    raise GraphRunNotFoundError(
                        f"Graph run {run_id} was not found."
                    )
                existing_admission = session.get(
                    GraphAdmissionRecord, identity
                )
                if existing_admission is not None:
                    request = session.get(
                        CrawlRequestRecord,
                        existing_admission.crawl_request_id,
                    )
                    return AdmissionResult(
                        _crawl_request(request) if request is not None else None,
                        False,
                    )
                if run.status == "paused":
                    raise GraphRunNotRunnableError(
                        f"Graph run {run_id} is paused."
                    )
                if run.status not in {"queued", "running"}:
                    return AdmissionResult(None, False)
                if run.not_before is not None and run.not_before > now:
                    raise GraphRunNotRunnableError(
                        f"Graph run {run_id} is deferred until {run.not_before.isoformat()}."
                    )
                if run.request_count >= run.max_crawls:
                    run.crawl_limit_reached = True
                    return AdmissionResult(None, False, limit_reached=True)
                if (
                    run.acquisition_pending_count
                    >= CRAWL_RUN_ACQUISITION_PENDING_LIMIT
                ):
                    raise GraphRunAdmissionDeferred(
                        f"graph run {run_id} has "
                        f"{run.acquisition_pending_count} acquisition requests pending"
                    )

                request = CrawlRequestRecord(
                    id=request_id,
                    graph_run_id=run_id,
                    identity=identity,
                    node_id=node_id,
                    url=url,
                    effective_policy_snapshot=effective_policy_snapshot,
                    source_crawl_id=source_crawl_id,
                    source_edge_id=source_edge_id,
                    parent_request_id=parent_request_id,
                    generation=run.generation,
                    priority=priority,
                    not_before=not_before,
                    created_at=now,
                    updated_at=now,
                )
                session.add(request)
                session.flush()
                session.add(
                    GraphAdmissionRecord(
                        identity=identity,
                        graph_run_id=run_id,
                        crawl_request_id=request_id,
                        created_at=now,
                    )
                )
                if graph_identity != identity:
                    session.add(
                        GraphAdmissionRecord(
                            identity=graph_identity,
                            graph_run_id=run_id,
                            crawl_request_id=request_id,
                            created_at=now,
                        )
                    )
                work = CrawlWork(
                    crawl_request_id=request_id,
                    generation=request.generation,
                )
                session.add(
                    GraphOutboxRecord(
                        graph_run_id=run_id,
                        message_id=_crawl_message_id(
                            request_id, request.generation
                        ),
                        subject=CRAWL_SUBJECT,
                        payload=work.model_dump(mode="json"),
                        created_at=now,
                        not_before=not_before,
                    )
                )
                run.status = "running"
                run.started_at = run.started_at or now
                run.last_progress_at = now
                run.request_count += 1
                run.pending_request_count += 1
                run.acquisition_pending_count += 1
                result = _crawl_request(request)
            return AdmissionResult(result, True)
        except IntegrityError:
            # A concurrent transaction may have inserted either dedupe identity.
            with self._session_factory() as session:
                admission = session.get(GraphAdmissionRecord, identity)
                if admission is None:
                    admission = session.get(
                        GraphAdmissionRecord, graph_identity
                    )
                if admission is None:
                    raise
                request = session.get(
                    CrawlRequestRecord, admission.crawl_request_id
                )
                return AdmissionResult(
                    _crawl_request(request) if request is not None else None,
                    False,
                )

    def complete_acquisition(
        self,
        *,
        request_id: UUID,
        generation: int,
        claim_token: UUID,
        document_id: str | None,
        acquisition_attempts: tuple[dict, ...],
        readiness: NavigationReadinessWork,
        now: datetime | None = None,
    ) -> tuple[CrawlRequest, bool]:
        """Checkpoint acquisition and its navigation wakeup in one commit."""

        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            request = session.scalar(
                select(CrawlRequestRecord)
                .where(CrawlRequestRecord.id == request_id)
                .with_for_update()
            )
            if request is None:
                raise KeyError(request_id)
            run = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == request.graph_run_id)
                .with_for_update()
            )
            if run is None:
                raise KeyError(request.graph_run_id)
            if (
                run.status
                in {
                    "completed",
                    "completed_with_errors",
                    "failed",
                    "cancelled",
                }
                or
                request.generation != generation
                or request.status != "crawling"
                or request.claim_token != claim_token
            ):
                return _crawl_request(request), False
            if readiness.generation != generation:
                raise ValueError(
                    "navigation readiness generation does not match acquisition"
                )
            request.document_id = document_id
            request.acquisition_attempts = list(acquisition_attempts)
            request.status = "awaiting_navigation"
            request.claim_token = None
            request.claim_expires_at = None
            request.updated_at = now
            run.acquisition_pending_count = max(
                0, run.acquisition_pending_count - 1
            )
            run.last_progress_at = now
            session.add(
                GraphOutboxRecord(
                    graph_run_id=run.id,
                    message_id=(
                        f"navigation:{request.id.hex}:"
                        f"g{generation}:{readiness.crawl_id.hex}"
                    ),
                    subject=NAVIGATION_READINESS_SUBJECT,
                    payload=readiness.model_dump(mode="json"),
                    created_at=now,
                )
            )
            return _crawl_request(request), True

    def settle_request(
        self,
        *,
        request_id: UUID,
        status: str,
        error: str | None = None,
        expected_claim_token: UUID | None = None,
        failure_stage: str | None = None,
        failure_code: str | None = None,
        status_code: int | None = None,
        now: datetime | None = None,
    ) -> CrawlRequest:
        """Settle request state and aggregate run state in one transaction."""

        from runtime.graph_runs import _updated_failure_groups

        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            request_record = session.scalar(
                select(CrawlRequestRecord)
                .where(CrawlRequestRecord.id == request_id)
                .with_for_update()
            )
            if request_record is None:
                raise KeyError(request_id)
            if request_record.status in {
                "completed",
                "failed",
                "cancelled",
            }:
                return _crawl_request(request_record)
            if (
                expected_claim_token is not None
                and request_record.claim_token != expected_claim_token
            ):
                return _crawl_request(request_record)
            run_record = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == request_record.graph_run_id)
                .with_for_update()
            )
            if run_record is None:
                raise KeyError(request_record.graph_run_id)
            previous_status = request_record.status
            request_record.status = status
            request_record.error = error
            request_record.failure_stage = (
                failure_stage if status == "failed" else None
            )
            request_record.claim_token = None
            request_record.claim_expires_at = None
            request_record.updated_at = now
            run_record.pending_request_count = max(
                0, run_record.pending_request_count - 1
            )
            if previous_status in {"queued", "crawling"}:
                run_record.acquisition_pending_count = max(
                    0, run_record.acquisition_pending_count - 1
                )
            if status == "failed":
                run = _graph_run(run_record)
                request = _crawl_request(request_record)
                run_record.failed_request_count += 1
                run_record.error_count += 1
                run_record.failure_groups = [
                    group.model_dump(mode="json")
                    for group in _updated_failure_groups(
                        run,
                        request=request,
                        failure_stage=failure_stage,
                        failure_code=failure_code,
                        status_code=status_code,
                        detail=error,
                        occurred_at=now,
                    )
                ]
            run_record.last_progress_at = now
            if (
                run_record.pending_request_count == 0
                and (
                    run_record.root_admission_cursor
                    >= len(run_record.trigger_urls)
                    or run_record.crawl_limit_reached
                )
                and run_record.status not in {"paused", "cancelled", "failed"}
            ):
                run_record.status = (
                    "completed_with_errors"
                    if run_record.failed_request_count
                    else "completed"
                )
                run_record.completed_at = now
            return _crawl_request(request_record)

    def activate_navigation(
        self,
        *,
        event: NavigationReadinessWork,
        edges: tuple[tuple[EdgeEvaluation, EdgeWork], ...],
        now: datetime | None = None,
    ) -> str:
        """Advance readiness and enqueue every outgoing edge atomically."""

        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            request = session.scalar(
                select(CrawlRequestRecord)
                .where(CrawlRequestRecord.id == event.crawl_request_id)
                .with_for_update()
            )
            if (
                request is None
                or request.graph_run_id != event.graph_run_id
                or request.generation != event.generation
                or request.status
                in {"completed", "failed", "cancelled"}
            ):
                return "stale"
            run = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == event.graph_run_id)
                .with_for_update()
            )
            if run is None or run.status in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }:
                return "stale"
            if run.status == "paused":
                return "paused"
            if request.status == "evaluating_edges":
                return "duplicate"
            if request.status != "awaiting_navigation":
                return "stale"
            if not edges:
                request.status = "completed"
                request.updated_at = now
                run.pending_request_count = max(
                    0, run.pending_request_count - 1
                )
                run.last_progress_at = now
                if (
                    run.pending_request_count == 0
                    and (
                        run.root_admission_cursor >= len(run.trigger_urls)
                        or run.crawl_limit_reached
                    )
                ):
                    run.status = (
                        "completed_with_errors"
                        if run.failed_request_count
                        else "completed"
                    )
                    run.completed_at = now
                return "completed"
            request.status = "evaluating_edges"
            request.updated_at = now
            for evaluation, work in edges:
                existing = session.get(
                    EdgeEvaluationRecord, evaluation.identity
                )
                if existing is None:
                    session.add(
                        EdgeEvaluationRecord(
                            identity=evaluation.identity,
                            graph_run_id=evaluation.graph_run_id,
                            crawl_request_id=evaluation.crawl_request_id,
                            crawl_id=evaluation.crawl_id,
                            edge_id=evaluation.edge_id,
                            generation=work.generation,
                            status="pending",
                            created_at=evaluation.created_at,
                            updated_at=evaluation.updated_at,
                        )
                    )
                outbox = session.scalar(
                    select(GraphOutboxRecord.id).where(
                        GraphOutboxRecord.message_id
                        == _edge_message_id(
                            evaluation.identity, work.generation
                        )
                    )
                )
                if outbox is None:
                    session.add(
                        GraphOutboxRecord(
                            graph_run_id=run.id,
                            message_id=_edge_message_id(
                                evaluation.identity, work.generation
                            ),
                            subject=EDGE_SUBJECT,
                            payload=work.model_dump(mode="json"),
                            created_at=now,
                        )
                    )
            run.last_progress_at = now
            return "activated"

    def finish_edge(
        self,
        *,
        identity: str,
        claim_token: UUID,
        status: str,
        output_count: int,
        error: str | None = None,
        request_error: str | None = None,
        now: datetime | None = None,
    ) -> tuple[EdgeEvaluation, bool]:
        """Finish one edge and, when appropriate, its request atomically."""

        from runtime.graph_runs import _updated_failure_groups

        if status not in {"completed", "failed"}:
            raise ValueError("edge terminal status is invalid")
        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            evaluation_record = session.scalar(
                select(EdgeEvaluationRecord)
                .where(EdgeEvaluationRecord.identity == identity)
                .with_for_update()
            )
            if evaluation_record is None:
                raise KeyError(identity)
            if evaluation_record.status in {"completed", "failed"}:
                return _edge_evaluation(evaluation_record), False
            if evaluation_record.claim_token != claim_token:
                return _edge_evaluation(evaluation_record), False
            request_record = session.scalar(
                select(CrawlRequestRecord)
                .where(
                    CrawlRequestRecord.id
                    == evaluation_record.crawl_request_id
                )
                .with_for_update()
            )
            if request_record is None:
                raise KeyError(evaluation_record.crawl_request_id)
            run_record = session.scalar(
                select(GraphRunRecord)
                .where(GraphRunRecord.id == evaluation_record.graph_run_id)
                .with_for_update()
            )
            if run_record is None:
                raise KeyError(evaluation_record.graph_run_id)
            evaluation_record.status = status
            evaluation_record.output_count = output_count
            evaluation_record.error = error
            evaluation_record.claim_token = None
            evaluation_record.claim_expires_at = None
            evaluation_record.updated_at = now
            request_settled = False
            if request_record.status not in {
                "completed",
                "failed",
                "cancelled",
            }:
                should_settle = status == "failed"
                request_status = "failed" if status == "failed" else "completed"
                if status == "completed":
                    session.flush()
                    remaining = session.scalar(
                        select(func.count())
                        .select_from(EdgeEvaluationRecord)
                        .where(
                            EdgeEvaluationRecord.crawl_request_id
                            == request_record.id,
                            EdgeEvaluationRecord.status != "completed",
                        )
                    )
                    should_settle = remaining == 0
                if should_settle:
                    request_record.status = request_status
                    request_record.error = (
                        request_error if status == "failed" else None
                    )
                    request_record.failure_stage = (
                        "edge" if status == "failed" else None
                    )
                    request_record.updated_at = now
                    run_record.pending_request_count = max(
                        0, run_record.pending_request_count - 1
                    )
                    if status == "failed":
                        run = _graph_run(run_record)
                        request = _crawl_request(request_record)
                        run_record.failed_request_count += 1
                        run_record.error_count += 1
                        run_record.failure_groups = [
                            group.model_dump(mode="json")
                            for group in _updated_failure_groups(
                                run,
                                request=request,
                                failure_stage="edge",
                                failure_code="edge_evaluation_failed",
                                status_code=None,
                                detail=request_error,
                                occurred_at=now,
                            )
                        ]
                    run_record.last_progress_at = now
                    if (
                        run_record.pending_request_count == 0
                        and (
                            run_record.root_admission_cursor
                            >= len(run_record.trigger_urls)
                            or run_record.crawl_limit_reached
                        )
                        and run_record.status
                        not in {"paused", "cancelled", "failed"}
                    ):
                        run_record.status = (
                            "completed_with_errors"
                            if run_record.failed_request_count
                            else "completed"
                        )
                        run_record.completed_at = now
                    request_settled = True
            return _edge_evaluation(evaluation_record), request_settled

    def claim_outbox(
        self,
        *,
        batch: int = 100,
        lease_seconds: float = 30,
        now: datetime | None = None,
    ) -> list[OutboxDelivery]:
        if batch < 1:
            raise ValueError("outbox batch must be positive")
        now = now or datetime.now(UTC)
        claim_token = uuid4()
        with self._session_factory() as session, session.begin():
            records = list(
                session.scalars(
                    select(GraphOutboxRecord)
                    .join(
                        GraphRunRecord,
                        GraphRunRecord.id
                        == GraphOutboxRecord.graph_run_id,
                    )
                    .where(
                        GraphRunRecord.status.in_(("queued", "running")),
                        GraphOutboxRecord.published_at.is_(None),
                        or_(
                            GraphOutboxRecord.not_before.is_(None),
                            GraphOutboxRecord.not_before <= now,
                        ),
                        or_(
                            GraphOutboxRecord.claim_expires_at.is_(None),
                            GraphOutboxRecord.claim_expires_at <= now,
                        ),
                    )
                    .order_by(GraphOutboxRecord.created_at)
                    .limit(batch)
                    .with_for_update(
                        skip_locked=True, of=GraphOutboxRecord
                    )
                )
            )
            expires_at = now + timedelta(seconds=lease_seconds)
            for record in records:
                record.claim_token = claim_token
                record.claim_expires_at = expires_at
                record.publish_attempts += 1
            return [
                OutboxDelivery(
                    id=record.id,
                    message_id=record.message_id,
                    subject=record.subject,
                    payload=record.payload,
                    claim_token=claim_token,
                )
                for record in records
            ]

    def mark_outbox_published(
        self,
        delivery: OutboxDelivery,
        *,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        with self._session_factory() as session, session.begin():
            record = session.get(GraphOutboxRecord, delivery.id)
            if (
                record is None
                or record.published_at is not None
                or record.claim_token != delivery.claim_token
            ):
                return False
            record.published_at = now
            record.claim_token = None
            record.claim_expires_at = None
            record.last_error = None
            return True

    def release_outbox(
        self,
        delivery: OutboxDelivery,
        error: Exception,
    ) -> bool:
        with self._session_factory() as session, session.begin():
            record = session.get(GraphOutboxRecord, delivery.id)
            if (
                record is None
                or record.published_at is not None
                or record.claim_token != delivery.claim_token
            ):
                return False
            record.claim_token = None
            record.claim_expires_at = None
            record.last_error = str(error)[:4000]
            return True

    def progress_counts(
        self, run_id: UUID
    ) -> dict[tuple[UUID, str], int]:
        with self._session_factory() as session:
            rows = session.execute(
                select(
                    CrawlRequestRecord.node_id,
                    CrawlRequestRecord.status,
                    func.count(),
                )
                .where(CrawlRequestRecord.graph_run_id == run_id)
                .group_by(
                    CrawlRequestRecord.node_id, CrawlRequestRecord.status
                )
            )
            return {
                (node_id, status): count
                for node_id, status, count in rows
            }

    def progress_snapshot(self, run_id: UUID) -> tuple[list, list]:
        from runtime.graph_progress import (
            EdgeProgress,
            NodeActivity,
            NodeProgress,
        )

        with self._session_factory() as session:
            run_record = session.get(GraphRunRecord, run_id)
            if run_record is None:
                raise GraphRunNotFoundError(
                    f"Graph run {run_id} was not found."
                )
            run = _graph_run(run_record)
            requests = list(
                session.scalars(
                    select(CrawlRequestRecord)
                    .where(CrawlRequestRecord.graph_run_id == run_id)
                    .order_by(CrawlRequestRecord.updated_at.desc())
                )
            )
            evaluations = list(
                session.scalars(
                    select(EdgeEvaluationRecord).where(
                        EdgeEvaluationRecord.graph_run_id == run_id
                    )
                )
            )
            terminal = run.status in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }
            nodes = []
            for node in run.snapshot.nodes:
                node_requests = [
                    request
                    for request in requests
                    if request.node_id == node.id
                ]
                counts = {
                    status: sum(
                        request.status == status
                        for request in node_requests
                    )
                    for status in (
                        "queued",
                        "crawling",
                        "awaiting_navigation",
                        "evaluating_edges",
                        "completed",
                        "failed",
                        "cancelled",
                    )
                }
                nodes.append(
                    NodeProgress(
                        graph_run_id=run_id,
                        node_id=node.id,
                        admitted=len(node_requests),
                        activity=tuple(
                            NodeActivity(
                                request_id=request.id,
                                status=request.status,
                                url=request.url,
                                updated_at=request.updated_at,
                                error=request.error,
                            )
                            for request in node_requests[:3]
                        ),
                        settled=terminal
                        or (
                            bool(node_requests)
                            and all(
                                request.status
                                in {"completed", "failed", "cancelled"}
                                for request in node_requests
                            )
                        ),
                        **counts,
                    )
                )
            edges = []
            for edge in run.snapshot.edges:
                edge_evaluations = [
                    evaluation
                    for evaluation in evaluations
                    if evaluation.edge_id == edge.id
                ]
                selected = sum(
                    evaluation.output_count
                    for evaluation in edge_evaluations
                )
                admitted = sum(
                    request.source_edge_id == edge.id
                    for request in requests
                )
                edges.append(
                    EdgeProgress(
                        graph_run_id=run_id,
                        edge_id=edge.id,
                        evaluations_pending=sum(
                            item.status == "pending"
                            for item in edge_evaluations
                        ),
                        evaluations_running=sum(
                            item.status == "running"
                            for item in edge_evaluations
                        ),
                        evaluations_completed=sum(
                            item.status == "completed"
                            for item in edge_evaluations
                        ),
                        evaluations_failed=sum(
                            item.status == "failed"
                            for item in edge_evaluations
                        ),
                        urls_selected=selected,
                        urls_admitted=admitted,
                        urls_deduplicated=max(0, selected - admitted),
                        settled=terminal
                        or (
                            bool(edge_evaluations)
                            and all(
                                item.status in {"completed", "failed"}
                                for item in edge_evaluations
                            )
                        ),
                    )
                )
            return nodes, edges


class AsyncGraphRuntimeStore:
    """Async adapter; every call checks out a connection only inside its thread."""

    def __init__(self, store: GraphRuntimeStore | None = None) -> None:
        self._store = store or GraphRuntimeStore()

    async def create_run(self, run: GraphRun) -> GraphRun:
        return await asyncio.to_thread(self._store.create_run, run)

    async def get_run(self, run_id: UUID) -> GraphRun | None:
        return await asyncio.to_thread(self._store.get_run, run_id)

    async def list_runs(self) -> list[GraphRun]:
        return await asyncio.to_thread(self._store.list_runs)

    async def delete_terminal_runs_before(
        self, cutoff: datetime, *, limit: int = 1000
    ) -> int:
        return await asyncio.to_thread(
            self._store.delete_terminal_runs_before,
            cutoff,
            limit=limit,
        )

    async def get_request(self, request_id: UUID) -> CrawlRequest | None:
        return await asyncio.to_thread(self._store.get_request, request_id)

    async def update_run(self, run_id: UUID, mutate) -> GraphRun:
        return await asyncio.to_thread(
            self._store.update_run, run_id, mutate
        )

    async def pause_run(self, run_id: UUID) -> GraphRun:
        return await asyncio.to_thread(self._store.pause_run, run_id)

    async def resume_run(self, run_id: UUID) -> GraphRun:
        return await asyncio.to_thread(self._store.resume_run, run_id)

    async def cancel_run(self, run_id: UUID) -> GraphRun:
        return await asyncio.to_thread(self._store.cancel_run, run_id)

    async def fail_run(self, run_id: UUID, *, error: str) -> GraphRun:
        return await asyncio.to_thread(
            self._store.fail_run, run_id, error=error
        )

    async def settle_run_if_idle(self, run_id: UUID) -> GraphRun:
        return await asyncio.to_thread(
            self._store.settle_run_if_idle, run_id
        )

    async def update_request(
        self, request_id: UUID, mutate
    ) -> CrawlRequest:
        return await asyncio.to_thread(
            self._store.update_request, request_id, mutate
        )

    async def get_edge_evaluation(
        self, identity: str
    ) -> EdgeEvaluation | None:
        return await asyncio.to_thread(
            self._store.get_edge_evaluation, identity
        )

    async def create_edge_evaluation(
        self, evaluation: EdgeEvaluation
    ) -> tuple[EdgeEvaluation, bool]:
        return await asyncio.to_thread(
            self._store.create_edge_evaluation, evaluation
        )

    async def update_edge_evaluation(
        self, identity: str, mutate
    ) -> EdgeEvaluation:
        return await asyncio.to_thread(
            self._store.update_edge_evaluation, identity, mutate
        )

    async def list_requests(
        self, *, graph_run_id: UUID | None = None
    ) -> list[CrawlRequest]:
        return await asyncio.to_thread(
            self._store.list_requests, graph_run_id=graph_run_id
        )

    async def admit_request(self, **kwargs) -> AdmissionResult:
        return await asyncio.to_thread(self._store.admit_request, **kwargs)

    async def complete_acquisition(
        self, **kwargs
    ) -> tuple[CrawlRequest, bool]:
        return await asyncio.to_thread(
            self._store.complete_acquisition, **kwargs
        )

    async def settle_request(self, **kwargs) -> CrawlRequest:
        return await asyncio.to_thread(
            self._store.settle_request, **kwargs
        )

    async def activate_navigation(self, **kwargs) -> str:
        return await asyncio.to_thread(
            self._store.activate_navigation, **kwargs
        )

    async def finish_edge(
        self, **kwargs
    ) -> tuple[EdgeEvaluation, bool]:
        return await asyncio.to_thread(self._store.finish_edge, **kwargs)

    async def claim_outbox(self, **kwargs) -> list[OutboxDelivery]:
        return await asyncio.to_thread(self._store.claim_outbox, **kwargs)

    async def mark_outbox_published(
        self, delivery: OutboxDelivery
    ) -> bool:
        return await asyncio.to_thread(
            self._store.mark_outbox_published, delivery
        )

    async def release_outbox(
        self, delivery: OutboxDelivery, error: Exception
    ) -> bool:
        return await asyncio.to_thread(
            self._store.release_outbox, delivery, error
        )

    async def progress_counts(
        self, run_id: UUID
    ) -> dict[tuple[UUID, str], int]:
        return await asyncio.to_thread(
            self._store.progress_counts, run_id
        )

    async def progress_snapshot(self, run_id: UUID) -> tuple[list, list]:
        return await asyncio.to_thread(
            self._store.progress_snapshot, run_id
        )


def _same_run_or_raise(current: GraphRun, requested: GraphRun) -> GraphRun:
    if (
        current.graph_id != requested.graph_id
        or current.snapshot != requested.snapshot
        or current.trigger_urls != requested.trigger_urls
        or current.trigger_kind != requested.trigger_kind
        or current.trigger_schedule_id != requested.trigger_schedule_id
        or current.catalogue_snapshot_id != requested.catalogue_snapshot_id
        or current.catalogue_consistency != requested.catalogue_consistency
        or current.max_crawls != requested.max_crawls
    ):
        raise ValueError(
            f"Graph run identity {requested.id} is already used by another trigger."
        )
    return current


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _graph_run_record(run: GraphRun) -> GraphRunRecord:
    return GraphRunRecord(
        id=run.id,
        graph_id=run.graph_id,
        trigger_kind=run.trigger_kind,
        trigger_schedule_id=run.trigger_schedule_id,
        catalogue_snapshot_id=run.catalogue_snapshot_id,
        catalogue_consistency=run.catalogue_consistency,
        generation=run.generation,
        status=run.status,
        snapshot=run.snapshot.model_dump(mode="json"),
        trigger_urls=list(run.trigger_urls),
        max_crawls=run.max_crawls,
        crawl_limit_reached=run.crawl_limit_reached,
        root_admission_cursor=run.root_admission_cursor,
        request_count=run.request_count,
        pending_request_count=run.pending_request_count,
        acquisition_pending_count=run.acquisition_pending_count,
        failed_request_count=run.failed_request_count,
        error_count=run.error_count,
        failure_groups=[
            group.model_dump(mode="json") for group in run.failure_groups
        ],
        created_at=run.created_at,
        started_at=run.started_at,
        last_progress_at=run.last_progress_at,
        completed_at=run.completed_at,
        paused_at=run.paused_at,
        not_before=run.not_before,
        deadline_at=run.deadline_at,
        cancel_requested_at=run.cancel_requested_at,
        error=run.error,
    )


def _graph_run(record: GraphRunRecord) -> GraphRun:
    return GraphRun(
        id=record.id,
        graph_id=record.graph_id,
        trigger_kind=record.trigger_kind,
        trigger_schedule_id=record.trigger_schedule_id,
        catalogue_snapshot_id=record.catalogue_snapshot_id,
        catalogue_consistency=record.catalogue_consistency,
        generation=record.generation,
        status=record.status,
        snapshot=record.snapshot,
        trigger_urls=tuple(record.trigger_urls),
        max_crawls=record.max_crawls,
        crawl_limit_reached=record.crawl_limit_reached,
        root_admission_cursor=record.root_admission_cursor,
        request_count=record.request_count,
        pending_request_count=record.pending_request_count,
        acquisition_pending_count=record.acquisition_pending_count,
        failed_request_count=record.failed_request_count,
        error_count=record.error_count,
        failure_groups=tuple(
            GraphRunFailureGroup.model_validate(group)
            for group in record.failure_groups
        ),
        created_at=_aware(record.created_at),
        started_at=_aware(record.started_at),
        last_progress_at=_aware(record.last_progress_at),
        completed_at=_aware(record.completed_at),
        paused_at=_aware(record.paused_at),
        not_before=_aware(record.not_before),
        deadline_at=_aware(record.deadline_at),
        cancel_requested_at=_aware(record.cancel_requested_at),
        error=record.error,
    )


def _crawl_request(record: CrawlRequestRecord) -> CrawlRequest:
    return CrawlRequest(
        id=record.id,
        graph_run_id=record.graph_run_id,
        node_id=record.node_id,
        url=record.url,
        document_id=record.document_id,
        effective_policy_snapshot_json=record.effective_policy_snapshot,
        source_crawl_id=record.source_crawl_id,
        source_edge_id=record.source_edge_id,
        parent_request_id=record.parent_request_id,
        status=record.status,
        generation=record.generation,
        priority=record.priority,
        not_before=_aware(record.not_before),
        claim_token=record.claim_token,
        claim_expires_at=_aware(record.claim_expires_at),
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
        error=record.error,
        failure_stage=record.failure_stage,
        processing_failure_count=record.processing_failure_count,
        acquisition_attempts_json=tuple(record.acquisition_attempts),
    )


def _apply_graph_run(record: GraphRunRecord, run: GraphRun) -> None:
    record.catalogue_snapshot_id = run.catalogue_snapshot_id
    record.catalogue_consistency = run.catalogue_consistency
    record.generation = run.generation
    record.status = run.status
    record.max_crawls = run.max_crawls
    record.crawl_limit_reached = run.crawl_limit_reached
    record.root_admission_cursor = run.root_admission_cursor
    record.request_count = run.request_count
    record.pending_request_count = run.pending_request_count
    record.acquisition_pending_count = run.acquisition_pending_count
    record.failed_request_count = run.failed_request_count
    record.error_count = run.error_count
    record.failure_groups = [
        group.model_dump(mode="json") for group in run.failure_groups
    ]
    record.started_at = run.started_at
    record.last_progress_at = run.last_progress_at
    record.completed_at = run.completed_at
    record.paused_at = run.paused_at
    record.not_before = run.not_before
    record.deadline_at = run.deadline_at
    record.cancel_requested_at = run.cancel_requested_at
    record.error = run.error


def _apply_crawl_request(
    record: CrawlRequestRecord, request: CrawlRequest
) -> None:
    record.document_id = request.document_id
    record.status = request.status
    record.generation = request.generation
    record.priority = request.priority
    record.not_before = request.not_before
    record.claim_token = request.claim_token
    record.claim_expires_at = request.claim_expires_at
    record.updated_at = request.updated_at
    record.error = request.error
    record.failure_stage = request.failure_stage
    record.processing_failure_count = request.processing_failure_count
    record.acquisition_attempts = list(request.acquisition_attempts_json)


def _edge_evaluation(record: EdgeEvaluationRecord) -> EdgeEvaluation:
    return EdgeEvaluation(
        identity=record.identity,
        graph_run_id=record.graph_run_id,
        crawl_request_id=record.crawl_request_id,
        crawl_id=record.crawl_id,
        edge_id=record.edge_id,
        status=record.status,
        claim_token=record.claim_token,
        claim_expires_at=_aware(record.claim_expires_at),
        created_at=_aware(record.created_at),
        updated_at=_aware(record.updated_at),
        output_count=record.output_count,
        selection=record.selection,
        error=record.error,
    )


def _crawl_message_id(request_id: UUID, generation: int) -> str:
    return f"crawl:{request_id.hex}:g{generation}"


def _edge_message_id(identity: str, generation: int) -> str:
    return f"edge:{identity}:g{generation}"
