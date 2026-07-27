"""Transactional control state for fixed materialization maintenance."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import select

from db.session import session_scope
from materialization.maintenance import (
    PROJECTOR_VERSIONS,
    ProjectionName,
    dependency_closure,
)
from materialization.models import MaterializationRunRecord


RunStatus = Literal["queued", "running", "completed", "failed"]
RunMode = Literal["backfill", "rebuild"]


@dataclass(frozen=True, slots=True)
class MaterializationRun:
    id: UUID
    mode: RunMode
    status: RunStatus
    requested_stages: tuple[ProjectionName, ...]
    stages: tuple[ProjectionName, ...]
    projector_versions: dict[str, int]
    source_snapshot: int
    catchup_snapshot: int
    catchup_target_snapshot: int | None
    catchup_stage: int
    catchup_cursors: dict[str, str | None]
    current_stage: int
    cursors: dict[str, str | None]
    destinations: dict[str, str]
    item_budget: int
    byte_budget: int
    source_items: int
    source_bytes: int
    output_rows: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    error: str | None

    @property
    def active_stage(self) -> ProjectionName | None:
        return (
            self.stages[self.current_stage]
            if self.current_stage < len(self.stages)
            else None
        )

    @property
    def active_catchup_stage(self) -> ProjectionName | None:
        return (
            self.stages[self.catchup_stage]
            if (
                self.catchup_target_snapshot is not None
                and self.catchup_stage < len(self.stages)
            )
            else None
        )


class MaterializationRunStore:
    def create(
        self,
        *,
        mode: RunMode,
        requested_stages: set[ProjectionName],
        source_snapshot: int,
        item_budget: int,
        byte_budget: int,
    ) -> MaterializationRun:
        stages = dependency_closure(requested_stages)
        if not stages:
            raise ValueError("at least one materialization stage is required")
        with session_scope() as session:
            record = MaterializationRunRecord(
                mode=mode,
                status="queued",
                requested_stages=sorted(requested_stages),
                stages=list(stages),
                projector_versions={
                    stage: PROJECTOR_VERSIONS[stage] for stage in stages
                },
                source_snapshot=source_snapshot,
                catchup_snapshot=source_snapshot,
                catchup_target_snapshot=None,
                catchup_stage=0,
                catchup_cursors={},
                current_stage=0,
                cursors={stage: None for stage in stages},
                destinations={},
                item_budget=item_budget,
                byte_budget=byte_budget,
                source_items=0,
                source_bytes=0,
                output_rows=0,
            )
            session.add(record)
            session.flush()
            return _run(record)

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

    def start(self, run_id: UUID) -> MaterializationRun:
        with session_scope() as session:
            record = _required(session, run_id)
            if record.status == "queued":
                record.status = "running"
                record.started_at = datetime.now(timezone.utc)
            return _run(record)

    def set_destinations(
        self,
        run_id: UUID,
        destinations: dict[ProjectionName, str],
    ) -> MaterializationRun:
        with session_scope() as session:
            record = _required(session, run_id)
            if record.status != "running":
                raise RuntimeError("only a running rebuild can set destinations")
            if record.destinations and record.destinations != destinations:
                raise RuntimeError("rebuild destinations are immutable")
            record.destinations = dict(destinations)
            return _run(record)

    def advance(
        self,
        run_id: UUID,
        *,
        stage: ProjectionName,
        cursor: str | None,
        done: bool,
        source_items: int,
        source_bytes: int,
        output_rows: int,
    ) -> MaterializationRun:
        with session_scope() as session:
            record = _required(session, run_id)
            if record.status != "running":
                raise RuntimeError("only a running run can advance")
            if (
                record.current_stage >= len(record.stages)
                or record.stages[record.current_stage] != stage
            ):
                raise RuntimeError("materialization stage progress is stale")
            cursors = dict(record.cursors)
            if cursor is not None:
                cursors[stage] = cursor
            record.cursors = cursors
            record.source_items += source_items
            record.source_bytes += source_bytes
            record.output_rows += output_rows
            if done:
                record.current_stage += 1
            return _run(record)

    def begin_catchup(
        self,
        run_id: UUID,
        *,
        target_snapshot: int,
    ) -> MaterializationRun:
        with session_scope() as session:
            record = _required(session, run_id)
            if target_snapshot <= record.catchup_snapshot:
                raise ValueError("catch-up target must advance the snapshot")
            if record.catchup_target_snapshot is not None:
                if record.catchup_target_snapshot != target_snapshot:
                    raise RuntimeError("catch-up target is already fixed")
                return _run(record)
            record.catchup_target_snapshot = target_snapshot
            record.catchup_stage = 0
            record.catchup_cursors = {
                stage: None for stage in record.stages
            }
            return _run(record)

    def advance_catchup(
        self,
        run_id: UUID,
        *,
        stage: ProjectionName,
        cursor: str | None,
        done: bool,
        source_items: int,
        source_bytes: int,
        output_rows: int,
    ) -> MaterializationRun:
        with session_scope() as session:
            record = _required(session, run_id)
            if (
                record.catchup_target_snapshot is None
                or record.catchup_stage >= len(record.stages)
                or record.stages[record.catchup_stage] != stage
            ):
                raise RuntimeError("materialization catch-up progress is stale")
            cursors = dict(record.catchup_cursors)
            if cursor is not None:
                cursors[stage] = cursor
            record.catchup_cursors = cursors
            record.source_items += source_items
            record.source_bytes += source_bytes
            record.output_rows += output_rows
            if done:
                record.catchup_stage += 1
            if record.catchup_stage == len(record.stages):
                record.catchup_snapshot = record.catchup_target_snapshot
                record.catchup_target_snapshot = None
                record.catchup_stage = 0
                record.catchup_cursors = {}
            return _run(record)

    def complete(self, run_id: UUID) -> MaterializationRun:
        with session_scope() as session:
            record = _required(session, run_id)
            if record.current_stage != len(record.stages):
                raise RuntimeError("cannot complete before every stage is done")
            record.status = "completed"
            record.completed_at = datetime.now(timezone.utc)
            return _run(record)

    def fail(self, run_id: UUID, error: BaseException) -> MaterializationRun:
        with session_scope() as session:
            record = _required(session, run_id)
            record.status = "failed"
            record.completed_at = datetime.now(timezone.utc)
            record.error = f"{type(error).__name__}: {error}"[:4000]
            return _run(record)


class AsyncMaterializationRunStore:
    def __init__(self, store: MaterializationRunStore | None = None) -> None:
        self._store = store or MaterializationRunStore()

    async def create(self, **kwargs) -> MaterializationRun:
        return await asyncio.to_thread(self._store.create, **kwargs)

    async def get(self, run_id: UUID) -> MaterializationRun | None:
        return await asyncio.to_thread(self._store.get, run_id)

    async def list(self, *, limit: int = 100) -> list[MaterializationRun]:
        return await asyncio.to_thread(self._store.list, limit=limit)

    async def start(self, run_id: UUID) -> MaterializationRun:
        return await asyncio.to_thread(self._store.start, run_id)

    async def set_destinations(self, run_id: UUID, destinations):
        return await asyncio.to_thread(
            self._store.set_destinations, run_id, destinations
        )

    async def advance(self, run_id: UUID, **kwargs) -> MaterializationRun:
        return await asyncio.to_thread(self._store.advance, run_id, **kwargs)

    async def begin_catchup(self, run_id: UUID, **kwargs):
        return await asyncio.to_thread(
            self._store.begin_catchup, run_id, **kwargs
        )

    async def advance_catchup(self, run_id: UUID, **kwargs):
        return await asyncio.to_thread(
            self._store.advance_catchup, run_id, **kwargs
        )

    async def complete(self, run_id: UUID) -> MaterializationRun:
        return await asyncio.to_thread(self._store.complete, run_id)

    async def fail(
        self, run_id: UUID, error: BaseException
    ) -> MaterializationRun:
        return await asyncio.to_thread(self._store.fail, run_id, error)


def _required(session, run_id: UUID) -> MaterializationRunRecord:
    record = session.get(MaterializationRunRecord, run_id)
    if record is None:
        raise KeyError(run_id)
    return record


def _run(record: MaterializationRunRecord) -> MaterializationRun:
    return MaterializationRun(
        id=record.id,
        mode=record.mode,
        status=record.status,
        requested_stages=tuple(record.requested_stages),
        stages=tuple(record.stages),
        projector_versions=dict(record.projector_versions),
        source_snapshot=record.source_snapshot,
        catchup_snapshot=record.catchup_snapshot,
        catchup_target_snapshot=record.catchup_target_snapshot,
        catchup_stage=record.catchup_stage,
        catchup_cursors=dict(record.catchup_cursors),
        current_stage=record.current_stage,
        cursors=dict(record.cursors),
        destinations=dict(record.destinations),
        item_budget=record.item_budget,
        byte_budget=record.byte_budget,
        source_items=record.source_items,
        source_bytes=record.source_bytes,
        output_rows=record.output_rows,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        error=record.error,
    )
