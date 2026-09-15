"""Small transactional build planner and fenced progress; no corpus mirror."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4, uuid5
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker
from periplus.materialization.rebuilds.models import (
    BuildRecord,
    RangeRecord,
    BatchRecord,
    PublicationRecord,
)
from periplus.materialization.recipe import recipe_digest
from periplus.platform.postgres.session import SessionLocal
from periplus.platform.execution import DRAIN_SECONDS

BOOTSTRAP_ID = UUID("00000000-0000-0000-0000-000000000001")
RUNNING = ("preparing", "building", "verifying", "ready", "serving", "previous")


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class RebuildConflict(ValueError):
    pass


class BuildControl:
    def __init__(self, sessions: sessionmaker[Session] = SessionLocal) -> None:
        self.sessions = sessions

    def bootstrap(self, manifest_key: str | None = None) -> None:
        with self.sessions.begin() as s:
            if publication := s.get(PublicationRecord, "public_v1"):
                bootstrap = s.get(BuildRecord, BOOTSTRAP_ID)
                if manifest_key and (
                    publication.build_id != BOOTSTRAP_ID
                    or bootstrap.manifest_key != manifest_key
                ):
                    raise RebuildConflict(
                        "Restore bootstrap requires empty control state or an exact bootstrap retry"
                    )
                return
            now = datetime.now(UTC)
            s.add(
                BuildRecord(
                    id=BOOTSTRAP_ID,
                    phase="preparing",
                    recipe=recipe_digest(),
                    manifest_key=manifest_key,
                    material_database="material",
                    query_database="public_v1",
                    created_at=now,
                    updated_at=now,
                )
            )
            s.flush()
            s.add(PublicationRecord(api_version="public_v1", build_id=BOOTSTRAP_ID))

    def create(self, page_size: int, manifest_key: str | None = None) -> UUID:
        if not 1 <= page_size <= 128:
            raise ValueError("Page size must be 1–128")
        with self.sessions.begin() as s:
            s.scalar(select(PublicationRecord).with_for_update())
            if s.scalar(
                select(BuildRecord.id)
                .where(
                    BuildRecord.phase.in_(
                        ("preparing", "building", "verifying", "ready", "cancelling", "draining")
                    )
                )
                .limit(1)
            ):
                raise RebuildConflict("Wait for the current rebuild or target reclamation to finish")
            identity, now = uuid4(), datetime.now(UTC)
            s.add(
                BuildRecord(
                    id=identity,
                    phase="preparing",
                    recipe=recipe_digest(),
                    manifest_key=manifest_key,
                    material_database="material_" + identity.hex,
                    query_database="query_" + identity.hex,
                    page_size=page_size,
                    created_at=now,
                    updated_at=now,
                )
            )
            return identity

    def get(self, identity: UUID) -> BuildRecord:
        with self.sessions() as s:
            value = s.get(BuildRecord, identity)
            if value is None:
                raise KeyError(identity)
            return value

    def builds(self) -> list[BuildRecord]:
        with self.sessions() as s:
            return list(
                s.scalars(
                    select(BuildRecord)
                    .order_by(BuildRecord.protected.desc(), BuildRecord.created_at.desc())
                    .limit(100)
                )
            )

    def ranges(self, identity: UUID) -> list[RangeRecord]:
        with self.sessions() as s:
            return list(
                s.scalars(
                    select(RangeRecord)
                    .where(RangeRecord.build_id == identity)
                    .order_by(RangeRecord.shard)
                )
            )

    def batches(self, identity: UUID) -> list[BatchRecord]:
        with self.sessions() as s:
            return list(
                s.scalars(
                    select(BatchRecord)
                    .where(BatchRecord.build_id == identity)
                    .order_by(BatchRecord.shard, BatchRecord.lane)
                )
            )

    def binding(self):
        with self.sessions() as s:
            publication = s.get(PublicationRecord, "public_v1")
            build = s.get(BuildRecord, publication.build_id)
            return {
                "database": build.query_database,
                "revision": publication.revision,
                "expires_at": datetime.now(UTC) + timedelta(seconds=30),
            }

    def change(self, identity: UUID, revision: int, **values: object) -> bool:
        with self.sessions.begin() as s:
            row = s.get(BuildRecord, identity, with_for_update=True)
            if row.revision != revision:
                return False
            for key, value in values.items():
                setattr(row, key, value)
            row.updated_at = datetime.now(UTC)
            return True

    def initialize(self, build: BuildRecord, key: str, heads: tuple[int, ...]) -> None:
        with self.sessions.begin() as s:
            row = s.get(BuildRecord, build.id, with_for_update=True)
            if row.revision != build.revision or row.phase != "preparing":
                return
            for shard, upper in enumerate(heads):
                s.add(
                    RangeRecord(
                        build_id=row.id, shard=shard, upper=upper, live_cursor=upper
                    )
                )
            row.manifest_key = key
            row.phase = "serving" if row.id == BOOTSTRAP_ID else "building"
            row.updated_at = datetime.now(UTC)

    def plan(self, build: BuildRecord, heads: tuple[int, ...]) -> list[BatchRecord]:
        """At most one historical and one live batch per shard/target."""
        with self.sessions.begin() as s:
            row = s.get(BuildRecord, build.id, with_for_update=True)
            if (
                row.revision != build.revision
                or row.phase not in RUNNING
                or row.phase == "preparing"
            ):
                return []
            existing_ranges = {(batch.shard, batch.lane) for batch in s.scalars(
                select(BatchRecord).where(BatchRecord.build_id == row.id))}
            for source in s.scalars(
                select(RangeRecord).where(RangeRecord.build_id == row.id)
            ):
                for lane, cursor, upper in (
                    ("live", source.live_cursor, heads[source.shard]),
                    ("history", source.cursor, source.upper),
                ):
                    if cursor >= upper or (lane == "history" and row.paused):
                        continue
                    if (source.shard, lane) in existing_ranges:
                        continue
                    end = min(upper, cursor + row.page_size)
                    s.add(
                        BatchRecord(
                            id=uuid5(
                                row.id, f"{source.shard}:{lane}:{cursor + 1}:{end}"
                            ),
                            build_id=row.id,
                            shard=source.shard,
                            lane=lane,
                            start=cursor + 1,
                            end=end,
                            build_revision=row.revision,
                        )
                    )
            s.flush()
            now = datetime.now(UTC)
            pending = list(
                s.scalars(
                    select(BatchRecord).where(
                        BatchRecord.build_id == row.id,
                        BatchRecord.status != "failed",
                        (
                            BatchRecord.published_at.is_(None)
                            | (BatchRecord.published_at < now - timedelta(seconds=30))
                        ),
                    )
                )
            )
            for batch in pending:
                batch.published_at = now
            return pending

    def claim(
        self, identity: UUID, worker_id: str, *, recipe: str
    ) -> tuple[BatchRecord, BuildRecord] | None:
        with self.sessions.begin() as s:
            # Build-before-batch ordering matches cancellation and completion.
            candidate = s.get(BatchRecord, identity)
            if candidate is None:
                return None
            build = s.get(BuildRecord, candidate.build_id, with_for_update=True)
            row = s.get(
                BatchRecord, identity, with_for_update=True, populate_existing=True
            )
            now = datetime.now(UTC)
            if row is None or build.recipe != recipe or build.phase not in RUNNING or row.status == "failed":
                return None
            if row.lease_until and utc(row.lease_until) > now:
                return None
            row.status = "running"
            row.owner = uuid4()
            row.lease_until = now + timedelta(seconds=DRAIN_SECONDS)
            row.build_revision = build.revision
            row.worker_id = worker_id
            row.attempts += 1
            return row, build

    def finish(self, batch: BatchRecord, count: int) -> bool:
        with self.sessions.begin() as s:
            build = s.get(BuildRecord, batch.build_id, with_for_update=True)
            row = s.get(BatchRecord, batch.id, with_for_update=True)
            if (
                row is None
                or row.owner != batch.owner
                or build.revision != batch.build_revision
                or build.phase not in RUNNING
            ):
                return False
            source = s.get(RangeRecord, (build.id, row.shard), with_for_update=True)
            key = "cursor" if row.lane == "history" else "live_cursor"
            if getattr(source, key) != row.start - 1:
                raise RebuildConflict("Non-contiguous material checkpoint")
            setattr(source, key, row.end)
            source.processed += count
            s.delete(row)
            build.updated_at = datetime.now(UTC)
            return True

    def fail(self, batch: BatchRecord, error: str, retryable: bool) -> None:
        with self.sessions.begin() as s:
            row = s.get(BatchRecord, batch.id, with_for_update=True)
            if row is None or row.owner != batch.owner:
                return
            row.status = "queued" if retryable else "failed"
            row.error = error[:1000]
            # Keep ownership through the uncertain-write drain before redelivery.
            if not retryable:
                row.lease_until = None

    def verify(self, build: BuildRecord, heads: tuple[int, ...]) -> None:
        with self.sessions.begin() as s:
            row = s.get(BuildRecord, build.id, with_for_update=True)
            if row.revision != build.revision or row.phase not in RUNNING:
                return
            ranges = list(
                s.scalars(select(RangeRecord).where(RangeRecord.build_id == row.id))
            )
            failures = list(
                s.scalars(
                    select(BatchRecord.error).where(
                        BatchRecord.build_id == row.id, BatchRecord.status == "failed"
                    )
                )
            )
            row.blocker = failures[0] if failures else None
            covered = bool(ranges) and all(
                r.cursor == r.upper and r.live_cursor >= heads[r.shard] for r in ranges
            )
            row.verification_heads = list(heads)
            row.verified_at = datetime.now(UTC) if covered and not failures else None
            if row.phase not in ("serving", "previous"):
                row.phase = "ready" if row.verified_at else "building"
            row.updated_at = datetime.now(UTC)

    def action(self, identity: UUID, action: str) -> None:
        with self.sessions.begin() as s:
            publication = s.scalar(select(PublicationRecord).with_for_update())
            row = s.get(BuildRecord, identity, with_for_update=True)
            if row is None:
                raise KeyError(identity)
            now = datetime.now(UTC)
            if action in ("pause", "resume") and row.phase in (
                "building",
                "verifying",
                "ready",
            ):
                row.paused = action == "pause"
            elif action == "retry" and row.phase in RUNNING:
                for batch in s.scalars(
                    select(BatchRecord).where(
                        BatchRecord.build_id == identity, BatchRecord.status == "failed"
                    )
                ):
                    batch.status = "queued"
                    batch.error = None
                    batch.published_at = None
                    batch.owner = None
                row.blocker = None
                row.verified_at = None
                if row.phase == "ready":
                    row.phase = "building"
            elif (
                action == "cancel"
                and row.phase in ("preparing", "building", "verifying", "ready")
                and publication.build_id != row.id
            ):
                row.phase = "cancelling"
                row.revision += 1
                row.drain_after = now + timedelta(seconds=DRAIN_SECONDS)
            elif (
                action == "activate"
                and row.phase == "ready"
                and row.verified_at
                and not row.blocker
            ):
                if now - utc(row.verified_at) > timedelta(seconds=15):
                    raise RebuildConflict("Wait for fresh worker verification")
                for previous in s.scalars(
                    select(BuildRecord)
                    .where(BuildRecord.phase == "previous")
                    .with_for_update()
                ):
                    previous.phase = "draining"
                    previous.revision += 1
                    previous.drain_after = now + timedelta(seconds=DRAIN_SECONDS)
                old = s.get(BuildRecord, publication.build_id)
                old.phase = "previous"
                publication.build_id = identity
                publication.revision += 1
                row.phase = "serving"
            else:
                raise RebuildConflict("Action is not valid in this build phase")
            row.updated_at = now

    def retire(self, build: BuildRecord) -> None:
        with self.sessions.begin() as s:
            row = s.get(BuildRecord, build.id, with_for_update=True)
            if row.revision != build.revision or row.phase not in (
                "cancelling",
                "draining",
            ):
                return
            s.execute(delete(BatchRecord).where(BatchRecord.build_id == row.id))
            row.phase = "cancelled" if row.phase == "cancelling" else "retired"
            row.protected = False
