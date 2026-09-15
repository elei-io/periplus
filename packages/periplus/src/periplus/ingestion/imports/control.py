"""Short database transactions around bounded import control state."""
from datetime import UTC, datetime
from uuid import UUID
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker
from periplus.ingestion.imports.models import ImportRecord
from periplus.ingestion.imports.schemas import ImportProgress, ImportSpec, ImportView


class ImportConflict(ValueError):
    pass


class ImportControl:
    def __init__(self, sessions: sessionmaker[Session]):
        self.sessions = sessions

    def create(self, identity: UUID, specification: ImportSpec) -> ImportView:
        with self.sessions.begin() as session:
            record = session.get(ImportRecord, identity)
            if record is not None:
                if record.specification != specification.model_dump(mode="json"):
                    raise ImportConflict("import identity already has different intent")
                return view(record)
            now = datetime.now(UTC)
            record = ImportRecord(id=identity, specification=specification.model_dump(mode="json"),
                progress=ImportProgress().model_dump(mode="json"), status="queued", error=None,
                revision=0, created_at=now, updated_at=now)
            session.add(record)
            session.flush()
            return view(record)

    def get(self, identity: UUID) -> ImportView:
        with self.sessions() as session:
            record = session.get(ImportRecord, identity)
            if record is None:
                raise LookupError("archive import not found")
            return view(record)

    def list(self, *, runnable: bool = False) -> list[ImportView]:
        with self.sessions() as session:
            query = select(ImportRecord)
            if runnable:
                query = query.where(ImportRecord.status.in_(("queued", "running")))
            query = query.order_by(ImportRecord.updated_at.asc() if runnable else ImportRecord.created_at.desc()).limit(50)
            return [view(record) for record in session.scalars(query)]

    def save(self, before: ImportView, *, progress: ImportProgress, status: str,
             error: str | None = None) -> ImportView:
        with self.sessions.begin() as session:
            result = session.execute(update(ImportRecord).where(
                ImportRecord.id == before.id, ImportRecord.revision == before.revision,
            ).values(progress=progress.model_dump(mode="json"), status=status, error=error,
                     revision=before.revision + 1, updated_at=datetime.now(UTC)))
            if result.rowcount != 1:
                raise ImportConflict("import changed while this operation was running")
        return self.get(before.id)

    def action(self, identity: UUID, action: str) -> ImportView:
        before = self.get(identity)
        if action == "retry":
            if before.status != "blocked":
                raise ImportConflict("only blocked imports can be retried")
            status = "queued"
        else:
            if before.status in ("completed", "cancelled"):
                return before
            status = "cancelled"
        return self.save(before, progress=before.progress, status=status)


def view(record: ImportRecord) -> ImportView:
    return ImportView(id=record.id, specification=ImportSpec.model_validate(record.specification),
        progress=ImportProgress.model_validate(record.progress), status=record.status,
        error=record.error, revision=record.revision, created_at=record.created_at, updated_at=record.updated_at)
