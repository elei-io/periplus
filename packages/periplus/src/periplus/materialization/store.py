"""Transactional rebuild coordination; JetStream remains delivery only."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from periplus.materialization.models import (
    MaterializationBatchRecord,
    MaterializationRunRecord,
)
from periplus.materialization.registry import REGISTRY_DIGEST
from periplus.platform.postgres.session import session_scope
from sqlalchemy import select, text
from periplus.platform.catalogue.exceptions import CatalogueConflictError

RunStatus = Literal[
    "queued",
    "planning",
    "running",
    "activating",
    "completed",
    "failed",
]


class MaterializationRunStopped(CatalogueConflictError):
    """A delayed rebuild writer must not touch a terminal generation."""


class MaterializationRunActive(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MaterializationRun:
    id: UUID
    status: RunStatus
    source_snapshot: int
    covered_snapshot: int
    activation_snapshot: int | None
    generation_tables: dict[str, str]
    registry_digest: str
    batch_size: int
    total_batches: int
    completed_batches: int
    source_items: int
    source_bytes: int
    output_rows: int
    output_bytes: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None


@dataclass(frozen=True, slots=True)
class MaterializationBatch:
    id: UUID
    run_id: UUID
    ordinal: int
    snapshot: int
    visit_ids: tuple[str, ...]
    status: str
    attempts: int


class MaterializationRunStore:
    def assert_writable(self, run_id: UUID) -> None:
        """Call under the generation claim, before any rebuild lake write."""
        run = self.get(run_id)
        if run is None or run.status not in {"running", "activating"}:
            raise MaterializationRunStopped(f"rebuild {run_id} is no longer writable")

    def create(
        self,
        *,
        source_snapshot: int,
        batch_size: int,
    ) -> MaterializationRun:
        with session_scope() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('periplus-materialization-rebuild'))"
                )
            )
            active = session.scalar(
                select(MaterializationRunRecord.id)
                .where(
                    MaterializationRunRecord.status.in_(
                        ("queued", "planning", "running", "activating")
                    )
                )
                .limit(1)
            )
            if active is not None:
                raise MaterializationRunActive(
                    f"materialization rebuild {active} is already active"
                )
            record = MaterializationRunRecord(
                status="queued",
                source_snapshot=source_snapshot,
                covered_snapshot=source_snapshot,
                batch_size=batch_size,
                registry_digest=REGISTRY_DIGEST,
            )
            session.add(record)
            session.flush()
            return _run(record)

    def ensure_rebuild(
        self,
        *,
        source_snapshot: int,
        batch_size: int,
    ) -> tuple[MaterializationRun, bool]:
        """Return the active rebuild or durably create one."""

        with session_scope() as session:
            session.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext('periplus-materialization-rebuild'))"
                )
            )
            active = session.scalar(
                select(MaterializationRunRecord)
                .where(
                    MaterializationRunRecord.status.in_(
                        ("queued", "planning", "running", "activating")
                    )
                )
                .limit(1)
            )
            if active is not None:
                return _run(active), False
            record = MaterializationRunRecord(
                status="queued",
                source_snapshot=source_snapshot,
                covered_snapshot=source_snapshot,
                batch_size=batch_size,
                registry_digest=REGISTRY_DIGEST,
            )
            session.add(record)
            session.flush()
            return _run(record), True

    def get(self, run_id: UUID) -> MaterializationRun | None:
        with session_scope() as session:
            record = session.get(MaterializationRunRecord, run_id)
            return _run(record) if record is not None else None

    def list(self, *, limit: int = 100) -> list[MaterializationRun]:
        with session_scope() as session:
            records = session.scalars(
                select(MaterializationRunRecord)
                .order_by(MaterializationRunRecord.created_at.desc())
                .limit(limit)
            ).all()
            return [_run(record) for record in records]

    def claim_plan(self, run_id: UUID) -> MaterializationRun | None:
        with session_scope() as session:
            record = session.scalar(
                select(MaterializationRunRecord)
                .where(MaterializationRunRecord.id == run_id)
                .with_for_update()
            )
            if record is None or record.status not in {"queued", "planning"}:
                return _run(record) if record is not None else None
            if record.status == "queued":
                record.status = "planning"
                record.started_at = datetime.now(timezone.utc)
            return _run(record)

    def mark_plan_published(self, run_id: UUID) -> None:
        with session_scope() as session:
            record = _required_run(session, run_id, lock=True)
            if record.plan_published_at is None:
                record.plan_published_at = datetime.now(timezone.utc)

    def finish_plan(
        self,
        run_id: UUID,
        *,
        generation_tables: dict[str, str],
        batches: list[tuple[int, list[str]]],
    ) -> tuple[MaterializationRun, tuple[MaterializationBatch, ...]]:
        with session_scope() as session:
            record = _required_run(session, run_id, lock=True)
            if record.status != "planning":
                return _run(record), ()
            if record.generation_tables:
                raise RuntimeError("rebuild generations were already planned")
            records = [
                MaterializationBatchRecord(
                    run_id=run_id,
                    ordinal=index,
                    snapshot=snapshot,
                    visit_ids=visit_ids,
                )
                for index, (snapshot, visit_ids) in enumerate(batches)
            ]
            session.add_all(records)
            record.generation_tables = generation_tables
            record.total_batches = len(records)
            record.status = "running"
            session.flush()
            return _run(record), tuple(_batch(item) for item in records)

    def get_batch(self, batch_id: UUID) -> MaterializationBatch | None:
        with session_scope() as session:
            record = session.get(MaterializationBatchRecord, batch_id)
            return _batch(record) if record is not None else None

    def mark_batch_published(self, batch_id: UUID) -> None:
        with session_scope() as session:
            record = session.scalar(
                select(MaterializationBatchRecord)
                .where(MaterializationBatchRecord.id == batch_id)
                .with_for_update()
            )
            if record is None:
                raise KeyError(batch_id)
            if record.published_at is None:
                record.published_at = datetime.now(timezone.utc)

    def start_batch(self, batch_id: UUID) -> MaterializationBatch | None:
        with session_scope() as session:
            record = session.scalar(
                select(MaterializationBatchRecord)
                .where(MaterializationBatchRecord.id == batch_id)
                .with_for_update()
            )
            if record is None or record.status == "completed":
                return _batch(record) if record is not None else None
            record.status = "running"
            record.attempts += 1
            record.started_at = datetime.now(timezone.utc)
            return _batch(record)

    def complete_batch(
        self,
        batch_id: UUID,
        *,
        source_items: int,
        source_bytes: int,
        output_rows: int,
        output_bytes: int,
    ) -> MaterializationRun:
        with session_scope() as session:
            batch = session.scalar(
                select(MaterializationBatchRecord)
                .where(MaterializationBatchRecord.id == batch_id)
                .with_for_update()
            )
            if batch is None:
                raise KeyError(batch_id)
            run = _required_run(session, batch.run_id, lock=True)
            if batch.status != "completed":
                batch.status = "completed"
                batch.completed_at = datetime.now(timezone.utc)
                batch.source_items = source_items
                batch.source_bytes = source_bytes
                batch.output_rows = output_rows
                batch.output_bytes = output_bytes
                run.completed_batches += 1
                run.source_items += source_items
                run.source_bytes += source_bytes
                run.output_rows += output_rows
                run.output_bytes += output_bytes
            return _run(run)

    def claim_activation(self, run_id: UUID) -> MaterializationRun | None:
        with session_scope() as session:
            record = _required_run(session, run_id, lock=True)
            if record.status == "activating":
                return _run(record)
            if (
                record.status != "running"
                or record.completed_batches != record.total_batches
            ):
                return None
            record.status = "activating"
            return _run(record)

    def mark_activation_published(
        self,
        run_id: UUID,
        *,
        completed_batches: int,
    ) -> None:
        with session_scope() as session:
            record = _required_run(session, run_id, lock=True)
            if (
                record.status in {"running", "activating"}
                and record.completed_batches == completed_batches
                and record.total_batches == completed_batches
                and record.activation_published_at is None
            ):
                record.activation_published_at = datetime.now(timezone.utc)

    def add_catchup(
        self,
        run_id: UUID,
        *,
        through_snapshot: int,
        visit_id_batches: list[list[str]],
    ) -> tuple[MaterializationBatch, ...]:
        with session_scope() as session:
            run = _required_run(session, run_id, lock=True)
            if run.status != "activating":
                raise RuntimeError("catch-up requires an activating rebuild")
            start = run.total_batches
            records = [
                MaterializationBatchRecord(
                    run_id=run_id,
                    ordinal=start + index,
                    snapshot=through_snapshot,
                    visit_ids=visit_ids,
                )
                for index, visit_ids in enumerate(visit_id_batches)
            ]
            session.add_all(records)
            run.total_batches += len(records)
            run.covered_snapshot = through_snapshot
            run.status = "running"
            run.activation_published_at = None
            session.flush()
            return tuple(_batch(item) for item in records)

    def complete(self, run_id: UUID, *, activation_snapshot: int) -> MaterializationRun:
        with session_scope() as session:
            record = _required_run(session, run_id, lock=True)
            if record.status != "activating":
                raise RuntimeError("only an activating rebuild can complete")
            record.status = "completed"
            record.activation_snapshot = activation_snapshot
            record.completed_at = datetime.now(timezone.utc)
            return _run(record)

    def fail(self, run_id: UUID, error: BaseException) -> MaterializationRun:
        with session_scope() as session:
            record = _required_run(session, run_id, lock=True)
            if record.status in {"completed", "failed"}:
                return _run(record)
            record.status = "failed"
            record.completed_at = datetime.now(timezone.utc)
            record.error = f"{type(error).__name__}: {error}"[:4000]
            return _run(record)

    def mark_cleanup_completed(self, run_id: UUID) -> None:
        with session_scope() as session:
            record = _required_run(session, run_id, lock=True)
            if record.status != "failed":
                raise RuntimeError("only failed rebuilds can be cleaned up")
            if record.cleanup_completed_at is None:
                record.cleanup_completed_at = datetime.now(timezone.utc)

    def recoverable(
        self,
    ) -> tuple[
        list[UUID],
        list[UUID],
        list[tuple[UUID, int]],
        list[MaterializationRun],
    ]:
        with session_scope() as session:
            plans = list(
                session.scalars(
                    select(MaterializationRunRecord.id).where(
                        MaterializationRunRecord.status.in_(("queued", "planning")),
                        MaterializationRunRecord.plan_published_at.is_(None),
                    )
                )
            )
            batches = list(
                session.scalars(
                    select(MaterializationBatchRecord.id)
                    .join(
                        MaterializationRunRecord,
                        MaterializationRunRecord.id
                        == MaterializationBatchRecord.run_id,
                    )
                    .where(
                        MaterializationBatchRecord.status.in_(("queued", "running")),
                        MaterializationBatchRecord.published_at.is_(None),
                        MaterializationRunRecord.status.in_(("running", "activating")),
                    )
                )
            )
            activations = list(
                session.execute(
                    select(
                        MaterializationRunRecord.id,
                        MaterializationRunRecord.completed_batches,
                    ).where(
                        MaterializationRunRecord.activation_published_at.is_(
                            None
                        ),
                        (
                            MaterializationRunRecord.status == "activating"
                        )
                        | (
                            (
                                MaterializationRunRecord.status == "running"
                            )
                            & (
                                MaterializationRunRecord.completed_batches
                                == MaterializationRunRecord.total_batches
                            )
                        )
                    )
                ).tuples()
            )
            cleanups = [
                _run(record)
                for record in session.scalars(
                    select(MaterializationRunRecord).where(
                        MaterializationRunRecord.status == "failed",
                        MaterializationRunRecord.cleanup_completed_at.is_(None),
                    )
                )
            ]
            return plans, batches, activations, cleanups


class AsyncMaterializationRunStore:
    def __init__(self, store: MaterializationRunStore | None = None) -> None:
        self._store = store or MaterializationRunStore()

    def __getattr__(self, name: str):
        method = getattr(self._store, name)

        async def call(*args, **kwargs):
            return await asyncio.to_thread(method, *args, **kwargs)

        return call


def _required_run(session, run_id: UUID, *, lock: bool) -> MaterializationRunRecord:
    statement = select(MaterializationRunRecord).where(
        MaterializationRunRecord.id == run_id
    )
    if lock:
        statement = statement.with_for_update()
    record = session.scalar(statement)
    if record is None:
        raise KeyError(run_id)
    return record


def _run(record: MaterializationRunRecord) -> MaterializationRun:
    return MaterializationRun(
        id=record.id,
        status=record.status,
        source_snapshot=record.source_snapshot,
        covered_snapshot=record.covered_snapshot,
        activation_snapshot=record.activation_snapshot,
        generation_tables=dict(record.generation_tables),
        registry_digest=record.registry_digest,
        batch_size=record.batch_size,
        total_batches=record.total_batches,
        completed_batches=record.completed_batches,
        source_items=record.source_items,
        source_bytes=record.source_bytes,
        output_rows=record.output_rows,
        output_bytes=record.output_bytes,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error=record.error,
    )


def _batch(record: MaterializationBatchRecord) -> MaterializationBatch:
    return MaterializationBatch(
        id=record.id,
        run_id=record.run_id,
        ordinal=record.ordinal,
        snapshot=record.snapshot,
        visit_ids=tuple(record.visit_ids),
        status=record.status,
        attempts=record.attempts,
    )
