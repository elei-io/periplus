"""Transactional operator intent, verified range checkpoints and publication."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4
from sqlalchemy import select
from periplus.materialization.rebuilds.models import BuildRecord, RangeRecord, PublicationRecord
from periplus.platform.postgres.session import SessionLocal

SEMANTIC_VERSION = 'html-links-v1'
BOOTSTRAP_ID = UUID('00000000-0000-0000-0000-000000000001')
RUNNING = ('preparing', 'building', 'verifying', 'ready', 'serving', 'previous')


class RebuildConflict(ValueError):
    pass


class BuildControl:
    def __init__(self, sessions=SessionLocal):
        self.sessions = sessions

    def bootstrap(self) -> None:
        with self.sessions.begin() as session:
            if session.get(PublicationRecord, 'public_v1') is not None:
                return
            now = datetime.now(UTC)
            session.add(BuildRecord(id=BOOTSTRAP_ID, phase='serving', semantic_version=SEMANTIC_VERSION,
                material_database='material', query_database='public_v1', consumer='periplus-materialization-live',
                created_at=now, updated_at=now))
            session.flush()
            session.add(PublicationRecord(api_version='public_v1', build_id=BOOTSTRAP_ID))

    def create(self, page_size: int) -> UUID:
        with self.sessions.begin() as session:
            session.scalar(select(PublicationRecord).with_for_update())
            if session.scalar(select(BuildRecord.id).where(BuildRecord.phase.in_(
                    ('preparing', 'building', 'verifying', 'ready', 'cancelling'))).limit(1)):
                raise RebuildConflict('Finish or cancel the current rebuild first.')
            identity, now = uuid4(), datetime.now(UTC)
            session.add(BuildRecord(id=identity, phase='preparing', semantic_version=SEMANTIC_VERSION,
                material_database='material_' + identity.hex, query_database='query_' + identity.hex,
                consumer='material-' + identity.hex, created_at=now, updated_at=now, page_size=page_size))
            return identity

    def fence_workers(self) -> None:
        # A new NATS owner invalidates checkpoints from any previous owner whose
        # remote writes are still draining. Remote output stays idempotent.
        with self.sessions.begin() as session:
            for build in session.scalars(select(BuildRecord).where(BuildRecord.phase.in_(RUNNING)).with_for_update()):
                build.revision += 1

    def builds(self) -> list[BuildRecord]:
        with self.sessions() as session:
            return list(session.scalars(select(BuildRecord).order_by(BuildRecord.created_at.desc()).limit(100)))

    def get(self, identity: UUID) -> BuildRecord:
        with self.sessions() as session:
            value = session.get(BuildRecord, identity)
            if value is None:
                raise KeyError(identity)
            return value

    def ranges(self, identity: UUID) -> list[RangeRecord]:
        with self.sessions() as session:
            return list(session.scalars(select(RangeRecord).where(RangeRecord.build_id == identity).order_by(RangeRecord.month)))

    def binding(self) -> dict:
        with self.sessions() as session:
            publication = session.get(PublicationRecord, 'public_v1')
            build = session.get(BuildRecord, publication.build_id)
            return {'database': build.query_database, 'revision': publication.revision}

    def change(self, identity: UUID, revision: int, **values) -> bool:
        with self.sessions.begin() as session:
            build = session.get(BuildRecord, identity, with_for_update=True)
            if build.revision != revision:
                return False
            for key, value in values.items():
                setattr(build, key, value)
            build.updated_at = datetime.now(UTC)
            return True

    def plan(self, identity: UUID, revision: int, ranges: list[tuple[int, list]]) -> None:
        with self.sessions.begin() as session:
            build = session.get(BuildRecord, identity, with_for_update=True)
            if build.revision != revision or build.phase != 'preparing':
                return
            for month, upper in ranges:
                if session.get(RangeRecord, (identity, month)) is None:
                    session.add(RangeRecord(build_id=identity, month=month, upper=upper))
            build.phase = 'building'

    def checkpoint(self, identity: UUID, revision: int, month: int, cursor: list | None, count: int, done: bool) -> bool:
        with self.sessions.begin() as session:
            build = session.get(BuildRecord, identity, with_for_update=True)
            if build.revision != revision or build.phase != 'building':
                return False
            row = session.get(RangeRecord, (identity, month), with_for_update=True)
            row.cursor, row.processed, row.done, row.blocker = cursor or row.cursor, row.processed + count, done, None
            return True

    def action(self, identity: UUID, action: str) -> None:
        with self.sessions.begin() as session:
            publication = session.scalar(select(PublicationRecord).with_for_update())
            build = session.get(BuildRecord, identity, with_for_update=True)
            if build is None:
                raise KeyError(identity)
            if action in ('pause', 'resume') and build.phase == 'building':
                build.paused = action == 'pause'
            elif action == 'retry' and build.phase in RUNNING:
                build.blocker = None
                if build.phase == 'ready':
                    build.phase = 'verifying'
                for row in session.scalars(select(RangeRecord).where(RangeRecord.build_id == identity)):
                    row.blocker = None
            elif action == 'cancel' and build.phase in ('preparing', 'building', 'verifying', 'ready'):
                build.phase = 'cancelling'
                # Longer than the hard process/write deadline. No candidate data is
                # dropped; protection survives a dead or partitioned worker.
                build.drain_after = datetime.now(UTC) + timedelta(seconds=610)
            elif action == 'activate' and build.phase == 'ready' and not build.blocker:
                if datetime.now(UTC) - build.updated_at.replace(tzinfo=UTC) > timedelta(seconds=15):
                    raise RebuildConflict('Wait for a fresh worker readiness check.')
                if build.live_pending or build.material_floor < (build.barrier or 0) or build.ingestion_floor < (build.barrier or 0):
                    raise RebuildConflict('The candidate has not caught up.')
                for previous in session.scalars(select(BuildRecord).where(BuildRecord.phase == 'previous').with_for_update()):
                    previous.phase = 'draining'
                    previous.revision += 1
                    previous.drain_after = datetime.now(UTC) + timedelta(seconds=610)
                old = session.get(BuildRecord, publication.build_id)
                old.phase = 'previous'
                publication.build_id, publication.revision = identity, publication.revision + 1
                build.phase = 'serving'
            else:
                raise RebuildConflict('Action is not valid in this build phase.')
            build.revision += 1
            build.updated_at = datetime.now(UTC)
