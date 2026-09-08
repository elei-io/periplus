"""Postgres-owned generation publication and committed-batch receipts."""
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select

from periplus.materialization.models import MaterializationAppliedBatchRecord, MaterializationStateRecord, MaterializationRunRecord
from periplus.platform.postgres.session import session_scope
from periplus.retention.identities import _insert


@dataclass(frozen=True, slots=True)
class ActiveGeneration:
    id: UUID
    covered_snapshot: int
    batch_size: int
    registry_digest: str


def active_generation() -> ActiveGeneration | None:
    with session_scope() as session:
        row = session.get(MaterializationStateRecord, 1)
        return None if row is None else ActiveGeneration(
            row.generation_id, row.covered_snapshot, row.batch_size, row.registry_digest)


def publish_generation(run, covered_snapshot: int) -> None:
    with session_scope() as session:
        values = dict(id=1, generation_id=run.id, covered_snapshot=covered_snapshot,
                      batch_size=run.batch_size, registry_digest=run.registry_digest,
                      activated_at=datetime.now(UTC))
        statement = _insert(session, MaterializationStateRecord).values(**values)
        session.execute(statement.on_conflict_do_update(index_elements=['id'], set_=values))


def invalidate_generation(generation_id: UUID) -> bool:
    with session_scope() as session:
        result = session.execute(delete(MaterializationStateRecord).where(
            MaterializationStateRecord.id == 1,
            MaterializationStateRecord.generation_id == generation_id))
        return bool(result.rowcount)


def cover_snapshot(generation_id: UUID, snapshot: int) -> bool:
    with session_scope() as session:
        row = session.get(MaterializationStateRecord, 1, with_for_update=True)
        if row is None or row.generation_id != generation_id:
            return False
        row.covered_snapshot = max(row.covered_snapshot, snapshot)
        return True


def applied_batch(batch_id: UUID) -> tuple[int, int, int, int] | None:
    with session_scope() as session:
        row = session.get(MaterializationAppliedBatchRecord, batch_id)
        return None if row is None else (row.source_items, row.source_bytes, row.output_rows, row.output_bytes)


def applied_batches(batch_ids: tuple[UUID, ...]) -> frozenset[UUID]:
    if not batch_ids:
        return frozenset()
    with session_scope() as session:
        return frozenset(session.scalars(select(MaterializationAppliedBatchRecord.batch_id).where(
            MaterializationAppliedBatchRecord.batch_id.in_(batch_ids))))


def record_applied(run_id: UUID, batch_id: UUID, snapshot: int, result) -> None:
    # The lake write is repeatable if this short Postgres transaction fails.
    with session_scope() as session:
        session.execute(_insert(session, MaterializationAppliedBatchRecord).values(
            run_id=run_id, batch_id=batch_id, source_snapshot=snapshot,
            source_items=result.source_items, source_bytes=result.source_bytes,
            output_rows=result.output_rows, output_bytes=result.output_bytes,
            committed_at=datetime.now(UTC),
        ).on_conflict_do_nothing(index_elements=['batch_id']))


def readable_generation() -> ActiveGeneration | None:
    with session_scope() as session:
        if session.scalar(select(MaterializationRunRecord.id).where(
            MaterializationRunRecord.status == "activating").limit(1)) is not None:
            return None
        row = session.get(MaterializationStateRecord, 1)
        return None if row is None else ActiveGeneration(
            row.generation_id, row.covered_snapshot, row.batch_size, row.registry_digest)
